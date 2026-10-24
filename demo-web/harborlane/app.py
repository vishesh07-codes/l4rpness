"""Harbor Lane — a small marketplace for outdoor gear.

Storefront + merchant portal + public API.
Run: python app.py  ->  http://127.0.0.1:8802
"""

import base64
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import time
import urllib.request
import uuid
from datetime import datetime, timezone

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "instance", "harborlane.db")
UPLOAD_DIR = os.path.join(BASE, "uploads")

app = Flask(__name__)
app.secret_key = os.environ.get("HL_SECRET", "harborlane::0c4e6a8b1d3f5a7c")
app.config.update(SESSION_COOKIE_NAME="hl_session", SESSION_COOKIE_HTTPONLY=False,
                  SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=4 * 1024 * 1024)
os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=20)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  credit_cents INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  tagline TEXT DEFAULT '',
  api_key TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL,
  sku TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  category TEXT NOT NULL,
  description TEXT NOT NULL,
  price_cents INTEGER NOT NULL,
  stock INTEGER NOT NULL DEFAULT 0,
  image TEXT DEFAULT '',
  active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  rating INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  ref TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'paid',
  email TEXT NOT NULL,
  ship_name TEXT NOT NULL,
  address TEXT NOT NULL,
  subtotal_cents INTEGER NOT NULL,
  discount_cents INTEGER NOT NULL DEFAULT 0,
  gift_applied_cents INTEGER NOT NULL DEFAULT 0,
  total_cents INTEGER NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  product_id INTEGER,
  name TEXT NOT NULL,
  unit_price_cents INTEGER NOT NULL,
  qty INTEGER NOT NULL,
  line_total_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gift_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  initial_cents INTEGER NOT NULL,
  balance_cents INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gift_txns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gift_card_id INTEGER NOT NULL,
  order_id INTEGER,
  amount_cents INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS returns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_item_id INTEGER NOT NULL,
  order_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  status TEXT DEFAULT 'approved',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  status INTEGER,
  created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def order_ref(order_id):
    """Human-friendly reference shown on invoices and emails."""
    return "HL-%05d-%03d" % (order_id, (order_id * 37 + 11) % 1000)


def money(cents):
    return "$%s" % ("{:.2f}".format((cents or 0) / 100.0))


app.jinja_env.filters["money"] = money


def slugify(v):
    v = re.sub(r"[^a-zA-Z0-9]+", "-", v.lower()).strip("-")
    return v or uuid.uuid4().hex[:8]


PRODUCTS = [
    ("HL-TNT-2P", "Ridgeline 2P Tent", "shelter", "A two-person, three-season tent that pitches in four minutes flat. 2.1 kg packed, 15 denier fly, taped seams.", 32900, 18),
    ("HL-PCK-40", "Drift 40L Pack", "packs", "Haul bag meets daypack. Roll-top closure, removable frame sheet, burly 420D ripstop.", 15900, 34),
    ("HL-BAG-15", "Sundown 15° Quilt", "sleep", "850 fill power, box-baffled footbox, drafts collared. Packs to the size of a melon.", 24900, 12),
    ("HL-STV-900", "Copperline Stove", "kitchen", "Titanium canister stove with a regulated valve. Simmers without scorching, 96 g.", 8900, 51),
    ("HL-JKT-3L", "Northwall 3L Shell", "apparel", "A hard shell that lives in your pack until the weather disagrees with you. Pit zips, helmet hood.", 27900, 21),
    ("HL-MUG-350", "Enamel Camp Mug", "kitchen", "350 ml, chip-resistant enamel, a rim you can actually drink from. Weighs nothing.", 1900, 200),
    ("HL-LMP-1", "Firefly Lantern", "camp", "Warm 2200K light, 40 hours on low, USB-C. The rechargeable that does not feel like a gadget.", 5900, 44),
    ("HL-STK-1", "Basalt Trekking Poles", "packs", "Carbon-aluminium hybrid, flick locks that stay locked, foam grips. Pair, 480 g.", 11900, 27),
]

REVIEW_SEED = [
    ("HL-TNT-2P", 5, "Pitches in the rain", "Tested it in a proper Welsh downpour. Bone dry inside, and the fly tensioner actually works with gloves on."),
    ("HL-TNT-2P", 4, "Great tent, tight for two tall people", "No complaints on build. If you are both over 6'2\", look at the 3P."),
    ("HL-BAG-15", 5, "Warm and light", "Took it to 4°C with a liner and slept fine. The footbox is the reason to buy it."),
    ("HL-STV-900", 4, "Simmers properly", "Rare for a canister stove. The igniter is the weak point, carry a lighter."),
    ("HL-PCK-40", 5, "Carries weight well", "40L but swallows a week of food. Hip belt is legitimately comfortable."),
]


def seed(db):
    if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
        return
    stamp = now()
    mira = db.execute("INSERT INTO users(email,password_hash,name,credit_cents,created_at) VALUES(?,?,?,?,?)",
                      ("mira@harborlane.test", generate_password_hash("mira1234"), "Mira Okafor", 0, stamp)).lastrowid
    demo = db.execute("INSERT INTO users(email,password_hash,name,credit_cents,created_at) VALUES(?,?,?,?,?)",
                      ("demo@harborlane.test", generate_password_hash("demo1234"), "Alex Rivera", 2500, stamp)).lastrowid
    priya = db.execute("INSERT INTO users(email,password_hash,name,credit_cents,created_at) VALUES(?,?,?,?,?)",
                       ("priya@harborlane.test", generate_password_hash("priya1234"), "Priya Raman", 0, stamp)).lastrowid

    store = db.execute("INSERT INTO stores(owner_id,name,slug,tagline,api_key,created_at) VALUES(?,?,?,?,?,?)",
                       (mira, "Harbor Lane Outfitters", "harbor-lane",
                        "Small-batch gear for people who leave the trailhead early.",
                        "hl_live_7c41d9e2b8a640f5c3d1e0a9b7f2c4d8", stamp)).lastrowid

    for sku, name, cat, desc, price, stock in PRODUCTS:
        db.execute("INSERT INTO products(store_id,sku,name,slug,category,description,price_cents,stock,image)"
                   " VALUES(?,?,?,?,?,?,?,?,?)",
                   (store, sku, name, slugify(name), cat, desc, price, stock, ""))

    for sku, rating, title, body in REVIEW_SEED:
        pid = db.execute("SELECT id FROM products WHERE sku=?", (sku,)).fetchone()["id"]
        db.execute("INSERT INTO reviews(product_id,user_id,rating,title,body,created_at) VALUES(?,?,?,?,?,?)",
                   (pid, demo if rating % 2 else priya, rating, title, body, stamp))

    db.execute("INSERT INTO gift_cards(code,initial_cents,balance_cents,created_at) VALUES(?,?,?,?)",
               ("HL-GIFT-50", 5000, 5000, stamp))
    db.execute("INSERT INTO gift_cards(code,initial_cents,balance_cents,created_at) VALUES(?,?,?,?)",
               ("HL-GIFT-200", 20000, 20000, stamp))

    # mira's past order, used by the account page and the returns flow
    subtotal = 32900 + 8900
    oid = db.execute(
        "INSERT INTO orders(user_id,ref,status,email,ship_name,address,subtotal_cents,discount_cents,"
        "gift_applied_cents,total_cents,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (mira, order_ref(1), "shipped", "mira@harborlane.test", "Mira Okafor",
         "14 Quayside Walk, Bristol BS1 4RN", subtotal, 0, 0, subtotal, stamp)).lastrowid
    db.execute("INSERT INTO order_items(order_id,product_id,name,unit_price_cents,qty,line_total_cents)"
               " VALUES(?,?,?,?,?,?)", (oid, 1, "Ridgeline 2P Tent", 32900, 1, 32900))
    db.execute("INSERT INTO order_items(order_id,product_id,name,unit_price_cents,qty,line_total_cents)"
               " VALUES(?,?,?,?,?,?)", (oid, 4, "Copperline Stove", 8900, 1, 8900))
    db.commit()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.commit()
    seed(db)
    db.close()


# ----------------------------------------------------------------------- cart

def read_cart():
    raw = request.cookies.get("hl_cart")
    if not raw:
        return []
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 - malformed cart is an empty cart
        return []


def write_cart(resp, items):
    payload = base64.urlsafe_b64encode(json.dumps(items).encode()).decode()
    resp.set_cookie("hl_cart", payload, max_age=60 * 60 * 24 * 14, samesite="Lax")
    return resp


def cart_detail(items):
    db = get_db()
    rows = []
    for item in items:
        p = db.execute("SELECT * FROM products WHERE sku=? AND active=1", (item.get("sku"),)).fetchone()
        if not p:
            continue
        qty = int(item.get("qty", 0))
        rows.append({"product": p, "qty": qty, "line": p["price_cents"] * qty})
    subtotal = sum(r["line"] for r in rows)
    return rows, subtotal


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def my_store():
    user = current_user()
    if not user:
        return None
    return get_db().execute("SELECT * FROM stores WHERE owner_id=?", (user["id"],)).fetchone()


@app.context_processor
def globals_():
    user = current_user()
    items = read_cart()
    count = sum(int(i.get("qty", 0)) for i in items)
    store = my_store()
    return {"me": user, "cart_count": count, "my_store": store, "year": datetime.now().year}


# --------------------------------------------------------------------- views

@app.get("/")
def index():
    db = get_db()
    products = db.execute("SELECT * FROM products WHERE active=1 ORDER BY id LIMIT 4").fetchall()
    return render_template("index.html", products=products,
                           store=db.execute("SELECT * FROM stores LIMIT 1").fetchone())


@app.get("/shop")
def shop():
    db = get_db()
    cat = request.args.get("category", "")
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM products WHERE active=1"
    args = []
    if cat:
        sql += " AND category=?"
        args.append(cat)
    if q:
        sql += " AND (name LIKE ? OR description LIKE ? OR sku LIKE ?)"
        args.extend(["%" + q + "%"] * 3)
    products = db.execute(sql + " ORDER BY id", args).fetchall()
    cats = db.execute("SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category").fetchall()
    return render_template("shop.html", products=products, cats=cats, cat=cat, q=q)


@app.get("/product/<slug>")
def product(slug):
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE slug=? AND active=1", (slug,)).fetchone()
    if not p:
        abort(404)
    reviews = db.execute(
        "SELECT r.*, u.name FROM reviews r JOIN users u ON u.id=r.user_id"
        " WHERE r.product_id=? ORDER BY r.created_at DESC", (p["id"],)).fetchall()
    rating = db.execute("SELECT AVG(rating) a, COUNT(*) c FROM reviews WHERE product_id=?",
                        (p["id"],)).fetchone()
    return render_template("product.html", p=p, reviews=reviews, rating=rating)


@app.post("/product/<slug>/review")
def add_review(slug):
    if not current_user():
        return redirect(url_for("login", next=request.path))
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE slug=?", (slug,)).fetchone()
    if not p:
        abort(404)
    title = request.form.get("title", "").strip()[:120] or "Review"
    body = clean_review(request.form.get("body", "").strip())
    rating = max(1, min(5, int(request.form.get("rating", 5))))
    db.execute("INSERT INTO reviews(product_id,user_id,rating,title,body,created_at) VALUES(?,?,?,?,?,?)",
               (p["id"], current_user()["id"], rating, title, body, now()))
    db.commit()
    return redirect(url_for("product", slug=slug) + "#reviews")


@app.get("/cart")
def cart():
    items = read_cart()
    rows, subtotal = cart_detail(items)
    return render_template("cart.html", rows=rows, subtotal=subtotal)


@app.post("/cart/add")
def cart_add():
    sku = request.form.get("sku", "")
    try:
        qty = int(request.form.get("qty", 1))
    except ValueError:
        qty = 1
    qty = max(1, min(99, qty))
    items = read_cart()
    for item in items:
        if item.get("sku") == sku:
            item["qty"] = max(1, min(99, int(item.get("qty", 0)) + qty))
            break
    else:
        items.append({"sku": sku, "qty": qty})
    resp = redirect(request.form.get("next") or url_for("cart"))
    return write_cart(resp, items)


@app.post("/cart/update")
def cart_update():
    sku = request.form.get("sku", "")
    action = request.form.get("action", "set")
    items = read_cart()
    for item in list(items):
        if item.get("sku") != sku:
            continue
        if action == "remove":
            items.remove(item)
        else:
            try:
                qty = int(request.form.get("qty", 1))
            except ValueError:
                qty = 1
            item["qty"] = max(1, min(99, qty))
    resp = redirect(url_for("cart"))
    return write_cart(resp, items)


def clean_review(text):
    """Reviews are plain prose; strip the obvious script vectors on the way in."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text or "", flags=re.I | re.S)
    text = re.sub(r"<script[^>]*>", "", text, flags=re.I)
    text = re.sub(r"\son\w+\s*=", " ", text, flags=re.I)
    return text[:4000]


def _apply_gift(db, code, total):
    """Reserve the gift-card balance for this order."""
    gc = db.execute("SELECT * FROM gift_cards WHERE code=?", (code.strip().upper(),)).fetchone()
    if not gc or total <= 0:
        return 0, gc
    applied = min(gc["balance_cents"], total)
    if applied <= 0:
        return 0, gc
    time.sleep(0.15)  # hold the reservation while the payment provider responds
    db.execute("UPDATE gift_cards SET balance_cents = balance_cents - ? WHERE id=?",
               (applied, gc["id"]))
    return applied, gc


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    user = current_user()
    if not user:
        return redirect(url_for("login", next=url_for("checkout")))
    items = read_cart()
    rows, subtotal = cart_detail(items)
    if not rows:
        return redirect(url_for("cart"))
    error = None
    if request.method == "POST":
        db = get_db()
        email = request.form.get("email", user["email"]).strip()
        ship_name = request.form.get("ship_name", user["name"]).strip()
        address = request.form.get("address", "").strip()
        coupon_code = request.form.get("coupon", "").strip().upper()
        gift_code = request.form.get("gift_card", "").strip().upper()
        if not address:
            error = "A shipping address is required."
        else:
            discount = 0
            if coupon_code == "WELCOME10" and subtotal > 0:
                discount = int(round(subtotal * 0.10))
            total_after_discount = subtotal - discount
            applied, gc = _apply_gift(db, gift_code, total_after_discount)
            total = total_after_discount - applied
            if total < 0:
                # overpayment comes back as store credit
                db.execute("UPDATE users SET credit_cents = credit_cents + ? WHERE id=?",
                           (-total, user["id"]))
            stamp = now()
            oid = db.execute(
                "INSERT INTO orders(user_id,ref,status,email,ship_name,address,subtotal_cents,"
                "discount_cents,gift_applied_cents,total_cents,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (user["id"], "tmp-" + uuid.uuid4().hex[:10], "paid", email, ship_name, address,
                 subtotal, discount, applied, total, stamp)).lastrowid
            ref = order_ref(oid)
            db.execute("UPDATE orders SET ref=? WHERE id=?", (ref, oid))
            for r in rows:
                db.execute("INSERT INTO order_items(order_id,product_id,name,unit_price_cents,qty,"
                           "line_total_cents) VALUES(?,?,?,?,?,?)",
                           (oid, r["product"]["id"], r["product"]["name"],
                            r["product"]["price_cents"], r["qty"], r["line"]))
            if applied and gc:
                db.execute("INSERT INTO gift_txns(gift_card_id,order_id,amount_cents,created_at)"
                           " VALUES(?,?,?,?)", (gc["id"], oid, -applied, stamp))
            db.commit()
            resp = redirect(url_for("order", ref=ref))
            return write_cart(resp, [])
    coupon = "WELCOME10" if subtotal > 0 else ""
    return render_template("checkout.html", rows=rows, subtotal=subtotal,
                           user=user, error=error, coupon_hint=coupon)


@app.get("/order/<ref>")
def order(ref):
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE ref=?", (ref,)).fetchone()
    if not o:
        abort(404)
    items = db.execute("SELECT * FROM order_items WHERE order_id=?", (o["id"],)).fetchall()
    return render_template("order.html", o=o, items=items)


@app.get("/gift/<code>")
def gift(code):
    gc = get_db().execute("SELECT * FROM gift_cards WHERE code=?", (code.upper(),)).fetchone()
    return render_template("gift.html", gc=gc, code=code.upper())


# ----------------------------------------------------------------------- auth

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = user["id"]
            nxt = request.form.get("next") or request.args.get("next")
            return redirect(nxt if nxt and nxt.startswith("/") else url_for("account"))
        error = "Those credentials did not match an account."
    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        pw = request.form.get("password", "")
        if not email or not name or len(pw) < 8:
            error = "Name, email and a password of at least 8 characters are required."
        elif get_db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            error = "That email is already registered."
        else:
            uid = get_db().execute(
                "INSERT INTO users(email,password_hash,name,credit_cents,created_at) VALUES(?,?,?,?,?)",
                (email, generate_password_hash(pw), name, 0, now())).lastrowid
            get_db().commit()
            session.clear()
            session["uid"] = uid
            return redirect(url_for("account"))
    return render_template("register.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.get("/account")
def account():
    user = current_user()
    if not user:
        return redirect(url_for("login", next="/account"))
    orders = get_db().execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC",
                              (user["id"],)).fetchall()
    return render_template("account.html", orders=orders)


# ------------------------------------------------------------------- merchant

@app.route("/merchant/login", methods=["GET", "POST"])
def merchant_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = user["id"]
            if my_store():
                return redirect(url_for("merchant_dashboard"))
            return redirect(url_for("merchant_apply"))
        return render_template("merchant_login.html", error="Those credentials did not match.")
    return render_template("merchant_login.html", error=None)


@app.route("/merchant/apply", methods=["GET", "POST"])
def merchant_apply():
    user = current_user()
    if not user:
        return redirect(url_for("merchant_login"))
    if my_store():
        return redirect(url_for("merchant_dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:80] or "New Store"
        slug = slugify(name)
        base = slug
        i = 2
        while get_db().execute("SELECT 1 FROM stores WHERE slug=?", (slug,)).fetchone():
            slug = "%s-%d" % (base, i)
            i += 1
        key = "hl_live_" + uuid.uuid4().hex + uuid.uuid4().hex[:8]
        get_db().execute("INSERT INTO stores(owner_id,name,slug,tagline,api_key,created_at)"
                         " VALUES(?,?,?,?,?,?)",
                         (user["id"], name, slug, request.form.get("tagline", "")[:140], key, now()))
        get_db().commit()
        return redirect(url_for("merchant_dashboard"))
    return render_template("merchant_apply.html")


@app.get("/merchant/")
def merchant_dashboard():
    store = my_store()
    if not store:
        return redirect(url_for("merchant_login"))
    db = get_db()
    stats = {
        "revenue": db.execute("SELECT COALESCE(SUM(total_cents),0) v FROM orders").fetchone()["v"],
        "orders": db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"],
        "reviews": db.execute("SELECT COUNT(*) c FROM reviews r JOIN products p ON p.id=r.product_id"
                              " WHERE p.store_id=?", (store["id"],)).fetchone()["c"],
        "products": db.execute("SELECT COUNT(*) c FROM products WHERE store_id=?", (store["id"],)).fetchone()["c"],
    }
    recent = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 6").fetchall()
    return render_template("merchant_dashboard.html", store=store, stats=stats, recent=recent)


@app.get("/merchant/orders")
def merchant_orders():
    store = my_store()
    if not store:
        return redirect(url_for("merchant_login"))
    orders = get_db().execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    return render_template("merchant_orders.html", store=store, orders=orders)


@app.get("/merchant/reviews")
def merchant_reviews():
    store = my_store()
    if not store:
        return redirect(url_for("merchant_login"))
    reviews = get_db().execute(
        "SELECT r.*, u.name, p.name pname, p.slug pslug FROM reviews r"
        " JOIN users u ON u.id=r.user_id JOIN products p ON p.id=r.product_id"
        " WHERE p.store_id=? ORDER BY r.created_at DESC", (store["id"],)).fetchall()
    return render_template("merchant_reviews.html", store=store, reviews=reviews)


@app.get("/merchant/products")
def merchant_products():
    store = my_store()
    if not store:
        return redirect(url_for("merchant_login"))
    products = get_db().execute("SELECT * FROM products WHERE store_id=? ORDER BY id",
                                (store["id"],)).fetchall()
    return render_template("merchant_products.html", store=store, products=products)


ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


@app.post("/merchant/products")
def merchant_product_new():
    store = my_store()
    if not store:
        return redirect(url_for("merchant_login"))
    name = request.form.get("name", "").strip()[:100]
    if not name:
        return redirect(url_for("merchant_products"))
    slug = slugify(name)
    if get_db().execute("SELECT 1 FROM products WHERE slug=?", (slug,)).fetchone():
        slug = slug + "-" + uuid.uuid4().hex[:4]
    image_name = ""
    upload = request.files.get("image")
    if upload and upload.filename:
        ext = os.path.splitext(upload.filename)[1].lower()
        if ext in ALLOWED_IMAGE_EXT:
            image_name = uuid.uuid4().hex + ext
            upload.save(os.path.join(UPLOAD_DIR, image_name))
    sku = request.form.get("sku", "").strip().upper() or "HL-" + uuid.uuid4().hex[:6].upper()
    try:
        price = int(round(float(request.form.get("price", "0")) * 100))
        stock = int(request.form.get("stock", "0"))
    except ValueError:
        price, stock = 0, 0
    get_db().execute(
        "INSERT INTO products(store_id,sku,name,slug,category,description,price_cents,stock,image)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (store["id"], sku, name, slug, request.form.get("category", "gear"),
         request.form.get("description", ""), price, stock, image_name))
    get_db().commit()
    return redirect(url_for("merchant_products"))


@app.get("/uploads/<path:name>")
def uploaded_file(name):
    return send_from_directory(UPLOAD_DIR, name)


@app.get("/merchant/settings")
def merchant_settings():
    store = my_store()
    if not store:
        return redirect(url_for("merchant_login"))
    attempts = get_db().execute("SELECT * FROM webhook_attempts WHERE store_id=? ORDER BY id DESC LIMIT 8",
                                (store["id"],)).fetchall()
    return render_template("merchant_settings.html", store=store, attempts=attempts, result=None)


@app.get("/developers")
def developers():
    return render_template("developers.html")


# ------------------------------------------------------------------------ API

def key_store():
    key = request.args.get("key") or request.headers.get("X-API-Key") or ""
    row = get_db().execute("SELECT * FROM stores WHERE api_key=?", (key,)).fetchone()
    return row


@app.post("/api/v1/cart/items")
def api_cart_add():
    data = request.get_json(silent=True) or {}
    sku = data.get("sku")
    if not sku or not get_db().execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
        return jsonify(error="unknown sku"), 404
    try:
        qty = int(data.get("qty", 1))
    except (TypeError, ValueError):
        return jsonify(error="qty must be a number"), 400
    items = read_cart()
    for item in items:
        if item.get("sku") == sku:
            item["qty"] = int(item.get("qty", 0)) + qty
            break
    else:
        items.append({"sku": sku, "qty": qty})
    resp = jsonify(ok=True, cart=items)
    return write_cart(resp, items)


@app.get("/api/v1/products")
def api_products():
    rows = get_db().execute("SELECT sku, name, slug, category, price_cents, stock FROM products"
                            " WHERE active=1 ORDER BY id").fetchall()
    return jsonify(products=[dict(r) for r in rows])


@app.get("/api/v1/customers/export")
def api_export_customers():
    store = key_store()
    if not store:
        return jsonify(error="invalid api key"), 401
    rows = get_db().execute("SELECT * FROM orders ORDER BY id").fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ref", "created_at", "name", "email", "address", "total_cents", "status"])
    for o in rows:
        writer.writerow([o["ref"], o["created_at"], o["ship_name"], o["email"],
                         o["address"], o["total_cents"], o["status"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=customers.csv"})


@app.post("/api/v1/returns")
def api_returns():
    user = current_user()
    if not user:
        return jsonify(error="authentication required"), 401
    data = request.get_json(silent=True) or {}
    try:
        item_id = int(data.get("order_item_id"))
    except (TypeError, ValueError):
        return jsonify(error="order_item_id is required"), 400
    db = get_db()
    item = db.execute("SELECT * FROM order_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return jsonify(error="order item not found"), 404
    order = db.execute("SELECT * FROM orders WHERE id=?", (item["order_id"],)).fetchone()
    reason = str(data.get("reason", "changed my mind"))[:300]
    rid = db.execute(
        "INSERT INTO returns(order_item_id,order_id,user_id,reason,amount_cents,status,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (item_id, order["id"], user["id"], reason, item["line_total_cents"], "approved", now())).lastrowid
    db.execute("UPDATE users SET credit_cents = credit_cents + ? WHERE id=?",
               (item["line_total_cents"], user["id"]))
    db.commit()
    return jsonify(ok=True, return_id=rid, refund_cents=item["line_total_cents"],
                   credited_to=user["email"])


@app.post("/api/v1/webhooks/test")
def api_webhook_test():
    store = key_store()
    if not store:
        return jsonify(error="invalid api key"), 401
    data = request.get_json(silent=True) or {}
    url = data.get("url", "")
    if not url.startswith(("http://", "https://")):
        return jsonify(error="url must be http(s)"), 400
    status, body = None, ""
    try:
        req = urllib.request.Request(url, data=json.dumps({"event": "order.created", "store": store["slug"]}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "HarborLane-Webhooks/1.4"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            status = resp.status
            body = resp.read(4000).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        status = 0
        body = str(exc)
    get_db().execute("INSERT INTO webhook_attempts(store_id,url,status,created_at) VALUES(?,?,?,?)",
                     (store["id"], url, status, now()))
    get_db().commit()
    return jsonify(status=status, response=body[:4000])


@app.get("/healthz")
def healthz():
    return jsonify(status="ok", app="harborlane")


@app.errorhandler(404)
def nf(_e):
    return render_template("error.html", code=404, message="That page is not on the shelf."), 404


init_db()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8802)),
            debug=False, threaded=True)

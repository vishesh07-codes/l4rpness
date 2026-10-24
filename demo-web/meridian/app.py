"""Meridian Support — customer support desk.

Run: python app.py  ->  http://127.0.0.1:8803
"""

import base64
import csv
import hashlib
import io
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "instance", "meridian.db")
UPLOAD_DIR = os.path.join(BASE, "uploads")

app = Flask(__name__)
app.secret_key = os.environ.get("MER_SECRET", "meridian::5b1d7f3a9c2e4d6f")
app.config.update(SESSION_COOKIE_NAME="meridian_session", SESSION_COOKIE_HTTPONLY=False,
                  SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=8 * 1024 * 1024)
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
  role TEXT NOT NULL DEFAULT 'customer',
  team TEXT DEFAULT '',
  avatar TEXT DEFAULT '#4353a8',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ref TEXT UNIQUE NOT NULL,
  subject TEXT NOT NULL,
  requester_id INTEGER NOT NULL,
  assignee_id INTEGER,
  status TEXT NOT NULL DEFAULT 'open',
  priority TEXT NOT NULL DEFAULT 'normal',
  channel TEXT NOT NULL DEFAULT 'email',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sla_due TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  author_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  is_internal INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  message_id INTEGER,
  filename TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  uploaded_by INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id INTEGER,
  actor_label TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL,
  ip TEXT NOT NULL,
  ua TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ticket_ref(tid):
    return "MER-%04d" % (1000 + tid)


def seed(db):
    if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
        return
    stamp = now()
    users = [
        ("sofia@meridian.test", "sofia1234", "Sofia Anders", "admin", "Operations", "#3d4a8f"),
        ("dev@meridian.test", "dev1234", "Dev Patel", "agent", "Tier 2", "#2f6f5e"),
        ("lena@meridian.test", "lena1234", "Lena Fischer", "agent", "Tier 1", "#8a5a3a"),
        ("demo@meridian.test", "demo1234", "Alex Rivera", "customer", "", "#4353a8"),
        ("priya@northwind.test", "priya1234", "Priya Raman", "customer", "", "#7a4a86"),
        ("tom@beacon.test", "tom12345", "Tom Whitfield", "customer", "", "#4a7a86"),
    ]
    ids = {}
    for email, pw, name, role, team, color in users:
        ids[email] = db.execute(
            "INSERT INTO users(email,password_hash,name,role,team,avatar,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (email, generate_password_hash(pw), name, role, team, color, stamp)).lastrowid

    tickets = [
        ("Refund has not arrived", "demo@meridian.test", "dev@meridian.test", "open", "high", "email"),
        ("Cannot add a second user", "demo@meridian.test", "lena@meridian.test", "pending", "normal", "web"),
        ("Invoice address is wrong", "priya@northwind.test", "dev@meridian.test", "open", "normal", "email"),
        ("API returns 401 after key rotation", "tom@beacon.test", "lena@meridian.test", "open", "urgent", "api"),
        ("Export is missing June rows", "priya@northwind.test", None, "solved", "low", "email"),
    ]
    threads = [
        [
            (3, "Hi — I returned the order two weeks ago and the refund still is not on my card. "
                "Order HL-88213. Can you check?", 0),
            (1, "Looking into it now. The return was scanned at the warehouse on the 3rd, so the refund "
                "should clear within 3–5 business days.", 0),
            (1, "Stripe refund was issued but the customer's bank shows no record. Escalating to finance.", 1),
        ],
        [
            (3, "I am trying to invite a colleague and the form says 'seat limit reached'. We are on the "
                "Team plan with 4 of 5 seats used.", 0),
            (2, "Checking the seat count now — one pending invitation may have reserved a seat.", 0),
        ],
        [
            (4, "The invoice for May shows our old address. Please reissue it to 18 Harbour Street.", 0),
            (1, "Reissued and sent to the billing contact. The PDF in the portal is updated.", 0),
        ],
        [
            (5, "We rotated our API key this morning and now every call returns 401. The old key still works "
                "in staging. What is the propagation delay?", 0),
            (2, "Keys propagate in under a minute. Can you send the key prefix so I can check the environment?", 0),
            (5, "It starts with hl_live_2b. Also noticed our webhook secret changed at the same time.", 0),
            (2, "Internal: staging uses a separate key vault; do not rotate it with production. "
                "Prod key prefix hl_live_2b9f confirmed active.", 1),
        ],
        [
            (4, "The June export only has 12 rows, we expect about 40.", 0),
            (1, "The export filters by activity date, not invoice date. Regenerated with the correct range.", 0),
        ],
    ]
    for i, (subject, requester, assignee, status, priority, channel) in enumerate(tickets):
        created = (datetime.now(timezone.utc) - timedelta(days=6 - i)).strftime("%Y-%m-%d %H:%M:%S")
        tid = db.execute(
            "INSERT INTO tickets(ref,subject,requester_id,assignee_id,status,priority,channel,"
            "created_at,updated_at,sla_due) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ticket_ref(i + 1), subject, ids[requester], ids[assignee] if assignee else None,
             status, priority, channel, created, created,
             (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"))).lastrowid
        for author, body, internal in threads[i]:
            db.execute("INSERT INTO messages(ticket_id,author_id,body,is_internal,created_at)"
                       " VALUES(?,?,?,?,?)",
                       (tid, ids[users[author][0]], body, internal, created))

    db.execute("INSERT INTO audit(actor_id,actor_label,action,detail,ip,ua,created_at)"
               " VALUES(?,?,?,?,?,?,?)",
               (ids["sofia@meridian.test"], "Sofia Anders", "settings.update",
                "Set business hours to 09:00–18:00 Europe/London", "10.0.2.14",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15", stamp))
    db.commit()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.commit()
    seed(db)
    db.close()


# ------------------------------------------------------------------------ auth

def current_user():
    uid = session.get("uid")
    if not uid:
        uid = user_from_remember()
        if uid:
            session["uid"] = uid
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def remember_value(uid):
    digest = hashlib.md5(str(uid).encode()).hexdigest()
    return base64.urlsafe_b64encode(("%d:%s" % (uid, digest)).encode()).decode()


def user_from_remember():
    raw = request.cookies.get("meridian_remember")
    if not raw:
        return None
    try:
        decoded = base64.urlsafe_b64decode(raw.encode()).decode()
        uid_s, digest = decoded.split(":", 1)
        uid = int(uid_s)
    except Exception:  # noqa: BLE001
        return None
    if hashlib.md5(str(uid).encode()).hexdigest() != digest:
        return None
    return uid


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def staff_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user = current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if user["role"] not in ("agent", "admin"):
            abort(403)
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user = current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if user["role"] != "admin":
            abort(403)
        return fn(*a, **kw)
    return wrapper


def log_audit(action, detail, actor=None):
    user = actor if actor is not None else current_user()
    db = get_db()
    db.execute(
        "INSERT INTO audit(actor_id,actor_label,action,detail,ip,ua,created_at) VALUES(?,?,?,?,?,?,?)",
        (user["id"] if user else None, user["name"] if user else "anonymous", action,
         detail[:500], request.remote_addr or "", request.headers.get("User-Agent", "")[:400], now()))
    db.commit()


@app.context_processor
def globals_():
    user = current_user()
    return {"me": user, "year": datetime.now().year,
            "staff": user and user["role"] in ("agent", "admin")}


# ---------------------------------------------------------------------- pages

@app.get("/")
def index():
    if current_user():
        return redirect(url_for("tickets"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        log_audit("auth.login.attempt", "email=%s result=%s" % (email, "ok" if user else "unknown"))
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["uid"] = user["id"]
            resp = redirect(request.form.get("next") or url_for("tickets"))
            if request.form.get("remember"):
                resp.set_cookie("meridian_remember", remember_value(user["id"]),
                                max_age=60 * 60 * 24 * 30, httponly=False, samesite="Lax")
            return resp
        error = "That email and password did not match."
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    resp = redirect(url_for("login"))
    resp.delete_cookie("meridian_remember")
    return resp


def visible_ticket(ticket_id):
    user = current_user()
    t = get_db().execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not t:
        abort(404)
    if user["role"] == "customer" and t["requester_id"] != user["id"]:
        abort(403)
    return t


@app.get("/tickets")
@login_required
def tickets():
    user = current_user()
    db = get_db()
    clauses, args = [], []
    if user["role"] == "customer":
        clauses.append("t.requester_id=?")
        args.append(user["id"])
    f = request.args.get("filter", "").strip()
    for token in f.split():
        key, _, val = token.partition(":")
        if not val:
            continue
        if key == "status" and val in ("open", "pending", "solved"):
            clauses.append("t.status=?")
            args.append(val)
        elif key == "priority" and val in ("low", "normal", "high", "urgent"):
            clauses.append("t.priority=?")
            args.append(val)
        elif key == "assignee":
            if val == "me":
                clauses.append("t.assignee_id=?")
                args.append(user["id"])
            elif val == "none":
                clauses.append("t.assignee_id IS NULL")
            else:
                clauses.append("t.assignee_id=(SELECT id FROM users WHERE email=?)")
                args.append(val)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.execute(
        "SELECT t.*, r.name requester, a.name assignee FROM tickets t"
        " JOIN users r ON r.id=t.requester_id LEFT JOIN users a ON a.id=t.assignee_id"
        + where + " ORDER BY t.updated_at DESC", args).fetchall()
    counts = {
        "open": db.execute("SELECT COUNT(*) c FROM tickets WHERE status='open'").fetchone()["c"],
        "pending": db.execute("SELECT COUNT(*) c FROM tickets WHERE status='pending'").fetchone()["c"],
        "urgent": db.execute("SELECT COUNT(*) c FROM tickets WHERE priority='urgent'").fetchone()["c"],
    }
    return render_template("tickets.html", rows=rows, f=f, counts=counts)


@app.get("/tickets/new")
@login_required
def ticket_new():
    return render_template("ticket_new.html")


@app.post("/tickets/new")
@login_required
def ticket_create():
    user = current_user()
    subject = request.form.get("subject", "").strip()[:160]
    body = request.form.get("body", "").strip()
    priority = request.form.get("priority", "normal")
    if priority not in ("low", "normal", "high", "urgent"):
        priority = "normal"
    if not subject or not body:
        return render_template("ticket_new.html", error="A subject and a message are required.")
    db = get_db()
    stamp = now()
    tid = db.execute(
        "INSERT INTO tickets(ref,subject,requester_id,status,priority,channel,created_at,updated_at,sla_due)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        ("tmp-" + uuid.uuid4().hex[:8], subject, user["id"], "open", priority, "web", stamp, stamp,
         (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"))).lastrowid
    db.execute("UPDATE tickets SET ref=? WHERE id=?", (ticket_ref(tid), tid))
    db.execute("INSERT INTO messages(ticket_id,author_id,body,is_internal,created_at) VALUES(?,?,?,?,?)",
               (tid, user["id"], body, 0, stamp))
    db.commit()
    log_audit("ticket.create", "ref=%s" % ticket_ref(tid))
    return redirect(url_for("ticket_view", ticket_id=tid))


@app.get("/tickets/<int:ticket_id>")
@login_required
def ticket_view(ticket_id):
    t = visible_ticket(ticket_id)
    db = get_db()
    user = current_user()
    messages = db.execute(
        "SELECT m.*, u.name, u.role, u.avatar FROM messages m JOIN users u ON u.id=m.author_id"
        " WHERE m.ticket_id=? ORDER BY m.created_at", (ticket_id,)).fetchall()
    if user["role"] == "customer":
        messages = [m for m in messages if not m["is_internal"]]
    attachments = db.execute("SELECT * FROM attachments WHERE ticket_id=?", (ticket_id,)).fetchall()
    requester = db.execute("SELECT * FROM users WHERE id=?", (t["requester_id"],)).fetchone()
    assignee = db.execute("SELECT * FROM users WHERE id=?", (t["assignee_id"],)).fetchone() if t["assignee_id"] else None
    return render_template("ticket.html", t=t, messages=messages, attachments=attachments,
                           requester=requester, assignee=assignee)


@app.post("/tickets/<int:ticket_id>/reply")
@login_required
def ticket_reply(ticket_id):
    t = visible_ticket(ticket_id)
    body = request.form.get("body", "").strip()
    if body:
        db = get_db()
        db.execute("INSERT INTO messages(ticket_id,author_id,body,is_internal,created_at)"
                   " VALUES(?,?,?,?,?)", (ticket_id, current_user()["id"], body, 0, now()))
        db.execute("UPDATE tickets SET updated_at=?, status='open' WHERE id=?", (now(), ticket_id))
        db.commit()
        log_audit("ticket.reply", "ref=%s" % t["ref"])
    return redirect(url_for("ticket_view", ticket_id=ticket_id))


@app.post("/tickets/<int:ticket_id>/note")
@staff_required
def ticket_note(ticket_id):
    t = visible_ticket(ticket_id)
    body = request.form.get("body", "").strip()
    if body:
        db = get_db()
        db.execute("INSERT INTO messages(ticket_id,author_id,body,is_internal,created_at)"
                   " VALUES(?,?,?,?,?)", (ticket_id, current_user()["id"], body, 1, now()))
        db.commit()
        log_audit("ticket.internal_note", "ref=%s" % t["ref"])
    return redirect(url_for("ticket_view", ticket_id=ticket_id))


@app.post("/tickets/<int:ticket_id>/attachments")
@login_required
def ticket_upload(ticket_id):
    t = visible_ticket(ticket_id)
    f = request.files.get("file")
    if f and f.filename:
        original = os.path.basename(f.filename)
        stored = uuid.uuid4().hex + os.path.splitext(original)[1].lower()
        f.save(os.path.join(UPLOAD_DIR, stored))
        get_db().execute(
            "INSERT INTO attachments(ticket_id,filename,stored_name,uploaded_by,created_at)"
            " VALUES(?,?,?,?,?)", (ticket_id, original, stored, current_user()["id"], now()))
        get_db().commit()
        log_audit("ticket.attachment", "ref=%s file=%s" % (t["ref"], original))
    return redirect(url_for("ticket_view", ticket_id=ticket_id))


@app.get("/attachments/<int:attachment_id>/<path:filename>")
@login_required
def attachment_download(attachment_id, filename):
    """Serve an upload. The visible filename is kept in the URL for nicer downloads."""
    user = current_user()
    db = get_db()
    att = db.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
    if not att:
        abort(404)
    t = db.execute("SELECT * FROM tickets WHERE id=?", (att["ticket_id"],)).fetchone()
    if user["role"] == "customer" and t["requester_id"] != user["id"]:
        abort(403)
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        abort(404)
    return send_file(path)


# ---------------------------------------------------------------------- admin

@app.get("/admin/")
@admin_required
def admin_dashboard():
    db = get_db()
    stats = {
        "open": db.execute("SELECT COUNT(*) c FROM tickets WHERE status='open'").fetchone()["c"],
        "urgent": db.execute("SELECT COUNT(*) c FROM tickets WHERE priority='urgent'").fetchone()["c"],
        "customers": db.execute("SELECT COUNT(*) c FROM users WHERE role='customer'").fetchone()["c"],
        "agents": db.execute("SELECT COUNT(*) c FROM users WHERE role IN ('agent','admin')").fetchone()["c"],
    }
    by_priority = db.execute("SELECT priority, COUNT(*) c FROM tickets GROUP BY priority").fetchall()
    return render_template("admin_dashboard.html", stats=stats, by_priority=by_priority)


@app.get("/admin/tickets")
@admin_required
def admin_tickets():
    rows = get_db().execute(
        "SELECT t.*, r.name requester, a.name assignee FROM tickets t JOIN users r ON r.id=t.requester_id"
        " LEFT JOIN users a ON a.id=t.assignee_id ORDER BY t.updated_at DESC").fetchall()
    return render_template("admin_tickets.html", rows=rows)


@app.get("/admin/export/tickets.csv")
@login_required
def admin_export():
    """CSV export used by the finance and support leads. Streamed for large mailboxes."""
    db = get_db()
    rows = db.execute(
        "SELECT t.ref, t.subject, t.status, t.priority, t.created_at, r.name requester,"
        " r.email requester_email, a.name assignee FROM tickets t JOIN users r ON r.id=t.requester_id"
        " LEFT JOIN users a ON a.id=t.assignee_id ORDER BY t.id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ref", "subject", "status", "priority", "created_at", "requester", "email", "assignee",
                "internal_notes"])
    for t in rows:
        notes = db.execute("SELECT u.name, m.body FROM messages m JOIN users u ON u.id=m.author_id"
                           " WHERE m.ticket_id=(SELECT id FROM tickets WHERE ref=?) AND m.is_internal=1",
                           (t["ref"],)).fetchall()
        w.writerow([t["ref"], t["subject"], t["status"], t["priority"], t["created_at"],
                    t["requester"], t["requester_email"], t["assignee"] or "",
                    " | ".join("%s: %s" % (n["name"], n["body"].replace("\n", " ")) for n in notes)])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tickets.csv"})


def audit_table(rows):
    """Render the audit rows in one pass — the table is read-only and can get long."""
    cells = []
    for r in rows:
        cells.append(
            "<tr><td class=mono>%s</td><td>%s</td><td><span class=action>%s</span></td>"
            "<td>%s</td><td class=mono>%s</td><td class=ua>%s</td></tr>" % (
                r["created_at"], r["actor_label"], r["action"], r["detail"], r["ip"], r["ua"]))
    return Markup("<table class=audit-table><thead><tr><th>Time</th><th>Actor</th><th>Action</th>"
                  "<th>Detail</th><th>IP</th><th>User agent</th></tr></thead><tbody>%s</tbody></table>"
                  % "".join(cells))


@app.get("/admin/audit")
@admin_required
def admin_audit():
    rows = get_db().execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200").fetchall()
    return render_template("admin_audit.html", table=audit_table(rows), count=len(rows))


@app.get("/admin/users")
@admin_required
def admin_users():
    rows = get_db().execute("SELECT * FROM users ORDER BY id").fetchall()
    return render_template("admin_users.html", rows=rows)


@app.get("/admin/settings")
@admin_required
def admin_settings():
    rows = get_db().execute("SELECT * FROM settings").fetchall()
    return render_template("admin_settings.html", rows=rows)


# ------------------------------------------------------------------------ API

SORTABLE = {"id", "created_at", "updated_at", "priority", "status"}


@app.get("/api/tickets")
@login_required
def api_tickets():
    user = current_user()
    sort = request.args.get("sort", "updated_at.desc")
    field, _, direction = sort.partition(".")
    if field not in SORTABLE:
        return jsonify(error="unknown sort field", allowed=sorted(SORTABLE)), 400
    where, args = "", []
    if user["role"] == "customer":
        where = " WHERE t.requester_id=?"
        args.append(user["id"])
    sql = ("SELECT t.*, r.name requester, a.name assignee FROM tickets t"
           " JOIN users r ON r.id=t.requester_id LEFT JOIN users a ON a.id=t.assignee_id"
           + where + " ORDER BY " + field + " " + direction)
    rows = get_db().execute(sql, args).fetchall()
    return jsonify(tickets=[dict(r) for r in rows])


@app.get("/api/tickets/<int:ticket_id>")
@login_required
def api_ticket(ticket_id):
    t = visible_ticket(ticket_id)
    db = get_db()
    messages = db.execute(
        "SELECT m.id, m.body, m.is_internal, m.created_at, u.name author, u.role author_role,"
        " u.email author_email FROM messages m JOIN users u ON u.id=m.author_id"
        " WHERE m.ticket_id=? ORDER BY m.id", (ticket_id,)).fetchall()
    attachments = db.execute("SELECT id, filename, created_at FROM attachments WHERE ticket_id=?",
                             (ticket_id,)).fetchall()
    return jsonify(ticket=dict(t), messages=[dict(m) for m in messages],
                   attachments=[dict(a) for a in attachments])


@app.get("/healthz")
def healthz():
    return jsonify(status="ok", app="meridian")


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, message="That area is for the support team."), 403


@app.errorhandler(404)
def nf(_e):
    return render_template("error.html", code=404, message="We could not find that ticket."), 404


init_db()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8803)),
            debug=False, threaded=True)

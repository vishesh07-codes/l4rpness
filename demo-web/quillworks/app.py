"""Quillworks — collaborative documentation for small teams.

A demo product. Runs standalone:  python app.py  ->  http://127.0.0.1:8801
"""

import html as html_mod
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import wraps

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "instance", "quillworks.db")

app = Flask(__name__)
app.secret_key = os.environ.get("QW_SECRET", "quillworks::8f3a1d9c7e2b4a6d")
app.config.update(SESSION_COOKIE_NAME="qw_session", SESSION_COOKIE_HTTPONLY=False,
                  SESSION_COOKIE_SAMESITE="Lax")

# --------------------------------------------------------------------------- db

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  handle TEXT NOT NULL,
  title TEXT DEFAULT '',
  avatar TEXT DEFAULT '#b4552d',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspaces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  owner_id INTEGER NOT NULL,
  plan TEXT DEFAULT 'team',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
  workspace_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer',
  joined_at TEXT NOT NULL,
  PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id INTEGER NOT NULL,
  author_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  is_public INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS note_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id INTEGER NOT NULL,
  editor_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  UNIQUE(workspace_id, name)
);
CREATE TABLE IF NOT EXISTS note_tags (
  note_id INTEGER NOT NULL,
  tag_id INTEGER NOT NULL,
  PRIMARY KEY (note_id, tag_id)
);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id INTEGER NOT NULL,
  author_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id INTEGER NOT NULL,
  actor_id INTEGER NOT NULL,
  verb TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def slugify(value):
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return value.strip("-") or "untitled"


SEED_NOTES = [
    ("Atlas Handbook", "atlas", "mira@quillworks.test", [
        ("Welcome to the Atlas handbook",
         "Atlas is the internal name for the customer migration programme.\n\n"
         "## Ground rules\n\n"
         "- Every change lands behind a flag\n"
         "- Support gets a heads-up 48h before a rollout\n"
         "- Rollback plan lives in the runbook, not in someone's head\n\n"
         "Questions go to #atlas-migration.",
         "handbook, onboarding"),
        ("Migration runbook v4",
         "1. Freeze writes on the legacy cluster\n"
         "2. Replay the change stream into `atlas-v2`\n"
         "3. Verify row counts per shard\n"
         "4. Flip the read path, watch error budget for 30m\n"
         "5. Unfreeze writes\n\n"
         "Emergency contact: mira (on-call, rotation B).",
         "runbook, infra"),
        ("Release checklist",
         "Before every release:\n\n"
         "- [ ] changelog entry\n- [ ] feature flags documented\n"
         "- [ ] dashboards updated\n- [ ] support macros refreshed\n\n"
         "The release captain owns the checklist, not the reviewer.",
         "release, process"),
        ("Customer tiers and limits",
         "| Tier | Seats | Storage | Support |\n"
         "|---|---|---|---|\n"
         "| Starter | 5 | 10 GB | email |\n"
         "| Team | 50 | 250 GB | priority |\n"
         "| Scale | 500 | 2 TB | dedicated |\n\n"
         "Limits are enforced softly; the billing team handles exceptions.",
         "pricing, support"),
    ]),
    ("Field Notes", "field-notes", "jonas@quillworks.test", [
        ("Interview notes — Northwind",
         "Spoke with the ops lead for 40 minutes.\n\n"
         "**Pain:** they reconcile shipments by hand every Friday.\n"
         "**Quote:** \"If it saved one afternoon a week, I'd pay double.\"\n\n"
         "Follow-up: send the pricing sheet, loop in mira.",
         "research, interview"),
        ("Weekly sync 12/03",
         "Attendees: mira, jonas, priya.\n\n"
         "Decisions:\n"
         "- Push the audit-log beta to two design partners\n"
         "- Park the mobile app until Q3\n"
         "- Priya owns the onboarding rewrite",
         "meeting"),
        ("Competitor teardown",
         "Three tools in the same lane. All of them nail import, none of them\n"
         "handle version history in a way a non-technical editor understands.\n\n"
         "Opportunity: make history legible. Not more features — fewer scary ones.",
         "research, product"),
    ]),
]


def seed(db):
    if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
        return
    stamp = now()
    users = [
        ("mira@quillworks.test", "mira1234", "Mira Okafor", "mira", "Head of Operations", "#b4552d"),
        ("demo@quillworks.test", "demo1234", "Alex Rivera", "alex", "Technical writer", "#3f6f6a"),
        ("jonas@quillworks.test", "jonas1234", "Jonas Lind", "jonas", "Product researcher", "#4a5b8c"),
        ("priya@quillworks.test", "priya1234", "Priya Raman", "priya", "Support lead", "#8c5a4a"),
    ]
    ids = {}
    for email, pw, name, handle, title, color in users:
        cur = db.execute(
            "INSERT INTO users(email,password_hash,name,handle,title,avatar,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (email, generate_password_hash(pw), name, handle, title, color, stamp))
        ids[email] = cur.lastrowid

    ws_rows = [
        ("Atlas Handbook", "atlas", "mira@quillworks.test", "team"),
        ("Field Notes", "field-notes", "jonas@quillworks.test", "team"),
        ("Mira — drafts", "mira-drafts", "mira@quillworks.test", "personal"),
        ("Sandbox", "sandbox", "demo@quillworks.test", "personal"),
    ]
    ws = {}
    for name, slug, owner, plan in ws_rows:
        cur = db.execute("INSERT INTO workspaces(name,slug,owner_id,plan,created_at) VALUES(?,?,?,?,?)",
                         (name, slug, ids[owner], plan, stamp))
        ws[slug] = cur.lastrowid

    members = [
        ("atlas", "mira@quillworks.test", "owner"), ("atlas", "demo@quillworks.test", "editor"),
        ("atlas", "priya@quillworks.test", "editor"), ("atlas", "jonas@quillworks.test", "viewer"),
        ("field-notes", "jonas@quillworks.test", "owner"), ("field-notes", "demo@quillworks.test", "editor"),
        ("field-notes", "priya@quillworks.test", "viewer"),
        ("mira-drafts", "mira@quillworks.test", "owner"),
        ("sandbox", "demo@quillworks.test", "owner"),
    ]
    for slug, email, role in members:
        db.execute("INSERT INTO memberships(workspace_id,user_id,role,joined_at) VALUES(?,?,?,?)",
                   (ws[slug], ids[email], role, stamp))

    for name, slug, owner, notes in SEED_NOTES:
        for title, body, tagstr in notes:
            cur = db.execute(
                "INSERT INTO notes(workspace_id,author_id,title,body,is_public,created_at,updated_at)"
                " VALUES(?,?,?,?,0,?,?)",
                (ws[slug], ids[owner], title, body, stamp, stamp))
            note_id = cur.lastrowid
            db.execute("INSERT INTO note_versions(note_id,editor_id,title,body,created_at) VALUES(?,?,?,?,?)",
                       (note_id, ids[owner], title, body, stamp))
            for tag in [t.strip() for t in tagstr.split(",") if t.strip()]:
                row = db.execute("SELECT id FROM tags WHERE workspace_id=? AND name=?", (ws[slug], tag)).fetchone()
                tag_id = row["id"] if row else db.execute(
                    "INSERT INTO tags(workspace_id,name) VALUES(?,?)", (ws[slug], tag)).lastrowid
                db.execute("INSERT OR IGNORE INTO note_tags(note_id,tag_id) VALUES(?,?)", (note_id, tag_id))
            db.execute("INSERT INTO activity(workspace_id,actor_id,verb,detail,created_at) VALUES(?,?,?,?,?)",
                       (ws[slug], ids[owner], "created", title, stamp))

    # a private note with a few revisions, owned by mira only
    cur = db.execute(
        "INSERT INTO notes(workspace_id,author_id,title,body,is_public,created_at,updated_at)"
        " VALUES(?,?,?,?,0,?,?)",
        (ws["mira-drafts"], ids["mira@quillworks.test"], "Q3 pricing memo",
         "Do not share outside the leadership group.\n\n"
         "Starter moves to $8/seat. Team stays at $12 but gets the audit log.\n"
         "Enterprise floor is $18k/year, no exceptions below 40 seats.\n\n"
         "Margin target: 78% blended by Q4.", stamp, stamp))
    memo_id = cur.lastrowid
    db.execute("INSERT INTO note_versions(note_id,editor_id,title,body,created_at) VALUES(?,?,?,?,?)",
               (memo_id, ids["mira@quillworks.test"], "Q3 pricing memo (draft)",
                "Initial thinking: Starter $7, Team $12, Enterprise $15k floor.\n"
                "Numbers are not final. Waiting on finance.", stamp))
    db.execute("INSERT INTO note_versions(note_id,editor_id,title,body,created_at) VALUES(?,?,?,?,?)",
               (memo_id, ids["mira@quillworks.test"], "Q3 pricing memo",
                "Do not share outside the leadership group.\n\n"
                "Starter moves to $8/seat. Team stays at $12 but gets the audit log.\n"
                "Enterprise floor is $18k/year, no exceptions below 40 seats.\n\n"
                "Margin target: 78% blended by Q4.", stamp))
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
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def membership(workspace_id, user_id=None):
    user = current_user()
    if not user:
        return None
    uid = user_id if user_id is not None else user["id"]
    return get_db().execute(
        "SELECT * FROM memberships WHERE workspace_id=? AND user_id=?", (workspace_id, uid)).fetchone()


def workspace_by_slug(slug):
    ws = get_db().execute("SELECT * FROM workspaces WHERE slug=?", (slug,)).fetchone()
    if not ws:
        abort(404)
    return ws


def require_workspace(slug):
    ws = workspace_by_slug(slug)
    if not membership(ws["id"]):
        abort(403)
    return ws


@app.context_processor
def inject_globals():
    user = current_user()
    workspaces = []
    if user:
        workspaces = get_db().execute(
            "SELECT w.*, m.role FROM workspaces w JOIN memberships m ON m.workspace_id=w.id"
            " WHERE m.user_id=? ORDER BY w.name", (user["id"],)).fetchall()
    return {"me": user, "my_workspaces": workspaces,
            "year": datetime.now().year}


# ---------------------------------------------------------------------- render

def strip_html(text):
    """Drop raw HTML from user input. Markdown is rendered later, on display."""
    text = re.sub(r"<[^>]*>", "", text or "")
    return text.replace("\x00", "")


def render_markdown(text):
    """Small markdown subset: headings, bold, italics, code, links, images, lists."""
    out = []
    for block in (text or "").split("\n"):
        line = block
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1)) + 1
            out.append("<h%d>%s</h%d>" % (level, html_mod.escape(heading.group(2)), level))
            continue
        # images before links
        line = re.sub(r"!\[([^\]]*)\]\(([^)]*)\)",
                      lambda m: '<img src="%s" alt="%s">' % (m.group(2), html_mod.escape(m.group(1))), line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]*)\)",
                      lambda m: '<a href="%s">%s</a>' % (m.group(2), html_mod.escape(m.group(1))), line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        if line.startswith("- "):
            out.append("<li>%s</li>" % line[2:])
        elif line.strip():
            out.append("<p>%s</p>" % line)
        else:
            out.append("")
    rendered = "\n".join(out)
    rendered = rendered.replace("</li>\n<li>", "</li><li>")
    if "<li>" in rendered:
        rendered = re.sub(r"(<li>.*?</li>)", r"<ul>\1</ul>", rendered, flags=re.S)
    return rendered


app.jinja_env.filters["md"] = render_markdown


# ----------------------------------------------------------------------- views

@app.get("/")
def index():
    if current_user():
        return redirect(url_for("dashboard"))
    db = get_db()
    stats = {
        "notes": db.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"],
        "teams": db.execute("SELECT COUNT(*) c FROM workspaces").fetchone()["c"],
    }
    return render_template("landing.html", stats=stats)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["uid"] = user["id"]
            nxt = request.form.get("next") or request.args.get("next")
            if nxt and nxt.startswith("/"):
                return redirect(nxt)
            return redirect(url_for("dashboard"))
        error = "That email and password combination did not match."
    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        if not email or not name or len(password) < 8:
            error = "Name, email and a password of at least 8 characters are required."
        elif get_db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            error = "An account with that email already exists."
        else:
            db = get_db()
            stamp = now()
            handle = slugify(name)
            cur = db.execute(
                "INSERT INTO users(email,password_hash,name,handle,title,avatar,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (email, generate_password_hash(password), name, handle, "", "#3f6f6a", stamp))
            uid = cur.lastrowid
            wslug = slugify(name) + "-notes"
            base = wslug
            i = 2
            while db.execute("SELECT 1 FROM workspaces WHERE slug=?", (wslug,)).fetchone():
                wslug = "%s-%d" % (base, i)
                i += 1
            wid = db.execute("INSERT INTO workspaces(name,slug,owner_id,plan,created_at) VALUES(?,?,?,?,?)",
                             ("%s's notes" % name.split()[0], wslug, uid, "personal", stamp)).lastrowid
            db.execute("INSERT INTO memberships(workspace_id,user_id,role,joined_at) VALUES(?,?,?,?)",
                       (wid, uid, "owner", stamp))
            db.commit()
            session.clear()
            session["uid"] = uid
            return redirect(url_for("dashboard"))
    return render_template("register.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    nxt = request.args.get("next")
    if nxt and nxt.startswith("/"):
        return redirect(nxt)
    return redirect(url_for("index"))


@app.get("/dashboard")
@login_required
def dashboard():
    db = get_db()
    user = current_user()
    ws_ids = [r["workspace_id"] for r in db.execute(
        "SELECT workspace_id FROM memberships WHERE user_id=?", (user["id"],))]
    recent = []
    if ws_ids:
        marks = ",".join("?" * len(ws_ids))
        recent = db.execute(
            "SELECT n.*, w.name ws_name, w.slug ws_slug, u.name author"
            " FROM notes n JOIN workspaces w ON w.id=n.workspace_id JOIN users u ON u.id=n.author_id"
            " WHERE n.workspace_id IN (%s) ORDER BY n.updated_at DESC LIMIT 8" % marks, ws_ids).fetchall()
    return render_template("dashboard.html", recent=recent)


@app.get("/w/<slug>/")
@login_required
def workspace(slug):
    ws = require_workspace(slug)
    db = get_db()
    notes = db.execute(
        "SELECT n.*, u.name author FROM notes n JOIN users u ON u.id=n.author_id"
        " WHERE n.workspace_id=? ORDER BY n.updated_at DESC", (ws["id"],)).fetchall()
    tags = db.execute(
        "SELECT t.name, COUNT(nt.note_id) c FROM tags t LEFT JOIN note_tags nt ON nt.tag_id=t.id"
        " WHERE t.workspace_id=? GROUP BY t.id ORDER BY c DESC", (ws["id"],)).fetchall()
    return render_template("workspace.html", ws=ws, notes=notes, tags=tags,
                           role=membership(ws["id"])["role"])


def note_or_404(note_id):
    note = get_db().execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if not note:
        abort(404)
    return note


@app.get("/w/<slug>/n/<int:note_id>")
@login_required
def note_view(slug, note_id):
    ws = require_workspace(slug)
    db = get_db()
    note = note_or_404(note_id)
    if note["workspace_id"] != ws["id"]:
        abort(404)
    author = db.execute("SELECT * FROM users WHERE id=?", (note["author_id"],)).fetchone()
    tags = db.execute(
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id=t.id WHERE nt.note_id=?", (note_id,)).fetchall()
    comments = db.execute(
        "SELECT c.*, u.name, u.avatar FROM comments c JOIN users u ON u.id=c.author_id"
        " WHERE c.note_id=? ORDER BY c.created_at", (note_id,)).fetchall()
    versions = db.execute(
        "SELECT v.*, u.name FROM note_versions v JOIN users u ON u.id=v.editor_id"
        " WHERE v.note_id=? ORDER BY v.created_at DESC", (note_id,)).fetchall()

    # related notes share at least one tag
    tag_names = [t["name"] for t in tags]
    related = []
    if tag_names:
        quoted = ",".join("'" + t + "'" for t in tag_names)
        sql = ("SELECT DISTINCT n.id, n.title, w.slug ws_slug FROM notes n "
               "JOIN note_tags nt ON nt.note_id=n.id JOIN tags t ON t.id=nt.tag_id "
               "JOIN workspaces w ON w.id=n.workspace_id "
               "WHERE t.name IN (" + quoted + ") AND n.id != " + str(int(note_id)) + " LIMIT 6")
        related = db.execute(sql).fetchall()

    return render_template("note.html", ws=ws, note=note, author=author, tags=tag_names,
                           comments=comments, versions=versions, related=related,
                           body_html=render_markdown(note["body"]),
                           role=membership(ws["id"])["role"])


@app.route("/w/<slug>/new", methods=["GET", "POST"])
@login_required
def note_new(slug):
    ws = require_workspace(slug)
    if request.method == "POST":
        title = strip_html(request.form.get("title", "").strip()) or "Untitled note"
        body = strip_html(request.form.get("body", ""))
        tags = request.form.get("tags", "")
        db = get_db()
        stamp = now()
        cur = db.execute(
            "INSERT INTO notes(workspace_id,author_id,title,body,is_public,created_at,updated_at)"
            " VALUES(?,?,?,?,0,?,?)", (ws["id"], current_user()["id"], title, body, stamp, stamp))
        note_id = cur.lastrowid
        db.execute("INSERT INTO note_versions(note_id,editor_id,title,body,created_at) VALUES(?,?,?,?,?)",
                   (note_id, current_user()["id"], title, body, stamp))
        _save_tags(ws["id"], note_id, tags)
        db.execute("INSERT INTO activity(workspace_id,actor_id,verb,detail,created_at) VALUES(?,?,?,?,?)",
                   (ws["id"], current_user()["id"], "created", title, stamp))
        db.commit()
        return redirect(url_for("note_view", slug=slug, note_id=note_id))
    return render_template("editor.html", ws=ws, note=None, tag_str="")


@app.route("/w/<slug>/n/<int:note_id>/edit", methods=["GET", "POST"])
@login_required
def note_edit(slug, note_id):
    ws = require_workspace(slug)
    db = get_db()
    note = note_or_404(note_id)
    if note["workspace_id"] != ws["id"]:
        abort(404)
    tags = db.execute(
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id=t.id WHERE nt.note_id=?", (note_id,)).fetchall()
    if request.method == "POST":
        title = strip_html(request.form.get("title", "").strip()) or "Untitled note"
        body = strip_html(request.form.get("body", ""))
        stamp = now()
        db.execute("INSERT INTO note_versions(note_id,editor_id,title,body,created_at) VALUES(?,?,?,?,?)",
                   (note_id, current_user()["id"], title, body, stamp))
        db.execute("UPDATE notes SET title=?, body=?, updated_at=? WHERE id=?", (title, body, stamp, note_id))
        _save_tags(ws["id"], note_id, request.form.get("tags", ""))
        db.execute("INSERT INTO activity(workspace_id,actor_id,verb,detail,created_at) VALUES(?,?,?,?,?)",
                   (ws["id"], current_user()["id"], "edited", title, stamp))
        db.commit()
        return redirect(url_for("note_view", slug=slug, note_id=note_id))
    return render_template("editor.html", ws=ws, note=note,
                           tag_str="\n".join(t["name"] for t in tags))


def _save_tags(workspace_id, note_id, tagstr):
    db = get_db()
    db.execute("DELETE FROM note_tags WHERE note_id=?", (note_id,))
    for tag in [t.strip() for t in (tagstr or "").splitlines() if t.strip()][:8]:
        tag = tag[:120]
        row = db.execute("SELECT id FROM tags WHERE workspace_id=? AND name=?", (workspace_id, tag)).fetchone()
        tag_id = row["id"] if row else db.execute(
            "INSERT INTO tags(workspace_id,name) VALUES(?,?)", (workspace_id, tag)).lastrowid
        db.execute("INSERT OR IGNORE INTO note_tags(note_id,tag_id) VALUES(?,?)", (note_id, tag_id))


@app.post("/w/<slug>/n/<int:note_id>/comment")
@login_required
def add_comment(slug, note_id):
    ws = require_workspace(slug)
    note = note_or_404(note_id)
    if note["workspace_id"] != ws["id"]:
        abort(404)
    body = strip_html(request.form.get("body", "")).strip()
    if body:
        get_db().execute("INSERT INTO comments(note_id,author_id,body,created_at) VALUES(?,?,?,?)",
                         (note_id, current_user()["id"], body, now()))
        get_db().execute("INSERT INTO activity(workspace_id,actor_id,verb,detail,created_at) VALUES(?,?,?,?,?)",
                         (ws["id"], current_user()["id"], "commented", note["title"], now()))
        get_db().commit()
    return redirect(url_for("note_view", slug=slug, note_id=note_id) + "#comments")


@app.route("/w/<slug>/import", methods=["GET", "POST"])
@login_required
def import_doc(slug):
    """Pull an exported document (Markdown or plain text) straight into a draft."""
    ws = require_workspace(slug)
    preview = None
    error = None
    url = ""
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Quillworks-Importer/2.3"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                raw = resp.read(20000)
            charset = resp.headers.get_content_charset() or "utf-8"
            preview = raw.decode(charset, "replace")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            error = "Could not import that document: %s" % exc
    return render_template("import.html", ws=ws, preview=preview, error=error, url=url)


@app.get("/w/<slug>/members")
@login_required
def members(slug):
    ws = require_workspace(slug)
    rows = get_db().execute(
        "SELECT u.id, u.name, u.email, u.handle, u.avatar, m.role, m.joined_at"
        " FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?"
        " ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, u.name",
        (ws["id"],)).fetchall()
    return render_template("members.html", ws=ws, members=rows, role=membership(ws["id"])["role"])


@app.get("/w/<slug>/settings")
@login_required
def settings(slug):
    ws = require_workspace(slug)
    role = membership(ws["id"])["role"]
    return render_template("settings.html", ws=ws, role=role, is_admin=role in ("owner", "admin"))


@app.post("/w/<slug>/settings")
@login_required
def settings_save(slug):
    ws = require_workspace(slug)
    if membership(ws["id"])["role"] not in ("owner", "admin"):
        abort(403)
    name = strip_html(request.form.get("name", ws["name"]))[:80]
    get_db().execute("UPDATE workspaces SET name=? WHERE id=?", (name, ws["id"]))
    get_db().commit()
    return redirect(url_for("settings", slug=slug))


@app.get("/w/<slug>/search")
@login_required
def search(slug):
    ws = require_workspace(slug)
    q = request.args.get("q", "").strip()
    rows = []
    if q:
        rows = get_db().execute(
            "SELECT n.*, u.name author FROM notes n JOIN users u ON u.id=n.author_id"
            " WHERE n.workspace_id=? AND (n.title LIKE ? OR n.body LIKE ?)"
            " ORDER BY n.updated_at DESC", (ws["id"], "%" + q + "%", "%" + q + "%")).fetchall()
    return render_template("search.html", ws=ws, q=q, rows=rows)


@app.get("/account")
@login_required
def account():
    user = current_user()
    return render_template("account.html", user=user)


# ------------------------------------------------------------------------- api

@app.patch("/api/workspaces/<int:wid>/members/<int:uid>")
@login_required
def api_member_update(wid, uid):
    """Rename a member or change their workspace role (used by the members table)."""
    db = get_db()
    actor = current_user()
    member = db.execute("SELECT * FROM memberships WHERE workspace_id=? AND user_id=?",
                        (wid, uid)).fetchone()
    if not member:
        return jsonify(error="member not found"), 404
    actor_row = db.execute("SELECT * FROM memberships WHERE workspace_id=? AND user_id=?",
                           (wid, actor["id"])).fetchone()
    if not actor_row:
        return jsonify(error="not a member of this workspace"), 403
    actor_role = actor_row["role"]
    # members may manage their own display preferences; admins manage everyone
    if actor_role not in ("owner", "admin") and actor["id"] != uid:
        return jsonify(error="admin role required"), 403
    data = request.get_json(silent=True) or {}
    new_role = data.get("role", member["role"])
    if new_role not in ("owner", "admin", "editor", "viewer"):
        return jsonify(error="invalid role"), 400
    db.execute("UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?",
               (new_role, wid, uid))
    db.execute("INSERT INTO activity(workspace_id,actor_id,verb,detail,created_at) VALUES(?,?,?,?,?)",
               (wid, actor["id"], "role_changed", "%s -> %s" % (member["role"], new_role), now()))
    db.commit()
    return jsonify(ok=True, role=new_role)


@app.post("/api/notes/<int:note_id>/versions/<int:version_id>/restore")
@login_required
def api_restore(note_id, version_id):
    db = get_db()
    note = note_or_404(note_id)
    if not membership(note["workspace_id"]):
        return jsonify(error="forbidden"), 403
    version = db.execute("SELECT * FROM note_versions WHERE id=?", (version_id,)).fetchone()
    if not version:
        return jsonify(error="version not found"), 404
    db.execute("INSERT INTO note_versions(note_id,editor_id,title,body,created_at) VALUES(?,?,?,?,?)",
               (note_id, current_user()["id"], note["title"], note["body"], now()))
    db.execute("UPDATE notes SET title=?, body=?, updated_at=? WHERE id=?",
               (version["title"], version["body"], now(), note_id))
    db.commit()
    return jsonify(ok=True, title=version["title"])


@app.get("/api/notes/<int:note_id>/versions")
@login_required
def api_versions(note_id):
    note = note_or_404(note_id)
    if not membership(note["workspace_id"]):
        return jsonify(error="forbidden"), 403
    rows = get_db().execute(
        "SELECT v.id, v.title, v.created_at, u.name FROM note_versions v"
        " JOIN users u ON u.id=v.editor_id WHERE v.note_id=? ORDER BY v.created_at DESC",
        (note_id,)).fetchall()
    return jsonify(versions=[dict(r) for r in rows])


@app.get("/healthz")
def healthz():
    return jsonify(status="ok", app="quillworks")


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, message="We could not find that page."), 404


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403,
                           message="You do not have access to this workspace."), 403


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500,
                           message="Something broke on our side. The incident was logged."), 500


init_db()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8801)),
            debug=False, threaded=True)

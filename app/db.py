import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    info TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    effort TEXT NOT NULL DEFAULT 'high',
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',
    tool_calls TEXT NOT NULL DEFAULT '[]',
    parts TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    field TEXT NOT NULL CHECK (field IN ('target', 'info', 'instructions', 'description', 'scope')),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS message_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_campaign ON messages(campaign_id, id);
CREATE INDEX IF NOT EXISTS idx_attachments_campaign ON attachments(campaign_id, field);
CREATE INDEX IF NOT EXISTS idx_message_attachments ON message_attachments(message_id, id);
"""


def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn):
    if "effort" not in _columns(conn, "campaigns"):
        conn.execute("ALTER TABLE campaigns ADD COLUMN effort TEXT NOT NULL DEFAULT 'high'")
    if "reasoning" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT NOT NULL DEFAULT ''")
    if "tool_calls" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN tool_calls TEXT NOT NULL DEFAULT '[]'")
    if "parts" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN parts TEXT NOT NULL DEFAULT '[]'")


def init():
    with db() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)


def recover():
    """Close out runs left open by a crash or restart.

    A campaign still marked running at boot cannot be running: the process
    that owned the turn is gone. Keep whatever was checkpointed, flag the
    dangling tool calls and the turn itself as interrupted, and hand the
    campaign back to the user.
    """
    interrupted = 0
    with db() as conn:
        rows = conn.execute("SELECT id FROM campaigns WHERE status = 'running'").fetchall()
        for row in rows:
            campaign_id = row["id"]
            last = conn.execute(
                "SELECT * FROM messages WHERE campaign_id = ? ORDER BY id DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if last is not None and last["role"] == "assistant":
                parts = json.loads(last["parts"] or "[]")
                calls = json.loads(last["tool_calls"] or "[]")
                if not (last["content"].strip() or last["reasoning"].strip() or parts or calls):
                    # the placeholder never saw a single token
                    conn.execute("DELETE FROM messages WHERE id = ?", (last["id"],))
                else:
                    for call in calls:
                        if call.get("output") is None:
                            call["output"] = "interrupted by a server restart"
                            call["ok"] = False
                    for part in parts:
                        if part.get("type") == "tool" and part.get("output") is None:
                            part["output"] = "interrupted by a server restart"
                            part["ok"] = False
                    parts.append({"type": "notice", "text": "run interrupted by a server restart"})
                    # the next request replays this row as history, and an
                    # empty assistant message breaks some providers
                    text = last["content"].strip() or "(run interrupted by a server restart)"
                    conn.execute(
                        "UPDATE messages SET content = ?, tool_calls = ?, parts = ? WHERE id = ?",
                        (text, json.dumps(calls), json.dumps(parts), last["id"]),
                    )
            conn.execute(
                "UPDATE campaigns SET status = 'interrupted', updated_at = datetime('now') WHERE id = ?",
                (campaign_id,),
            )
            interrupted += 1
    return interrupted

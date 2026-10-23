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
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
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

CREATE INDEX IF NOT EXISTS idx_messages_campaign ON messages(campaign_id, id);
CREATE INDEX IF NOT EXISTS idx_attachments_campaign ON attachments(campaign_id, field);
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
    if "input_tokens" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0")
    if "cached_tokens" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN cached_tokens INTEGER NOT NULL DEFAULT 0")
    if "output_tokens" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0")
    if "requests" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN requests INTEGER NOT NULL DEFAULT 0")
    if "cost_usd" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0")


def init():
    with db() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)

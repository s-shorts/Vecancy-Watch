import json
import secrets
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT,
    url TEXT NOT NULL UNIQUE,
    enabled INTEGER DEFAULT 0,
    user_disabled INTEGER DEFAULT 0,
    custom INTEGER DEFAULT 0,
    recruitment_pages TEXT,
    pages_found_at TEXT,
    last_checked TEXT,
    last_status TEXT,
    last_error TEXT,
    scanned_ok INTEGER DEFAULT 0,
    found_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vacancies (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    source_id INTEGER,
    state TEXT,
    institution TEXT,
    title TEXT,
    url TEXT,
    page_url TEXT,
    kind TEXT,             -- advertisement | corrigendum | update
    nature TEXT,           -- Regular | Contract/Guest
    profession TEXT,
    posted_date TEXT,
    opening_date TEXT,
    deadline TEXT,
    status TEXT,           -- open | upcoming | likely_open | closed | unknown
    baseline INTEGER DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    starred INTEGER DEFAULT 0,
    applied INTEGER DEFAULT 0,
    hidden INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_vac_status ON vacancies(status);
CREATE INDEX IF NOT EXISTS ix_vac_seen ON vacancies(first_seen);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    created TEXT,
    kind TEXT,
    title TEXT,
    body TEXT,
    url TEXT,
    vacancy_id INTEGER,
    read INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reminders (
    vacancy_id INTEGER,
    key TEXT,
    PRIMARY KEY (vacancy_id, key)
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    started TEXT,
    finished TEXT,
    sources INTEGER,
    ok INTEGER,
    failed INTEGER,
    new_items INTEGER
);
"""


def connect():
    con = sqlite3.connect(config.db_path(), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


@contextmanager
def conn():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


MIGRATIONS = {
    "vacancies": {"details": "TEXT", "subjects": "TEXT", "other_subjects": "TEXT", "subject_match": "TEXT",
                  "notice_read": "TEXT", "site": "TEXT", "deadline_src": "TEXT", "alt_links": "TEXT"},
    "sources": {"discovered": "INTEGER DEFAULT 0", "parent_id": "INTEGER"},
}
EXTRA = """
CREATE TABLE IF NOT EXISTS notices (vacancy_id INTEGER PRIMARY KEY, text TEXT);
CREATE TABLE IF NOT EXISTS rejected (uid TEXT PRIMARY KEY, seen TEXT);
CREATE TABLE IF NOT EXISTS vkeys (key TEXT PRIMARY KEY, vacancy_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_vkeys_vac ON vkeys(vacancy_id);
"""


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        c.executescript(EXTRA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


_MISSING = object()


def get_setting(key, default=_MISSING):
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return config.DEFAULT_SETTINGS.get(key) if default is _MISSING else default
    return json.loads(row["value"])


def all_settings():
    s = dict(config.DEFAULT_SETTINGS)
    with conn() as c:
        for row in c.execute("SELECT key, value FROM settings"):
            s[row["key"]] = json.loads(row["value"])
    return s


def set_settings(values: dict):
    with conn() as c:
        for k, v in values.items():
            c.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))


def secret_key():
    key = get_setting("_secret", None)
    if not key:
        key = secrets.token_hex(24)
        set_settings({"_secret": key})
    return key

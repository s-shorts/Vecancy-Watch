import csv

from . import config, db


def load_seed():
    with open(config.SOURCES_CSV, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("url") or "").strip()]


def seed_counts():
    counts = {}
    for r in load_seed():
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return counts


def sync(states, include_national=True, include_colleges=None):
    """Make sure seed sources exist and enable exactly those in the chosen states."""
    wanted = set(states) | ({config.NATIONAL} if include_national else set())
    if include_colleges is None:
        include_colleges = db.get_setting("include_colleges")
    with db.conn() as c:
        for r in load_seed():
            c.execute("INSERT OR IGNORE INTO sources(state, name, type, url, enabled, custom) "
                      "VALUES(?, ?, ?, ?, 0, 0)",
                      (r["state"].strip(), r["name"].strip(), r["type"].strip(), r["url"].strip()))
        for row in c.execute("SELECT id, state, user_disabled, custom, discovered FROM sources").fetchall():
            if row["custom"]:
                continue
            on = 1 if (row["state"] in wanted and not row["user_disabled"]
                       and (include_colleges or not row["discovered"])) else 0
            c.execute("UPDATE sources SET enabled=? WHERE id=?", (on, row["id"]))


def add_custom(state, name, url, type_="Custom"):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    with db.conn() as c:
        existing = c.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchone()
        if existing:
            c.execute("UPDATE sources SET enabled=1, user_disabled=0 WHERE id=?", (existing["id"],))
            return existing["id"]
        cur = c.execute("INSERT INTO sources(state, name, type, url, enabled, custom) VALUES(?, ?, ?, ?, 1, 1)",
                        (state, name.strip() or url, type_, url))
        return cur.lastrowid


def toggle(source_id):
    with db.conn() as c:
        row = c.execute("SELECT enabled FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return None
        new = 0 if row["enabled"] else 1
        c.execute("UPDATE sources SET enabled=?, user_disabled=? WHERE id=?", (new, 1 - new, source_id))
        return new


def add_discovered(parent, colleges, enabled):
    """Store colleges found on a university website. Returns how many were new."""
    from .crawler import site_key
    from urllib.parse import urlparse
    new = 0
    with db.conn() as c:
        known_sites = {site_key(urlparse(r[0]).netloc) for r in c.execute("SELECT url FROM sources")}
        for name, url, kind in colleges:
            if site_key(urlparse(url).netloc) in known_sites:
                continue  # already watched (e.g. CMP is in the list already)
            cur = c.execute("INSERT OR IGNORE INTO sources(state, name, type, url, enabled, custom, discovered, parent_id) "
                            "VALUES(?, ?, ?, ?, ?, 0, 1, ?)", (parent["state"], name, kind, url, 1 if enabled else 0,
                                                              parent["id"]))
            new += cur.rowcount
            known_sites.add(site_key(urlparse(url).netloc))
    return new

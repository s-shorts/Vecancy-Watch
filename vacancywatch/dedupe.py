"""Recognises when several links / pages / websites point to the same recruitment notice and keeps one card.

Two notices are treated as the same when, for the same institution website, they share any of:
  * the advertisement number (e.g. CMP/Rec./02/Asst. Prof./2026)
  * the notice document (same PDF link)
  * the notice content (same PDF text, even at a different link)
  * the last date (one institution's faculty drive = one last date)
"""
import hashlib
import json
import re
from urllib.parse import urlparse

from . import crawler, extract


def site_of(url):
    return crawler.site_key(urlparse(url or "").netloc) if url else ""


def _norm_url(url):
    u = urlparse(url)
    host = u.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return f"{host}{u.path.rstrip('/')}" + (f"?{u.query}" if u.query else "")


def content_hash(text):
    t = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    return hashlib.sha1(t[:8000].encode()).hexdigest() if len(t) > 300 else None


def keys_for(site, uid=None, title="", url="", page_url="", deadline=None, kind="advertisement",
             details=None, notice_text="", read_how=""):
    keys = []
    if uid:
        keys.append(f"uid:{uid}")
    if kind == "update":
        return keys  # results / interview lists are never merged with adverts
    ak = extract.advt_key(title) or extract.advt_key((details or {}).get("advt_no") and
                                                     "Advt No. " + (details or {}).get("advt_no"))
    if ak:
        keys.append(f"advt:{site}|{ak}")
    if url and url != page_url and (crawler.is_doc(url) or urlparse(url).query):
        keys.append(f"doc:{_norm_url(url)}")
    if read_how in ("pdf", "page") and url != page_url:
        h = content_hash(notice_text)
        if h:
            keys.append(f"hash:{h}")
    if deadline:
        keys.append(f"drive:{site}|{deadline}")
    return keys


def find_existing(c, keys):
    if not keys:
        return None
    row = c.execute(f"SELECT k.vacancy_id FROM vkeys k JOIN vacancies v ON v.id = k.vacancy_id "
                    f"WHERE k.key IN ({','.join('?' * len(keys))}) ORDER BY v.id LIMIT 1", keys).fetchone()
    return row[0] if row else None


def add_keys(c, vid, keys):
    for k in keys:
        c.execute("INSERT OR REPLACE INTO vkeys(key, vacancy_id) VALUES(?, ?)", (k, vid))


def add_alt_link(row, title, url):
    alts = json.loads(row.get("alt_links") or "[]")
    if url and url != row.get("url") and all(a["url"] != url or a["title"] != title for a in alts):
        alts.append({"title": (title or "")[:200], "url": url})
    return json.dumps(alts[:25])

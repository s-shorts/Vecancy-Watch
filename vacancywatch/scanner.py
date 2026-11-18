"""Runs scans over all enabled sources, stores results and raises notification events."""
import datetime as dt
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import re

from . import config, crawler, db, dedupe, extract, notify, sources

_lock = threading.Lock()
PROGRESS = {"running": False, "total": 0, "done": 0, "current": "", "new": 0,
            "started": None, "finished": None, "failed": 0}
READ_PER_SOURCE = 8          # notifications opened per institution per check
ACTIVE = ("open", "upcoming", "likely_open")


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _d(s):
    return dt.date.fromisoformat(s[:10]) if s else None


def _iso(d):
    return d.isoformat() if d else None


def is_running():
    return PROGRESS["running"]


def start_scan_async(source_ids=None):
    if PROGRESS["running"]:
        return False
    PROGRESS.update(running=True, done=0, total=0, current="Starting…")
    threading.Thread(target=run_scan, args=(source_ids,), daemon=True).start()
    return True


def _crawl_one(src, patterns, fetcher, find_colleges_enabled=True):
    found_at = src.get("pages_found_at")
    rediscover = not found_at or (dt.datetime.now() - dt.datetime.fromisoformat(found_at)).days >= 7
    find_colleges = (rediscover and find_colleges_enabled and not src.get("discovered")
                     and "universit" in ((src.get("type") or "") + " " + (src.get("name") or "")).lower())
    res = crawler.crawl_source(src, patterns, fetcher, rediscover=rediscover, find_colleges=find_colleges)
    if res.error and not res.items and not rediscover:
        res = crawler.crawl_source(src, patterns, fetcher, rediscover=True)
    if res.error and not res.items:
        return res
    with db.conn() as c:
        known = {r["uid"]: dict(r) for r in c.execute(
            "SELECT uid, notice_read, status, kind FROM vacancies WHERE source_id=?", (src["id"],))}
        uids = [it.uid for it in res.items]
        rejected = {r["uid"] for r in c.execute(
            f"SELECT uid FROM rejected WHERE uid IN ({','.join('?' * len(uids))})", uids)} if uids else set()
    today = dt.date.today()
    budget = READ_PER_SOURCE
    kept, res.rejected = [], []
    for it in res.items:
        if it.uid in rejected:
            continue
        k = known.get(it.uid)
        if k:
            # older notices that were never read (e.g. found by an earlier version) are read now
            if k["notice_read"] is None and k["status"] in ACTIVE and k["kind"] != "update" and budget > 0 \
                    and crawler.worth_fetching(it, src["url"]):
                budget -= 1
                crawler.enrich_item(it, fetcher, src["url"], patterns)
            kept.append(it)
            continue
        if it.confirm:
            if budget <= 0:
                continue  # try again on the next check
            budget -= 1
            crawler.enrich_item(it, fetcher, src["url"], patterns)
            head = it.notice_text[:4000]
            faculty = it.notice_text and any(p.search(it.notice_text[:30000]) for p in patterns)
            nonteach = extract.NON_TEACHING_RE.search(head[:800]) and not re.search(
                r"assistant\s*prof|asst\.?\s*prof|faculty|teaching\s+posts?", head, re.I)
            if it.read_how in ("pdf", "page") and (not faculty or nonteach):
                res.rejected.append(it.uid)
                continue
            if not faculty and it.read_how != "scanned":
                continue  # could not open it right now - try again on the next check
            # scanned notices can't be read: keep them as "subject not confirmed" so nothing is missed
            it.kind = extract.classify_kind(it.title + " " + head[:300])
            kept.append(it)
            continue
        closed = it.deadline and it.deadline < today
        if it.kind != "update" and not closed and budget > 0 and crawler.worth_fetching(it, src["url"]):
            budget -= 1
            crawler.enrich_item(it, fetcher, src["url"], patterns)
        kept.append(it)
    res.items = kept
    return res


def _subject_eval(title, text, how, subj):
    full = (title or "") + " " + (text or "")
    found = extract.find_subjects(full, subj)
    others = extract.other_subjects(full)
    if not subj:
        match = "any"
    elif found:
        match = "yes"
    elif how in ("pdf", "page") and (len(text or "") > 400 or extract.other_subjects(text or "")):
        match = "no"
    elif extract.other_subjects(title or "") and not re.search(
            r"various|all\s+subjects|departments|disciplines|विभिन्न", title or "", re.I):
        match = "no"
    else:
        match = "maybe"
    details = extract.read_details(text) if text else {}
    if text and subj:
        details["snippets"] = extract.subject_snippets(text, subj)
    return match, ", ".join(found), ", ".join(o for o in others if o not in found), details


def _status_for(opening, deadline, posted, baseline, first_seen, today):
    return extract.compute_status(opening, deadline, posted, None if baseline else _d(first_seen), today)


def _store(src, res, profession, alert_statuses, subj=None, include_colleges=True):
    events = []
    subj = subj or {}
    today = dt.date.today()
    now = _now()
    with db.conn() as c:
        if res.error and not res.items:
            c.execute("UPDATE sources SET last_checked=?, last_status='error', last_error=? WHERE id=?",
                      (now, res.error, src["id"]))
            return events, False
        baseline = 0 if src.get("scanned_ok") else 1
        c.execute("UPDATE sources SET last_checked=?, last_status='ok', last_error=NULL, recruitment_pages=?, "
                  "scanned_ok=1 WHERE id=?", (now, json.dumps(res.pages), src["id"]))
        if res.rediscovered:
            c.execute("UPDATE sources SET pages_found_at=? WHERE id=?", (now, src["id"]))
        site = dedupe.site_of(src["url"])
        # best-described item first, so it becomes the card that others are merged into
        for it in sorted(res.items, key=lambda i: -extract.title_score(i.title)):
            match, found, others, details = _subject_eval(it.title, it.notice_text, it.read_how, subj)
            title = extract.better_title(it.title, it.notice_text, details, found.split(", ") if found else [],
                                         others.split(", ") if others else [])
            keys = dedupe.keys_for(site, it.uid, title, it.url, it.page_url, _iso(it.deadline), it.kind,
                                   details, it.notice_text, it.read_how)
            vid = dedupe.find_existing(c, keys)
            if vid is None:
                status = _status_for(it.opening, it.deadline, it.posted, baseline, now, today)
                cur = c.execute(
                    "INSERT INTO vacancies(uid, source_id, state, institution, title, url, page_url, kind, nature, "
                    "profession, posted_date, opening_date, deadline, status, baseline, first_seen, last_seen, "
                    "details, subjects, other_subjects, subject_match, notice_read, site, deadline_src, alt_links) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (it.uid, src["id"], src["state"], src["name"], title, it.url, it.page_url, it.kind,
                     it.nature, profession, _iso(it.posted), _iso(it.opening), _iso(it.deadline), status,
                     baseline, now, now, json.dumps(details), found, others, match, it.read_how or None,
                     site, it.deadline_src or None, "[]"))
                vid = cur.lastrowid
                dedupe.add_keys(c, vid, keys)
                if it.notice_text:
                    c.execute("INSERT OR REPLACE INTO notices(vacancy_id, text) VALUES(?, ?)", (vid, it.notice_text))
                if it.kind != "update" and status in alert_statuses and match != "no":
                    events.append(("new", vid, None))
                continue

            # ---- same notice seen again (same link) or another link / page / site for it: merge
            row = dict(c.execute("SELECT * FROM vacancies WHERE id=?", (vid,)).fetchone())
            same = c.execute("SELECT 1 FROM vkeys WHERE key=? AND vacancy_id=?", (f"uid:{it.uid}", vid)).fetchone()
            upd = {}
            if not same:
                upd["alt_links"] = dedupe.add_alt_link(row, title, it.url)
                if extract.title_score(title) > extract.title_score(row["title"]):
                    upd["alt_links"] = dedupe.add_alt_link({**row, "alt_links": upd["alt_links"], "url": it.url},
                                                           row["title"], row["url"])
                    upd.update(title=title, url=it.url, page_url=it.page_url)
            if it.read_how in ("pdf", "page") and row.get("notice_read") not in ("pdf", "page"):
                m2, f2, o2, d2 = _subject_eval(upd.get("title", row["title"]), it.notice_text, it.read_how, subj)
                upd.update(details=json.dumps(d2), subjects=f2, other_subjects=o2, subject_match=m2,
                           notice_read=it.read_how)
                c.execute("INSERT OR REPLACE INTO notices(vacancy_id, text) VALUES(?, ?)", (vid, it.notice_text))
                if extract.title_score(row["title"]) < 2 and "title" not in upd:
                    upd["title"] = extract.better_title(row["title"], it.notice_text, d2,
                                                        f2.split(", ") if f2 else [], o2.split(", ") if o2 else [])
            deadline, opening, posted = _d(row["deadline"]), _d(row["opening_date"]), _d(row["posted_date"])
            cur_src, new_src = row.get("deadline_src") or "notice", it.deadline_src or "notice"
            if it.deadline:
                if not deadline or (new_src == "listing" and cur_src == "notice"):
                    deadline, upd["deadline_src"] = it.deadline, new_src
                elif new_src == cur_src and it.deadline > deadline:
                    if same and row["kind"] != "update":
                        events.append(("extended", vid, None))
                    deadline = it.deadline
            opening = opening or it.opening
            posted = posted or it.posted
            status = _status_for(opening, deadline, posted, row["baseline"], row["first_seen"], today)
            if same and row["status"] == "upcoming" and status == "open" and row["kind"] != "update" \
                    and not row["hidden"]:
                events.append(("opened", vid, None))
            upd.update(deadline=_iso(deadline), opening_date=_iso(opening), posted_date=_iso(posted),
                       status=status, last_seen=now)
            c.execute(f"UPDATE vacancies SET {', '.join(k + '=?' for k in upd)} WHERE id=?", [*upd.values(), vid])
            dedupe.add_keys(c, vid, keys + dedupe.keys_for(site, deadline=_iso(deadline), kind=row["kind"]))
        for uid in getattr(res, "rejected", []):
            c.execute("INSERT OR IGNORE INTO rejected(uid, seen) VALUES(?, ?)", (uid, now))
        n = c.execute("SELECT COUNT(*) FROM vacancies WHERE source_id=? AND kind!='update'",
                      (src["id"],)).fetchone()[0]
        c.execute("UPDATE sources SET found_count=? WHERE id=?", (n, src["id"]))
    if res.colleges:
        sources.add_discovered(src, res.colleges, include_colleges)
    return events, True


def maintenance():
    """Daily status refresh (upcoming -> open -> closed) and deadline reminders."""
    events = []
    today = dt.date.today()
    s = db.all_settings()
    days = sorted({int(d) for d in (s.get("reminder_days") or []) if str(d).isdigit()}, reverse=True)
    with db.conn() as c:
        for r in c.execute("SELECT * FROM vacancies WHERE status!='closed'").fetchall():
            st = _status_for(_d(r["opening_date"]), _d(r["deadline"]), _d(r["posted_date"]),
                             r["baseline"], r["first_seen"], today)
            if st != r["status"]:
                c.execute("UPDATE vacancies SET status=? WHERE id=?", (st, r["id"]))
                if r["status"] == "upcoming" and st == "open" and not r["hidden"] and r["kind"] != "update":
                    events.append(("opened", r["id"], None))
        if days:
            q = ("SELECT id, deadline FROM vacancies WHERE status='open' AND deadline IS NOT NULL AND hidden=0 "
                 "AND applied=0 AND kind!='update' AND COALESCE(subject_match,'')!='no'")
            if s.get("reminder_scope") == "starred":
                q += " AND starred=1"
            for r in c.execute(q).fetchall():
                left = (_d(r["deadline"]) - today).days
                applicable = [d for d in days if left <= d]
                if not applicable:
                    continue
                key = f"d{min(applicable)}"
                if c.execute("SELECT 1 FROM reminders WHERE vacancy_id=? AND key=?", (r["id"], key)).fetchone():
                    continue
                events.append(("reminder", r["id"], left))
                for d in applicable:
                    c.execute("INSERT OR IGNORE INTO reminders(vacancy_id, key) VALUES(?, ?)", (r["id"], f"d{d}"))
    return events


def run_scan(source_ids=None):
    if not _lock.acquire(blocking=False):
        return None
    try:
        s = db.all_settings()
        profession = s.get("profession") or "assistant_professor"
        patterns = config.compiled_patterns(profession)
        alert = {"open", "upcoming"} | ({"likely_open"} if s.get("notify_likely_open") else set())
        subj = config.compiled_subjects(s.get("subjects"), s.get("custom_subjects"))
        include_colleges = bool(s.get("include_colleges"))
        with db.conn() as c:
            if source_ids:
                q = f"SELECT * FROM sources WHERE id IN ({','.join('?' * len(source_ids))})"
                sources = [dict(r) for r in c.execute(q, list(source_ids))]
            else:
                sources = [dict(r) for r in c.execute("SELECT * FROM sources WHERE enabled=1")]
        started = _now()
        PROGRESS.update(running=True, total=len(sources), done=0, new=0, failed=0, started=started,
                        current="Starting…")
        fetcher = crawler.Fetcher()
        events, ok, failed = [], 0, 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_crawl_one, src, patterns, fetcher, include_colleges): src for src in sources}
            for f in as_completed(futs):
                src = futs[f]
                try:
                    res = f.result()
                except Exception as e:
                    res = crawler.CrawlResult([], [], f"internal error: {type(e).__name__}: {e}")
                ev, good = _store(src, res, profession, alert, subj, include_colleges)
                events += ev
                ok += good
                failed += not good
                PROGRESS.update(done=PROGRESS["done"] + 1, current=src["name"],
                                new=PROGRESS["new"] + sum(1 for e in ev if e[0] == "new"), failed=failed)
        events += maintenance()
        new_count = sum(1 for e in events if e[0] == "new")
        notify.dispatch(events)
        finished = _now()
        with db.conn() as c:
            c.execute("INSERT INTO scans(started, finished, sources, ok, failed, new_items) VALUES(?,?,?,?,?,?)",
                      (started, finished, len(sources), ok, failed, new_count))
        if not source_ids:
            db.set_settings({"last_scan": finished})
        PROGRESS.update(finished=finished, current="Done")
        return {"sources": len(sources), "ok": ok, "failed": failed, "new": new_count, "events": len(events)}
    finally:
        PROGRESS["running"] = False
        _lock.release()


def tick():
    """Called periodically by the scheduler."""
    s = db.all_settings()
    if not s.get("profession"):
        return
    last = s.get("last_scan")
    interval = float(s.get("scan_interval_hours") or 6)
    due = not last or (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds() >= interval * 3600 - 60
    if due:
        run_scan()
    else:
        notify.dispatch(maintenance())


def reevaluate_subjects():
    """Recompute subject matches after the user changes their subjects."""
    s = db.all_settings()
    subj = config.compiled_subjects(s.get("subjects"), s.get("custom_subjects"))
    with db.conn() as c:
        rows = c.execute("SELECT v.id, v.title, v.notice_read, n.text FROM vacancies v "
                         "LEFT JOIN notices n ON n.vacancy_id = v.id").fetchall()
        for r in rows:
            match, found, others, details = _subject_eval(r["title"], r["text"] or "", r["notice_read"] or "", subj)
            c.execute("UPDATE vacancies SET subjects=?, other_subjects=?, subject_match=?, details=? WHERE id=?",
                      (found, others, match, json.dumps(details), r["id"]))


def dedupe_existing():
    """One-time clean-up of history saved by earlier versions: merge duplicate cards, correct deadlines
    (online date, not hard-copy date) and replace titles like 'View advertisement'. Safe to run repeatedly."""
    s = db.all_settings()
    subj = config.compiled_subjects(s.get("subjects"), s.get("custom_subjects"))
    today = dt.date.today()
    merged = 0
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT v.*, s.url AS src_url FROM vacancies v LEFT JOIN sources s ON s.id = v.source_id ORDER BY v.id")]
        notices = {r[0]: r[1] for r in c.execute("SELECT vacancy_id, text FROM notices")}
        keymap, keep = {}, {}
        for r in rows:
            r.pop("src_url_dummy", None)
            site = r.get("site") or dedupe.site_of(r.get("src_url") or r["url"])
            text = notices.get(r["id"], "")
            details = json.loads(r.get("details") or "{}")
            listing = extract.extract_dates(r["title"] or "")
            if listing["deadline"]:
                r["deadline"], r["deadline_src"] = listing["deadline"].isoformat(), "listing"
            elif text and r.get("notice_read") in ("pdf", "page"):
                nd = extract.extract_dates(text, labelled_only=True)["deadline"]
                if nd:
                    r["deadline"], r["deadline_src"] = nd.isoformat(), "notice"
            if extract.title_score(r["title"]) < 2 and text:
                f = [x for x in (r.get("subjects") or "").split(", ") if x]
                o = [x for x in (r.get("other_subjects") or "").split(", ") if x]
                r["title"] = extract.better_title(r["title"], text, details, f, o)
            r["site"] = site
            keys = dedupe.keys_for(site, r["uid"], r["title"], r["url"], r["page_url"], r["deadline"], r["kind"],
                                   details, text, r.get("notice_read"))
            target = next((keymap[k] for k in keys if k in keymap), None)
            if target is None:
                keep[r["id"]] = r
                for k in keys:
                    keymap[k] = r["id"]
                continue
            t = keep[target]
            # merge r into t
            t["alt_links"] = dedupe.add_alt_link(t, r["title"], r["url"])
            for a in json.loads(r.get("alt_links") or "[]"):
                t["alt_links"] = dedupe.add_alt_link(t, a["title"], a["url"])
            if extract.title_score(r["title"]) > extract.title_score(t["title"]):
                t["alt_links"] = dedupe.add_alt_link({**t, "url": r["url"]}, t["title"], t["url"])
                t["title"], t["url"], t["page_url"] = r["title"], r["url"], r["page_url"]
            if r["deadline"] and (not t["deadline"] or (r.get("deadline_src") == "listing"
                                                        and t.get("deadline_src") != "listing")):
                t["deadline"], t["deadline_src"] = r["deadline"], r.get("deadline_src")
            for f in ("opening_date", "posted_date"):
                t[f] = t[f] or r[f]
            if t.get("notice_read") not in ("pdf", "page") and r.get("notice_read") in ("pdf", "page"):
                for f in ("details", "subjects", "other_subjects", "subject_match", "notice_read"):
                    t[f] = r[f]
                if text:
                    c.execute("INSERT OR REPLACE INTO notices(vacancy_id, text) VALUES(?, ?)", (target, text))
            for f in ("starred", "applied"):
                t[f] = max(t[f] or 0, r[f] or 0)
            t["first_seen"] = min(t["first_seen"], r["first_seen"])
            t["baseline"] = min(t["baseline"] or 0, r["baseline"] or 0)
            for k in keys:
                keymap[k] = target
            c.execute("UPDATE notifications SET vacancy_id=? WHERE vacancy_id=?", (target, r["id"]))
            c.execute("INSERT OR IGNORE INTO reminders(vacancy_id, key) SELECT ?, key FROM reminders WHERE vacancy_id=?",
                      (target, r["id"]))
            for tbl, col in (("reminders", "vacancy_id"), ("notices", "vacancy_id"), ("vacancies", "id")):
                c.execute(f"DELETE FROM {tbl} WHERE {col}=?", (r["id"],))
            merged += 1
        for vid, r in keep.items():
            r["status"] = _status_for(_d(r["opening_date"]), _d(r["deadline"]), _d(r["posted_date"]),
                                      r["baseline"], r["first_seen"], today)
            c.execute("UPDATE vacancies SET title=?, url=?, page_url=?, deadline=?, deadline_src=?, opening_date=?, "
                      "posted_date=?, status=?, site=?, alt_links=?, details=?, subjects=?, other_subjects=?, "
                      "subject_match=?, notice_read=?, starred=?, applied=?, first_seen=?, baseline=? WHERE id=?",
                      (r["title"], r["url"], r["page_url"], r["deadline"], r.get("deadline_src"), r["opening_date"],
                       r["posted_date"], r["status"], r["site"], r.get("alt_links") or "[]", r.get("details"),
                       r.get("subjects"), r.get("other_subjects"), r.get("subject_match"), r.get("notice_read"),
                       r["starred"], r["applied"], r["first_seen"], r["baseline"], vid))
        c.execute("DELETE FROM vkeys")
        for k, vid in keymap.items():
            c.execute("INSERT OR REPLACE INTO vkeys(key, vacancy_id) VALUES(?, ?)", (k, vid))
    return merged

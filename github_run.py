#!/usr/bin/env python3
"""Runs one VacancyWatch check inside GitHub Actions.

State is kept as readable JSON files in data/ (committed back to the repo after each run),
alerts go to Telegram / e-mail using repository secrets, and the dashboard is written to site/.
"""
import datetime as dt
import json
import os
import shutil
import time
from pathlib import Path

os.environ["TZ"] = "Asia/Kolkata"   # dates and "days left" in Indian time (GitHub's servers use UTC)
if hasattr(time, "tzset"):
    time.tzset()

ROOT = Path(__file__).resolve().parent
WORK = ROOT / ".work"
DATA = ROOT / "data"
SITE = ROOT / "site"
shutil.rmtree(WORK, ignore_errors=True)
os.environ["VACANCYWATCH_HOME"] = str(WORK)

from vacancywatch import config, db, notify, scanner, sources  # noqa: E402
from vacancywatch.scanner import dedupe_existing, reevaluate_subjects  # noqa: E402

TABLES = ["sources", "vacancies", "reminders", "notifications", "notices", "rejected"]
NOTICE_CHARS = 20000   # notification text kept per vacancy (for re-checking subjects)
KEEP_NOTIFICATIONS = 500


def load_state():
    db.init()
    with db.conn() as c:
        for t in TABLES:
            f = DATA / f"{t}.json"
            if not f.exists():
                continue
            rows = json.loads(f.read_text(encoding="utf-8"))
            for r in rows:
                cols = list(r)
                c.execute(f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                          [r[k] for k in cols])


def dump_state():
    DATA.mkdir(exist_ok=True)
    with db.conn() as c:
        for t in TABLES:
            q = f"SELECT * FROM {t}"
            if t == "notifications":
                q += f" ORDER BY id DESC LIMIT {KEEP_NOTIFICATIONS}"
            elif t == "notices":
                q = f"SELECT vacancy_id, substr(text, 1, {NOTICE_CHARS}) AS text FROM notices ORDER BY vacancy_id"
            elif t == "rejected":
                q += " ORDER BY uid"
            elif t != "reminders":
                q += " ORDER BY id"
            rows = [dict(r) for r in c.execute(q)]
            (DATA / f"{t}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def apply_settings(cfg):
    env = os.environ.get
    db.set_settings({
        "profession": cfg.get("profession") or "assistant_professor",
        "states": cfg.get("states") or [],
        "include_national": bool(cfg.get("include_national", True)),
        "include_colleges": bool(cfg.get("include_colleges", True)),
        "subjects": cfg.get("subjects", config.DEFAULT_SUBJECTS),
        "custom_subjects": cfg.get("custom_subjects") or [],
        "reminder_days": cfg.get("reminder_days") or [7, 3, 1],
        "reminder_scope": "all",
        "notify_likely_open": bool(cfg.get("notify_likely_open", True)),
        "notify_desktop": False,
        "telegram_enabled": bool(env("TELEGRAM_TOKEN") and env("TELEGRAM_CHAT_ID")),
        "telegram_token": env("TELEGRAM_TOKEN", ""),
        "telegram_chat_id": env("TELEGRAM_CHAT_ID", ""),
        "email_enabled": bool(env("SMTP_USER") and env("SMTP_PASSWORD") and env("EMAIL_TO")),
        "smtp_host": env("SMTP_HOST") or "smtp.gmail.com",
        "smtp_port": int(env("SMTP_PORT") or 587),
        "smtp_user": env("SMTP_USER", ""),
        "smtp_password": env("SMTP_PASSWORD", ""),
        "email_to": env("EMAIL_TO", ""),
    })


def build_site(cfg, result):
    shutil.rmtree(SITE, ignore_errors=True)
    shutil.copytree(ROOT / "web", SITE)
    (SITE / "data").mkdir(exist_ok=True)
    for t in ("vacancies", "sources", "notifications"):
        src = DATA / f"{t}.json"
        rows = json.loads(src.read_text(encoding="utf-8")) if src.exists() else []
        if t == "sources":
            rows = [{k: r.get(k) for k in ("id", "state", "name", "type", "url", "enabled", "custom", "discovered",
                                           "last_checked", "last_status", "last_error", "found_count")} for r in rows]
        (SITE / "data" / f"{t}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    meta = {
        "last_scan": db.get_setting("last_scan"),
        "result": result,
        "config": cfg,
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "branch": os.environ.get("GITHUB_REF_NAME", "main"),
        "states": config.STATES,
        "uts": config.UNION_TERRITORIES,
        "counts": sources.seed_counts(),
        "professions": {k: {"label": v["label"], "available": v["available"]} for k, v in config.PROFESSIONS.items()},
        "subjects": {k: v["label"] for k, v in config.SUBJECTS.items()},
        "telegram": bool(os.environ.get("TELEGRAM_TOKEN")),
        "email": bool(os.environ.get("SMTP_USER")),
        "built": dt.datetime.now().isoformat(timespec="seconds"),
    }
    (SITE / "data" / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    load_state()
    meta_f = DATA / "meta.json"
    if meta_f.exists():
        db.set_settings({"last_scan": json.loads(meta_f.read_text()).get("last_scan")})
    apply_settings(cfg)
    notify.SYNC = True
    result = None
    reevaluate_subjects()
    dedupe_existing()          # also rebuilds the duplicate-detection index
    if cfg.get("states"):
        sources.sync(cfg["states"], bool(cfg.get("include_national", True)), bool(cfg.get("include_colleges", True)))
        for extra in cfg.get("extra_sources") or []:
            if extra.get("url"):
                sources.add_custom(extra.get("state") or config.NATIONAL, extra.get("name", ""), extra["url"])
        result = scanner.run_scan()
        print("Check finished:", result)
    else:
        print("No states chosen yet - open the dashboard's Setup page to choose them.")
    dump_state()
    meta_f.write_text(json.dumps({"last_scan": db.get_setting("last_scan"), "result": result}, indent=1))
    build_site(cfg, result)
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()

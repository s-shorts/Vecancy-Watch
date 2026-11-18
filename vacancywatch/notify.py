"""In-app, desktop, Telegram and e-mail notifications."""
import datetime as dt
import html
import smtplib
import threading
from email.message import EmailMessage

import requests

from . import db

EVENT_META = {
    "new": ("🆕", "New vacancy"),
    "opened": ("✅", "Applications now open"),
    "extended": ("📅", "Deadline extended"),
    "reminder": ("⏰", "Closing soon"),
}
PRIORITY = {"new": 0, "extended": 1, "opened": 2, "reminder": 3}
SYNC = False  # True = send Telegram/e-mail before returning (used by the GitHub Actions runner)


def _fmt(d):
    if not d:
        return None
    return dt.date.fromisoformat(d).strftime("%d %b %Y")


def date_line(v):
    parts = []
    if v.get("opening_date"):
        parts.append(f"Opens {_fmt(v['opening_date'])}")
    if v.get("deadline"):
        left = (dt.date.fromisoformat(v["deadline"]) - dt.date.today()).days
        suffix = " (today!)" if left == 0 else (f" ({left} days left)" if left > 0 else "")
        parts.append(f"Last date {_fmt(v['deadline'])}{suffix}")
    if not parts:
        parts.append("Last date not found - check the notice")
    return " · ".join(parts)


def facts_line(v):
    """Short summary from the notification: subject match, posts, advt no."""
    import json as _j
    try:
        d = _j.loads(v.get("details") or "{}")
    except ValueError:
        d = {}
    parts = []
    if v.get("subject_match") == "yes" and v.get("subjects"):
        parts.append("✔ " + v["subjects"])
    elif v.get("subject_match") == "maybe":
        parts.append("Subject not confirmed - check the notice")
    if d.get("posts"):
        parts.append(f"{d['posts']} posts in total")
    if d.get("advt_no"):
        parts.append("Advt " + d["advt_no"])
    return " · ".join(parts)


def dispatch(events):
    """events: list of (kind, vacancy_id, extra). Stores in-app notifications and sends one digest."""
    if not events:
        return 0
    best = {}
    for kind, vid, extra in events:
        if vid not in best or PRIORITY[kind] < PRIORITY[best[vid][0]]:
            best[vid] = (kind, extra)
    now = dt.datetime.now().isoformat(timespec="seconds")
    sent = []
    with db.conn() as c:
        ids = list(best)
        rows = {r["id"]: dict(r) for r in c.execute(
            f"SELECT * FROM vacancies WHERE id IN ({','.join('?' * len(ids))})", ids)}
        for vid, (kind, extra) in best.items():
            v = rows.get(vid)
            if not v:
                continue
            icon, label = EVENT_META[kind]
            c.execute("INSERT INTO notifications(created, kind, title, body, url, vacancy_id) VALUES(?,?,?,?,?,?)",
                      (now, kind, f"{label}: {v['institution']}",
                       "\n".join(x for x in (v['title'][:220], facts_line(v), date_line(v)) if x), v["url"], vid))
            sent.append((kind, v))
    if sent:
        if SYNC:
            _send_external(sent)
        else:
            threading.Thread(target=_send_external, args=(sent,), daemon=True).start()
    return len(sent)


def _digest(sent):
    counts = {}
    for kind, _ in sent:
        counts[kind] = counts.get(kind, 0) + 1
    words = {"new": "new", "opened": "now open", "extended": "deadline extended", "reminder": "closing soon"}
    subject = "VacancyWatch: " + ", ".join(f"{n} {words[k]}" for k, n in
                                           sorted(counts.items(), key=lambda kv: PRIORITY[kv[0]]))
    text_lines, html_lines = [], []
    for kind, v in sorted(sent, key=lambda kv: PRIORITY[kv[0]]):
        icon, label = EVENT_META[kind]
        facts = facts_line(v)
        text_lines.append(f"{icon} {label} - {v['institution']} ({v['state']})\n{v['title'][:200]}\n"
                          + (f"{facts}\n" if facts else "") + f"{date_line(v)}\n{v['url']}\n")
        html_lines.append(f"{icon} <b>{html.escape(label)}</b> — {html.escape(v['institution'])} "
                          f"({html.escape(v['state'] or '')})\n{html.escape(v['title'][:200])}\n"
                          + (f"<b>{html.escape(facts)}</b>\n" if facts else "")
                          + f"<i>{html.escape(date_line(v))}</i>\n<a href=\"{html.escape(v['url'])}\">Open notice</a>\n")
    return subject, text_lines, html_lines


def _send_external(sent):
    s = db.all_settings()
    subject, text_lines, html_lines = _digest(sent)
    if s.get("notify_desktop"):
        first = sent[0][1]
        msg = first["institution"] + (f" and {len(sent) - 1} more" if len(sent) > 1 else "")
        send_desktop(subject.replace("VacancyWatch: ", "").capitalize(), msg)
    if s.get("telegram_enabled") and s.get("telegram_token") and s.get("telegram_chat_id"):
        send_telegram(s["telegram_token"], s["telegram_chat_id"], f"<b>{html.escape(subject)}</b>", html_lines)
    if s.get("email_enabled") and s.get("smtp_user") and s.get("email_to"):
        send_email(s, subject, "\n".join(text_lines), "<br>".join(l.replace("\n", "<br>") for l in html_lines))


def send_desktop(title, message):
    try:
        from plyer import notification
        notification.notify(title=title[:60], message=message[:240], app_name="VacancyWatch", timeout=10)
        return True, "Desktop notification sent"
    except Exception as e:  # plyer missing or no notification backend
        return False, f"Desktop notifications unavailable ({type(e).__name__})"


def send_telegram(token, chat_id, header, lines):
    chunks, cur = [], header + "\n\n"
    for line in lines:
        if len(cur) + len(line) > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    try:
        for ch in chunks:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data={"chat_id": chat_id, "text": ch, "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"}, timeout=20)
            if not r.ok:
                return False, f"Telegram error: {r.json().get('description', r.status_code)}"
        return True, "Telegram message sent"
    except Exception as e:
        return False, f"Telegram error: {e}"


def telegram_detect_chat(token):
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
        if not r.get("ok"):
            return None, r.get("description", "Invalid bot token")
        for upd in reversed(r.get("result", [])):
            msg = upd.get("message") or upd.get("channel_post") or {}
            if msg.get("chat", {}).get("id"):
                return str(msg["chat"]["id"]), "Chat found"
        return None, "No messages yet - open your bot in Telegram, press Start / send 'hi', then try again"
    except Exception as e:
        return None, f"Could not reach Telegram: {e}"


def send_email(s, subject, text, html_body):
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = s["smtp_user"]
        msg["To"] = s["email_to"]
        msg.set_content(text)
        msg.add_alternative(f"<div style='font-family:sans-serif;line-height:1.5'>{html_body}</div>", subtype="html")
        port = int(s.get("smtp_port") or 587)
        if port == 465:
            server = smtplib.SMTP_SSL(s["smtp_host"], port, timeout=30)
        else:
            server = smtplib.SMTP(s["smtp_host"], port, timeout=30)
            server.starttls()
        with server:
            server.login(s["smtp_user"], s["smtp_password"])
            server.send_message(msg)
        return True, "E-mail sent"
    except Exception as e:
        return False, f"E-mail error: {e}"

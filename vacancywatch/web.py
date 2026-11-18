import csv
import datetime as dt
import io
import json
import math
import re

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for

from . import config, db, extract, notify, scanner, sources

PER_PAGE = 30
ACTIVE = ("open", "upcoming", "likely_open")


def _d(s):
    return dt.date.fromisoformat(s[:10]) if s else None


def humanize(iso):
    if not iso:
        return "never"
    secs = (dt.datetime.now() - dt.datetime.fromisoformat(iso)).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} days ago"


def decorate(rows):
    today = dt.date.today()
    out = []
    for r in rows:
        v = dict(r)
        dl = _d(v.get("deadline"))
        v["days_left"] = (dl - today).days if dl else None
        v["status_label"] = extract.STATUS_LABELS.get(v["status"], v["status"])
        v["is_new"] = (not v.get("baseline")) and v.get("first_seen", "") >= (today - dt.timedelta(days=3)).isoformat()
        try:
            v["d"] = json.loads(v.get("details") or "{}")
        except ValueError:
            v["d"] = {}
        v["subject_list"] = [x for x in (v.get("subjects") or "").split(", ") if x]
        v["other_list"] = [x for x in (v.get("other_subjects") or "").split(", ") if x]
        try:
            v["alts"] = json.loads(v.get("alt_links") or "[]")
        except ValueError:
            v["alts"] = []
        out.append(v)
    return out


def build_query(args):
    where, params = ["1=1"], []
    status = args.get("status", "")
    if status == "active":
        where.append("status IN ('open','upcoming','likely_open')")
    elif status:
        where.append("status=?")
        params.append(status)
    kind = args.get("kind", "ads")
    if kind == "ads":
        where.append("kind!='update'")
    elif kind == "update":
        where.append("kind='update'")
    for field in ("state", "nature"):
        if args.get(field):
            where.append(f"{field}=?")
            params.append(args[field])
    subj = args.get("subject", "mine")
    if subj == "mine":
        where.append("COALESCE(subject_match,'')!='no'")
    elif subj == "yes":
        where.append("subject_match IN ('yes','any')")
    elif subj == "no":
        where.append("subject_match='no'")
    if args.get("starred"):
        where.append("starred=1")
    if args.get("applied"):
        where.append("applied=1")
    if not args.get("show_hidden"):
        where.append("hidden=0")
    if args.get("q"):
        where.append("(title LIKE ? OR institution LIKE ?)")
        params += [f"%{args['q']}%"] * 2
    if args.get("since"):
        where.append("first_seen>=?")
        params.append(args["since"])
    order = {
        "deadline": "CASE WHEN deadline IS NULL THEN 1 ELSE 0 END, deadline ASC, id DESC",
        "found": "first_seen DESC, id DESC",
        "institution": "institution ASC, first_seen DESC",
    }.get(args.get("sort", "found"), "first_seen DESC, id DESC")
    return " AND ".join(where), params, order


def _split(text):
    return [x.strip() for x in re.split(r"[,;\n]", text or "") if x.strip()]


def create_app():
    app = Flask(__name__)
    app.secret_key = db.secret_key()

    @app.template_filter("fdate")
    def fdate(s):
        d = _d(s)
        return d.strftime("%d %b %Y") if d else "—"

    @app.template_filter("ago")
    def ago(s):
        return humanize(s)

    @app.context_processor
    def inject():
        s = db.all_settings()
        with db.conn() as c:
            unread = c.execute("SELECT COUNT(*) FROM notifications WHERE read=0").fetchone()[0]
        prof = config.PROFESSIONS.get(s.get("profession") or "", {})
        subject_labels = [config.SUBJECTS[k]["label"] for k in (s.get("subjects") or []) if k in config.SUBJECTS] \
            + list(s.get("custom_subjects") or [])
        return dict(unread=unread, settings=s, profession_label=prof.get("label", ""), subject_labels=subject_labels,
                    last_scan_human=humanize(s.get("last_scan")), progress=scanner.PROGRESS,
                    status_labels=extract.STATUS_LABELS)

    @app.before_request
    def require_setup():
        if request.endpoint in ("setup", "static") or (request.endpoint or "").startswith("api_"):
            return None
        if not db.get_setting("profession"):
            return redirect(url_for("setup"))

    # ------------------------------------------------------------- setup
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        s = db.all_settings()
        if request.method == "POST":
            f = request.form
            states = f.getlist("states")
            profession = f.get("profession", "assistant_professor")
            if not states:
                flash("Please choose at least one state.", "error")
                return redirect(url_for("setup"))
            values = {
                "profession": profession,
                "states": states,
                "include_national": bool(f.get("include_national")),
                "include_colleges": bool(f.get("include_colleges")),
                "subjects": f.getlist("subjects"),
                "custom_subjects": _split(f.get("custom_subjects", "")),
                "notify_desktop": bool(f.get("notify_desktop")),
            }
            if f.get("telegram_token") and f.get("telegram_chat_id"):
                values.update(telegram_enabled=True, telegram_token=f["telegram_token"].strip(),
                              telegram_chat_id=f["telegram_chat_id"].strip())
            if f.get("email_to") and f.get("smtp_user") and f.get("smtp_password"):
                values.update(email_enabled=True, email_to=f["email_to"].strip(), smtp_user=f["smtp_user"].strip(),
                              smtp_password=f["smtp_password"].strip())
            db.set_settings(values)
            sources.sync(states, values["include_national"], values["include_colleges"])
            scanner.reevaluate_subjects()
            scanner.start_scan_async()
            flash("All set! The first check of your institutions has started — results appear here as they come in.",
                  "ok")
            return redirect(url_for("dashboard"))
        return render_template("setup.html", professions=config.PROFESSIONS, states=config.STATES,
                               subjects=config.SUBJECTS,
                               uts=config.UNION_TERRITORIES, counts=sources.seed_counts(), s=s, bare=True)

    # ------------------------------------------------------------- dashboard
    @app.route("/")
    def dashboard():
        today = dt.date.today()
        wk = (today + dt.timedelta(days=7)).isoformat()
        week_ago = (today - dt.timedelta(days=7)).isoformat()
        base = "FROM vacancies WHERE hidden=0 AND kind!='update' AND COALESCE(subject_match,'')!='no'"
        with db.conn() as c:
            def count(extra, p=()):
                return c.execute(f"SELECT COUNT(*) {base} AND {extra}", p).fetchone()[0]

            stats = {
                "open": count("status='open'"),
                "closing": count("status='open' AND deadline<=?", (wk,)),
                "upcoming": count("status='upcoming'"),
                "likely": count("status='likely_open'"),
                "new7": count("baseline=0 AND first_seen>=? AND status!='closed'", (week_ago,)),
                "total": c.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0],
            }
            closing = decorate(c.execute(f"SELECT * {base} AND status='open' ORDER BY deadline LIMIT 8"))
            recent = decorate(c.execute(
                f"SELECT * {base} AND status IN ('open','upcoming','likely_open') "
                "ORDER BY baseline ASC, first_seen DESC, id DESC LIMIT 10"))
            upcoming = decorate(c.execute(f"SELECT * {base} AND status='upcoming' ORDER BY opening_date LIMIT 6"))
            src = c.execute("SELECT COUNT(*) AS n, SUM(CASE WHEN last_status='error' THEN 1 ELSE 0 END) AS err, "
                            "SUM(CASE WHEN last_status IS NULL THEN 1 ELSE 0 END) AS pending "
                            "FROM sources WHERE enabled=1").fetchone()
        return render_template("dashboard.html", stats=stats, closing=closing, recent=recent, upcoming=upcoming,
                               src=src, active="dashboard")

    # ------------------------------------------------------------- vacancies / history
    @app.route("/vacancies")
    def vacancies():
        args = request.args.to_dict()
        where, params, order = build_query(args)
        page = max(1, int(args.get("page", 1) or 1))
        with db.conn() as c:
            total = c.execute(f"SELECT COUNT(*) FROM vacancies WHERE {where}", params).fetchone()[0]
            rows = decorate(c.execute(f"SELECT * FROM vacancies WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                                      params + [PER_PAGE, (page - 1) * PER_PAGE]))
            states = [r[0] for r in c.execute("SELECT DISTINCT state FROM vacancies ORDER BY state")]
        pages = max(1, math.ceil(total / PER_PAGE))
        return render_template("vacancies.html", rows=rows, total=total, page=page, pages=pages, args=args,
                               states=states, active="vacancies")

    @app.route("/vacancies.csv")
    def vacancies_csv():
        where, params, order = build_query(request.args.to_dict())
        with db.conn() as c:
            rows = c.execute(f"SELECT * FROM vacancies WHERE {where} ORDER BY {order}", params).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        cols = ["institution", "state", "title", "subjects", "subject_match", "status", "nature", "kind", "opening_date", "deadline",
                "posted_date", "first_seen", "starred", "applied", "url", "page_url"]
        w.writerow(cols)
        for r in rows:
            w.writerow([r[k] for k in cols])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=vacancies.csv"})

    @app.post("/api/vacancy/<int:vid>/<field>")
    def api_vacancy_toggle(vid, field):
        if field not in ("starred", "applied", "hidden"):
            return jsonify(error="bad field"), 400
        with db.conn() as c:
            row = c.execute(f"SELECT {field} FROM vacancies WHERE id=?", (vid,)).fetchone()
            if not row:
                return jsonify(error="not found"), 404
            new = 0 if row[0] else 1
            c.execute(f"UPDATE vacancies SET {field}=? WHERE id=?", (new, vid))
        return jsonify(value=new)

    # ------------------------------------------------------------- notifications
    @app.route("/notifications")
    def notifications():
        with db.conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 300")]
            c.execute("UPDATE notifications SET read=1 WHERE read=0")
        return render_template("notifications.html", rows=rows, meta=notify.EVENT_META, active="notifications")

    # ------------------------------------------------------------- sources
    @app.route("/sources")
    def sources_page():
        s = db.all_settings()
        show = request.args.get("show", "watching")
        with db.conn() as c:
            q = "SELECT * FROM sources"
            if show == "watching":
                q += " WHERE enabled=1 OR custom=1"
            elif show == "colleges":
                q += " WHERE discovered=1 OR type LIKE '%college%'"
            elif show == "errors":
                q += " WHERE enabled=1 AND last_status='error'"
            rows = [dict(r) for r in c.execute(q + " ORDER BY state, custom DESC, type, name")]
        grouped = {}
        for r in rows:
            grouped.setdefault(r["state"], []).append(r)
        state_opts = list(s.get("states") or []) + [config.NATIONAL]
        return render_template("sources.html", grouped=grouped, show=show, state_opts=state_opts,
                               total=len(rows), active="sources")

    @app.post("/sources/add")
    def sources_add():
        url = request.form.get("url", "").strip()
        if not url:
            flash("Please enter the website address.", "error")
            return redirect(url_for("sources_page"))
        sid = sources.add_custom(request.form.get("state") or config.NATIONAL, request.form.get("name", ""), url)
        scanner.start_scan_async([sid])
        flash("Source added and being checked now.", "ok")
        return redirect(url_for("sources_page"))

    @app.post("/sources/<int:sid>/delete")
    def sources_delete(sid):
        with db.conn() as c:
            c.execute("DELETE FROM sources WHERE id=? AND custom=1", (sid,))
        flash("Source removed. Vacancies already found stay in your history.", "ok")
        return redirect(url_for("sources_page"))

    @app.post("/api/sources/<int:sid>/toggle")
    def api_source_toggle(sid):
        return jsonify(enabled=sources.toggle(sid))

    @app.post("/api/sources/<int:sid>/scan")
    def api_source_scan(sid):
        return jsonify(started=scanner.start_scan_async([sid]))

    # ------------------------------------------------------------- settings
    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        if request.method == "POST":
            f = request.form
            states = f.getlist("states")
            if not states:
                flash("Please keep at least one state selected.", "error")
                return redirect(url_for("settings_page"))
            try:
                interval = min(48.0, max(1.0, float(f.get("scan_interval_hours") or 6)))
            except ValueError:
                interval = 6.0
            days = sorted({int(x) for x in f.get("reminder_days", "").replace(" ", "").split(",") if x.isdigit()},
                          reverse=True)
            values = {
                "states": states,
                "include_national": bool(f.get("include_national")),
                "include_colleges": bool(f.get("include_colleges")),
                "subjects": f.getlist("subjects"),
                "custom_subjects": _split(f.get("custom_subjects", "")),
                "scan_interval_hours": interval,
                "reminder_days": days,
                "reminder_scope": f.get("reminder_scope", "all"),
                "notify_likely_open": bool(f.get("notify_likely_open")),
                "notify_desktop": bool(f.get("notify_desktop")),
                "telegram_enabled": bool(f.get("telegram_enabled")),
                "telegram_token": f.get("telegram_token", "").strip(),
                "telegram_chat_id": f.get("telegram_chat_id", "").strip(),
                "email_enabled": bool(f.get("email_enabled")),
                "smtp_host": f.get("smtp_host", "smtp.gmail.com").strip(),
                "smtp_port": int(f.get("smtp_port") or 587),
                "smtp_user": f.get("smtp_user", "").strip(),
                "email_to": f.get("email_to", "").strip(),
            }
            if f.get("smtp_password"):
                values["smtp_password"] = f["smtp_password"].strip()
            old = db.all_settings()
            old_states = set(old.get("states") or [])
            db.set_settings(values)
            sources.sync(states, values["include_national"], values["include_colleges"])
            if (old.get("subjects"), old.get("custom_subjects")) != (values["subjects"], values["custom_subjects"]):
                scanner.reevaluate_subjects()
            if set(states) - old_states:
                scanner.start_scan_async()
                flash("Settings saved. Checking the newly added states now.", "ok")
            else:
                flash("Settings saved.", "ok")
            return redirect(url_for("settings_page"))
        return render_template("settings.html", states=config.STATES, uts=config.UNION_TERRITORIES,
                               subjects=config.SUBJECTS,
                               counts=sources.seed_counts(), active="settings")

    @app.post("/api/test/<channel>")
    def api_test(channel):
        s = db.all_settings()
        body = request.get_json(silent=True) or {}
        s.update({k: v for k, v in body.items() if v})
        if channel == "desktop":
            ok, msg = notify.send_desktop("VacancyWatch test", "Desktop notifications are working.")
        elif channel == "telegram":
            ok, msg = notify.send_telegram(s.get("telegram_token"), s.get("telegram_chat_id"),
                                           "<b>VacancyWatch</b>", ["✅ Telegram alerts are working."])
        elif channel == "email":
            ok, msg = notify.send_email(s, "VacancyWatch test", "E-mail alerts are working.",
                                        "✅ E-mail alerts are working.")
        else:
            return jsonify(ok=False, message="unknown channel"), 400
        return jsonify(ok=ok, message=msg)

    @app.post("/api/telegram/detect")
    def api_telegram_detect():
        token = (request.get_json(silent=True) or {}).get("token") or db.get_setting("telegram_token")
        chat_id, msg = notify.telegram_detect_chat(token or "")
        return jsonify(chat_id=chat_id, message=msg)

    # ------------------------------------------------------------- scanning / status
    @app.post("/api/scan")
    def api_scan():
        return jsonify(started=scanner.start_scan_async())

    @app.get("/api/status")
    def api_status():
        since = int(request.args.get("since", 0) or 0)
        with db.conn() as c:
            unread = c.execute("SELECT COUNT(*) FROM notifications WHERE read=0").fetchone()[0]
            max_id = c.execute("SELECT COALESCE(MAX(id), 0) FROM notifications").fetchone()[0]
            fresh = [dict(r) for r in c.execute(
                "SELECT id, title, body, url FROM notifications WHERE id>? ORDER BY id LIMIT 5", (since,))] \
                if since else []
        return jsonify(progress=scanner.PROGRESS, unread=unread, max_id=max_id, fresh=fresh,
                       last_scan=humanize(db.get_setting("last_scan")))

    return app

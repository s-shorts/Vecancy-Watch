"""End-to-end test against fake university + college websites served locally.

Run:  python -m pytest -q
"""
import datetime as dt
import functools
import http.server
import os
import tempfile
import threading
from pathlib import Path

import pytest

os.environ["VACANCYWATCH_HOME"] = tempfile.mkdtemp()
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from vacancywatch import config, db, extract, scanner, sources, web  # noqa: E402

T = dt.date.today()


def f(days, fmt="%d.%m.%Y"):
    return (T + dt.timedelta(days=days)).strftime(fmt)


def make_pdf(path: Path, lines=None, scanned=False):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    if scanned:  # an image-only page, like a scanned notice
        c.setFillColorRGB(0.2, 0.2, 0.2)
        for i in range(30):
            c.rect(60, 780 - i * 22, 400, 10, fill=1, stroke=0)
    else:
        y = 800
        for line in lines:
            c.drawString(40, y, line)
            y -= 16
    c.save()


def write_site(root: Path, port, extra_row="", extended=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(f"""<html><body>
<nav><ul><li><a href="recruitment.html">Recruitment</a></li><li><a href="faculty.html">Faculty Recruitment</a></li>
<li><a href="placement.html">Training &amp; Placement</a></li><li><a href="colleges.html">Constituent Colleges</a></li></ul></nav>
<div class="news"><marquee>
<a href="adv/asst-prof-2026.pdf">Advertisement for the post of Assistant Professor (Regular) - Last date {f(32)}</a>
</marquee></div></body></html>""")
    (root / "colleges.html").write_text(f"""<html><body><h1>Constituent Colleges</h1><ul>
<li><a href="http://localhost:{port}/cmp/index.html">C.M.P. Degree College</a></li>
<li><a href="https://www.facebook.com/university">Facebook</a></li></ul></body></html>""")
    last = f(40) if extended else f(27)
    (root / "recruitment.html").write_text(f"""<html><body><h2>Recruitment</h2><table>
<tr><td>1</td><td>Recruitment of Assistant Professors in various departments, Advt. No. 05/{T.year}</td>
    <td>Online application: {f(-8)} to {last}</td><td><a href="adv05.pdf">Download</a></td></tr>
<tr><td>2</td><td>Result of interview for Assistant Professor (Physics)</td><td>{f(-70)}</td><td><a href="res.pdf">View</a></td></tr>
<tr><td>3</td><td>Walk-in interview for Guest Faculty / Assistant Professor (Contractual) on {f(5)}</td><td><a href="guest.pdf">Details</a></td></tr>
<tr><td>4</td><td>Advertisement for Assistant Professor 2024, last date 31.01.2024</td><td><a href="old.pdf">Download</a></td></tr>
<tr><td>5</td><td>Assistant Professor recruitment (Commerce) - online application starts from {f(13)}, last date {f(43)}</td><td><a href="up.pdf">PDF</a></td></tr>
<tr><td>6</td><td>Recruitment of Non-Teaching staff (Clerk), last date {f(20)}</td><td><a href="nt.pdf">PDF</a></td></tr>
{extra_row}
</table></body></html>""")
    (root / "faculty.html").write_text(f"""<html><body>
<p>Notification: Applications are invited for the post of Assistant Professor on regular basis in the Department of
English. Last date for receipt of applications: {f(12, '%d %B %Y')}.
<a href="details.html">Read more</a></p></body></html>""")
    # ---- a constituent college with a recruitment portal like recruitment.cmpcollege.ac.in
    cmp = root / "cmp"
    cmp.mkdir(exist_ok=True)
    (cmp / "index.html").write_text("""<html><body><h1>C.M.P. Degree College</h1>
<a href="recruitment.html">Recruitment Portal</a> <a href="admission.html">Admission notification 2026</a></body></html>""")
    (cmp / "recruitment.html").write_text(f"""<html><body><h2>Recruitment</h2><table>
<tr><td>Advertisement No. CMP/Rec./02/{T.year} dated {f(-1)}</td><td><a href="advt02.pdf">Download</a></td></tr>
<tr><td>Advertisement No. CMP/NT/01/{T.year}</td><td><a href="nt01.pdf">Download</a></td></tr>
<tr><td>Advertisement No. CMP/Rec./01/{T.year}</td><td><a href="advt01.pdf">Download</a></td></tr>
<tr><td>Advertisement No. CMP/Rec./03/{T.year}</td><td><a href="advt03.pdf">Download</a></td></tr>
</table>
<div class="post"><p>Designation Assistant Professor Location Prayagraj, Uttar Pradesh Total posts 2 Application fee Calculated
 from your category and prior-applicant declaration Pay scale As per UGC / State Government norms Last date {f(20, '%d %b %Y')}</p></div>
<div class="post"><p>Designation Assistant Professor Location Prayagraj, Uttar Pradesh Total posts 5 Application fee Calculated
 from your category and prior-applicant declaration Pay scale As per UGC / State Government norms Last date {f(20, '%d %b %Y')}</p></div>
<ul class="adv"><li><a href="advt02.pdf">View advertisement</a></li></ul>
<a href="main.html">College website</a>
</body></html>""")
    (cmp / "main.html").write_text(f"""<html><body><h3>Latest news</h3><ul>
<li><a href="/cmp/advt02.pdf?v=2">Online applications are invited for vacant post of Assistant Professor in various subjects.
 Read both advertisements carefully before applying.</a></li></ul></body></html>""")
    make_pdf(cmp / "advt02.pdf", [
        "C.M.P. DEGREE COLLEGE, PRAYAGRAJ (A Constituent PG College of University of Allahabad)",
        f"Advertisement No. CMP/Rec./02/Asst. Prof./{T.year} dated {f(-1, '%d/%m/%Y')}",
        "Online applications are invited for 11 posts of Assistant Professor in Law,",
        "Political Science, Sociology, Computer Science and Statistics.",
        "S.No  Subject            UR  OBC  SC  Total",
        "4     Computer Science   1   1    0   02",
        "Pay: Academic Level 10, Pay Matrix Rs 57,700/- with admissible allowances.",
        "Application Fee: General/OBC Rs 1500/- SC/ST Rs 1000/-",
        "Eligibility as per UGC Regulations 2018 (NET / Ph.D.). Use of modern teaching aids is desirable.",
        "Apply on the recruitment portal https://recruitment.cmpcollege.ac.in",
        f"Last date for submission of online application: {f(20)}",
        f"Last date for receipt of hard copy of the online application by speed post: {f(27)}",
    ])
    make_pdf(cmp / "nt01.pdf", ["C.M.P. Degree College, Prayagraj - Advertisement No. CMP/NT/01",
                                "Recruitment of Junior Assistant and Laboratory Assistant (Non-Teaching) posts.",
                                "Applications are invited from eligible candidates. Last date " + f(15)])
    make_pdf(cmp / "advt01.pdf", ["Applications are invited for 3 posts of Assistant Professor in Hindi and Law.",
                                  "Last date for online application: " + f(18)])
    make_pdf(cmp / "advt03.pdf", scanned=True)


@pytest.fixture(scope="module")
def site():
    root = Path(tempfile.mkdtemp()) / "site"
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    write_site(root, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield root, port, f"http://127.0.0.1:{port}/index.html"
    srv.shutdown()


def test_dates_and_details():
    d = extract.extract_dates("Online application: 10.11.2026 to 15.12.2026")
    assert d["opening"] == dt.date(2026, 11, 10) and d["deadline"] == dt.date(2026, 12, 15)
    d = extract.extract_dates("Last date for receipt: 5th Dec, 2026. Dated: 01/11/2026")
    assert d["deadline"] == dt.date(2026, 12, 5) and d["posted"] == dt.date(2026, 11, 1)
    d = extract.extract_dates("Last date of online application 08.12.2026. Hard copy by speed post on or before 15.12.2026")
    assert d["deadline"] == dt.date(2026, 12, 8)
    assert extract.classify_kind("Result of interview for Assistant Professor") == "update"
    assert extract.classify_nature("Guest Faculty walk-in") == "Contract/Guest"
    S = config.compiled_subjects(config.DEFAULT_SUBJECTS)
    assert extract.find_subjects("Assistant Professor (CSE) and Dept. of IT", S) == ["Computer Science", "Information Technology"]
    assert extract.find_subjects("it is informed that the Department of English", S) == []
    assert extract.find_subjects("सहायक आचार्य कम्प्यूटर विज्ञान", S) == ["Computer Science"]


def test_full_flow(site):
    root, port, url = site
    db.init()
    db.set_settings({"profession": "assistant_professor", "states": ["Goa"], "include_national": False,
                     "include_colleges": True, "notify_desktop": False, "reminder_days": [7, 3, 1]})
    sources.sync(["Goa"], False)
    with db.conn() as c:  # only scan the fake sites in tests
        c.execute("UPDATE sources SET enabled=0")
    sid = sources.add_custom("Goa", "Fake Central University", url)
    with db.conn() as c:
        c.execute("UPDATE sources SET type='Central University' WHERE id=?", (sid,))

    r = scanner.run_scan()
    assert r["ok"] == 1, r
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM vacancies")]
        notes = [dict(n) for n in c.execute("SELECT * FROM notifications")]
        colleges = [dict(r) for r in c.execute("SELECT * FROM sources WHERE discovered=1")]
    for v in rows:
        print(f"{v['status']:12} {v['kind']:13} {v['subject_match']:6} {v['deadline']} | {v['title'][:60]}")
    by = lambda s: next(v for v in rows if s in v["title"])
    assert not any("Non-Teaching" in v["title"] for v in rows), "non-teaching posts must not match"
    assert not any(v["title"].strip() in ("Faculty Recruitment", "Recruitment") for v in rows)
    assert by("Advt. No. 05")["status"] == "open"
    assert by("Advt. No. 05")["opening_date"] == (T - dt.timedelta(days=8)).isoformat()
    assert by("Result of interview")["kind"] == "update"
    assert by("Guest Faculty")["nature"] == "Contract/Guest" and by("Guest Faculty")["status"] == "open"
    assert by("2024")["status"] == "closed"
    assert by("Commerce")["status"] == "upcoming" and by("Commerce")["subject_match"] == "no"
    assert by("Department of")["subject_match"] == "no"
    assert by("Advt. No. 05")["subject_match"] == "maybe"
    # already-open vacancies are alerted on the first check, but not other subjects / closed / results
    assert sorted(n["title"] for n in notes) == ["New vacancy: Fake Central University"] * 3, notes
    # the constituent college was found on the university website
    assert [c["name"] for c in colleges] == ["C.M.P. Degree College"], colleges
    assert colleges[0]["enabled"] == 1

    # ---- second check: the college portal is scanned now
    r = scanner.run_scan()
    with db.conn() as c:
        cmp_rows = [dict(r) for r in c.execute("SELECT * FROM vacancies WHERE institution='C.M.P. Degree College'")]
        new_notes = [dict(n) for n in c.execute("SELECT * FROM notifications WHERE id>?", (len(notes),))]
        rejected = c.execute("SELECT COUNT(*) FROM rejected").fetchone()[0]
    for v in cmp_rows:
        print(f"CMP {v['subject_match']:6} {v['notice_read']:8} {v['deadline']} | {v['title'][:50]} | {v['details'][:160]}")
    import json
    cs_cards = [v for v in cmp_rows if "/02/" in v["title"] or "Designation" in v["title"] or "View" in v["title"]
                or "various subjects" in v["title"]]
    assert len(cs_cards) == 1, [v["title"] for v in cs_cards]
    cs = cs_cards[0]
    alts = json.loads(cs["alt_links"])
    assert len(alts) >= 2, alts   # the two "Designation … Total posts" blocks are attached to this card
    assert cs["subject_match"] == "yes" and cs["subjects"] == "Computer Science", "no false 'Data Science' from 'aids'"
    assert cs["deadline"] == (T + dt.timedelta(days=20)).isoformat(), "online last date, not hard-copy date"
    assert cs["status"] == "open" and cs["notice_read"] == "pdf"
    d = json.loads(cs["details"])
    assert d["posts"] == 11 and d["advt_no"].startswith("CMP/Rec./02") and "Level 10" in d["pay"]
    assert d["hardcopy_by"] == (T + dt.timedelta(days=27)).isoformat()
    assert any("Computer Science" in sn for sn in d["snippets"])
    assert "Law" in cs["other_subjects"]
    assert not any("/NT/" in v["title"] for v in cmp_rows), "non-teaching advert must be dropped after reading it"
    assert rejected >= 1
    assert next(v for v in cmp_rows if "/01/" in v["title"])["subject_match"] == "no"
    scanned = next(v for v in cmp_rows if "/03/" in v["title"])
    assert scanned["notice_read"] == "scanned" and scanned["subject_match"] == "maybe"
    # undated notices seen on a site's very first check may be old: saved to history, no alert
    alerted = {n["vacancy_id"] for n in new_notes}
    assert alerted == {cs["id"]}, new_notes
    assert "Computer Science" in next(n for n in new_notes if n["vacancy_id"] == cs["id"])["body"]

    # ---- third check: a new CS advert + an extended deadline
    write_site(root, port, extra_row=f'<tr><td>7</td><td>Assistant Professor (Computer Science) - last date {f(25)}</td>'
                                     f'<td><a href="cs.pdf">PDF</a></td></tr>', extended=True)
    rec = root / "cmp" / "recruitment.html"
    make_pdf(root / "cmp" / "advt04.pdf", scanned=True)
    rec.write_text(rec.read_text().replace("</table>", f'<tr><td>Advertisement No. CMP/Rec./04/{T.year}</td>'
                                                       f'<td><a href="advt04.pdf">Download</a></td></tr></table>'))
    before = len(notes) + len(new_notes)
    scanner.run_scan()
    with db.conn() as c:
        kinds = sorted(n["kind"] for n in c.execute("SELECT kind FROM notifications WHERE id>?", (before,)))
    assert kinds == ["extended", "new", "new"], kinds  # + a new scanned advert: "subject not confirmed"

    # ---- time passes: status refresh + reminder
    with db.conn() as c:
        c.execute("UPDATE vacancies SET opening_date=?, subject_match='yes' WHERE title LIKE '%Commerce%'",
                  ((T - dt.timedelta(days=1)).isoformat(),))
        c.execute("UPDATE vacancies SET deadline=? WHERE title LIKE '%(Computer Science)%'",
                  ((T + dt.timedelta(days=2)).isoformat(),))
    ev = scanner.maintenance()
    assert ("opened" in [e[0] for e in ev]) and ("reminder" in [e[0] for e in ev]), ev
    assert scanner.maintenance() == [], "reminders must be sent only once"

    # ---- changing subjects re-evaluates stored notices
    db.set_settings({"subjects": [], "custom_subjects": ["Sociology"]})
    scanner.reevaluate_subjects()
    with db.conn() as c:
        assert c.execute("SELECT subject_match FROM vacancies WHERE id=?", (cs["id"],)).fetchone()[0] == "yes"
        assert c.execute("SELECT subject_match FROM vacancies WHERE title LIKE '%/01/%'").fetchone()[0] == "no"
    db.set_settings({"subjects": config.DEFAULT_SUBJECTS, "custom_subjects": []})
    scanner.reevaluate_subjects()

    # ---- web pages render
    app = web.create_app()
    cl = app.test_client()
    for path in ["/", "/vacancies", "/vacancies?status=active&sort=deadline", "/vacancies?kind=all&q=Physics",
                 "/vacancies?subject=no", "/vacancies?subject=all", "/notifications", "/sources",
                 "/sources?show=colleges", "/sources?show=all", "/settings", "/setup", "/vacancies.csv", "/api/status"]:
        resp = cl.get(path)
        assert resp.status_code == 200, (path, resp.status_code)
    page = cl.get("/vacancies?status=active").data.decode()
    assert "CMP/Rec./02" in page and "Academic Level 10" in page and "What it says about your subject" in page
    assert "Hindi and Law" not in page, "other-subject notices are hidden by default"
    assert cl.post(f"/api/vacancy/{cs['id']}/starred").get_json()["value"] == 1


def test_cleanup_old_duplicates(tmp_path, monkeypatch):
    """History saved by the earlier version (like the user's screenshot) is merged on start-up."""
    import json
    monkeypatch.setenv("VACANCYWATCH_HOME", str(tmp_path))
    db.init()
    db.set_settings({"profession": "assistant_professor", "states": ["Uttar Pradesh"]})
    with db.conn() as c:
        c.execute("INSERT INTO sources(id, state, name, type, url, enabled) VALUES(1,'Uttar Pradesh','CMP Portal','x',"
                  "'https://recruitment.cmpcollege.ac.in',1)")
        c.execute("INSERT INTO sources(id, state, name, type, url, enabled) VALUES(2,'Uttar Pradesh','CMP','x',"
                  "'https://cmpcollege.ac.in',1)")
        pdf_text = ("Advertisement No. CMP/Rec./02/Asst. Prof./2026 dated 17/11/2026. 11 posts of Assistant Professor in "
                    "Law, Computer Science. Last date for submission of online application: 08.12.2026. Last date for "
                    "receipt of hard copy by speed post: 15.12.2026. " + "x" * 400)
        rows = [  # id, source, title, url, page_url, deadline, notice
            (1, 1, "Designation Assistant Professor Location Prayagraj, Uttar Pradesh Total posts 2 Last date 08 Dec 2026",
             "https://recruitment.cmpcollege.ac.in/", "https://recruitment.cmpcollege.ac.in/", "2026-12-08", None),
            (2, 1, "Designation Assistant Professor Location Prayagraj, Uttar Pradesh Total posts 5 Last date 08 Dec 2026",
             "https://recruitment.cmpcollege.ac.in/", "https://recruitment.cmpcollege.ac.in/", "2026-12-08", None),
            (3, 1, "View advertisement", "https://recruitment.cmpcollege.ac.in/adv02.pdf",
             "https://recruitment.cmpcollege.ac.in/", "2026-12-15", pdf_text),
            (4, 2, "Online applications are invited for vacant post of Assistant Professor in various subjects.",
             "https://cmpcollege.ac.in/news/adv02.pdf", "https://cmpcollege.ac.in/", "2026-12-15", pdf_text),
            (5, 2, "View advertisement", "https://cmpcollege.ac.in/wp/adv02.pdf", "https://cmpcollege.ac.in/",
             "2026-12-15", pdf_text),
        ]
        for vid, sid, title, url, page, dl, text in rows:
            c.execute("INSERT INTO vacancies(id, uid, source_id, state, institution, title, url, page_url, kind, status, "
                      "deadline, first_seen, baseline, notice_read, details, subject_match, subjects) "
                      "VALUES(?,?,?,?,?,?,?,?,'advertisement','open',?,'2026-11-18T10:00:00',0,?,?,?,?)",
                      (vid, f"u{vid}", sid, "Uttar Pradesh", "CMP", title, url, page, dl, "pdf" if text else None,
                       json.dumps({"advt_no": "CMP/Rec./02/Asst. Prof./2026"} if text else {}),
                       "yes" if text else "maybe", "Computer Science" if text else ""))
            if text:
                c.execute("INSERT INTO notices(vacancy_id, text) VALUES(?, ?)", (vid, text))
        c.execute("INSERT INTO notifications(created, kind, title, body, vacancy_id) VALUES('x','new','t','b',5)")
    merged = scanner.dedupe_existing()
    with db.conn() as c:
        left = [dict(r) for r in c.execute("SELECT * FROM vacancies")]
        assert c.execute("SELECT vacancy_id FROM notifications").fetchone()[0] == left[0]["id"]
    assert merged == 4 and len(left) == 1, [v["title"] for v in left]
    v = left[0]
    assert v["deadline"] == "2026-12-08", "online last date, not the hard-copy date"
    assert "View advertisement" not in v["title"]
    assert len(json.loads(v["alt_links"])) >= 3
    assert scanner.dedupe_existing() == 0, "running it again changes nothing"

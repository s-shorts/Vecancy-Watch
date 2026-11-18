"""Text analysis: finding dates (opening / last date / published), and classifying notices."""
import datetime as dt
import re

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])")

DATE_RE = re.compile(
    r"(?<![\d/.-])(?P<d1>[0-3]?\d)\s?[./-]\s?(?P<m1>[01]?\d)\s?[./-]\s?(?P<y1>(?:19|20)\d{2}|\d{2})(?![\d])"
    r"|(?<!\d)(?P<d2>[0-3]?\d)(?:st|nd|rd|th)?[\s.,-]*(?:of\s+)?(?P<m2>" + _MON + r")\.?[\s.,'-]*(?P<y2>(?:19|20)\d{2})(?!\d)"
    r"|\b(?P<m3>" + _MON + r")\.?\s+(?P<d3>[0-3]?\d)(?:st|nd|rd|th)?,?\s*(?P<y3>(?:19|20)\d{2})(?!\d)",
    re.I,
)

DEADLINE_RE = re.compile(
    r"last\s*date|closing\s*date|close\s*date|\bcloses?\b|\bclosing\b|dead\s*line|due\s*date|on\s*or\s*before|"
    r"end(?:ing)?\s*date|last\s*day|apply\s*(?:by|before)|\btill\b|\buntil\b|\bupto\b|\bup\s*to\b|"
    r"\bbefore\b|walk[\s-]*in|extended\s*(?:up\s*)?to|receipt\s*of\s*applications?|"
    r"(?:must|should|shall)\s+reach|reach\s+(?:by|on|before)", re.I)
OPEN_RE = re.compile(
    r"start(?:ing)?\s*date|opening\s*date|\bopens?\b|\bopened\b|commenc\w*|\bbegins?\b|\bstarts?\b|"
    r"\bfrom\b|available\s*from|w\.?\s?e\.?\s?f", re.I)
POSTED_RE = re.compile(
    r"\bdated?\b|published|posted|uploaded|issued|notified|notification\s*date|advt\.?\s*date|"
    r"date\s*of\s*(?:advertisement|publication|issue)", re.I)
_HARDCOPY_CTX = re.compile(r"hard\s*copy|print[\s-]*out|physical\s+copy|speed\s*post|registered\s*post|by\s+post|"
                           r"reach\s+(?:the|this)\s+(?:office|college|university)", re.I)
_ONLINE_CTX = re.compile(r"online|portal|website|e-?mail|registration", re.I)


def _is_hardcopy(window):
    """True when the nearest wording before a date is about the hard copy / post, not the online form."""
    hc = [m.end() for m in _HARDCOPY_CTX.finditer(window)]
    on = [m.end() for m in _ONLINE_CTX.finditer(window)]
    return bool(hc) and (not on or max(hc) > max(on))


RANGE_RE = re.compile(r"^[\s:,()]*(?:to|till|until|upto|up\s*to|-|–|—|and)[\s:,()]*$", re.I)

UPDATE_RE = re.compile(
    r"\bresults?\b|answer\s*keys?|admit\s*cards?|call\s*letters?|short[\s-]*list(?:ed)?|"
    r"list\s+of\s+(?:eligible|ineligible|candidates|selected|provisionally|shortlisted)|"
    r"interview\s+schedule|schedule\s+(?:of|for)\s+(?:the\s+)?(?:interview|presentation)|"
    r"(?:selection|merit|wait(?:ing)?)\s+list|score\s*card|\bmarks\b|eligible\s+candidates|"
    r"not\s+eligible|rejected|screening\s+(?:result|test)|syllabus|\bminutes\b|scrutiny", re.I)
CORR_RE = re.compile(r"corrigendum|addendum|extension\s+of|\bextended\b|\brevised\b|errata|postpone|re-?open", re.I)
CONTRACT_RE = re.compile(
    r"\bguest\b|contract|ad[\s-]*hoc|temporary|visiting|part[\s-]*time|deputation|tenure\s+basis|"
    r"teaching\s+associate|honorarium|walk[\s-]*in", re.I)

STATUS_LABELS = {
    "open": "Open",
    "upcoming": "Opening soon",
    "likely_open": "Possibly open",
    "closed": "Closed",
    "unknown": "Date unclear",
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _mk(y, m, d):
    y = int(y)
    if y < 100:
        y += 2000
    try:
        return dt.date(y, int(m), int(d))
    except ValueError:
        return None


def find_dates(text: str):
    out = []
    max_year = dt.date.today().year + 2
    for m in DATE_RE.finditer(text):
        if m.group("d1"):
            d = _mk(m.group("y1"), m.group("m1"), m.group("d1"))
        elif m.group("d2"):
            d = _mk(m.group("y2"), _MONTHS[m.group("m2")[:3].lower()], m.group("d2"))
        else:
            d = _mk(m.group("y3"), _MONTHS[m.group("m3")[:3].lower()], m.group("d3"))
        if d and 2015 <= d.year <= max_year:
            out.append((d, m.start(), m.end()))
    return out


def _closest_label(window: str):
    """Return 'deadline' / 'opening' / 'posted' / None for the label nearest to the end of window."""
    best, best_end, prio = None, -1, {"deadline": 3, "opening": 2, "posted": 1}
    for name, rx in (("deadline", DEADLINE_RE), ("opening", OPEN_RE), ("posted", POSTED_RE)):
        for m in rx.finditer(window):
            if m.end() > best_end or (m.end() == best_end and prio[name] > prio[best]):
                best, best_end = name, m.end()
    return best


def extract_dates(text: str, labelled_only: bool = False):
    """Find opening date, deadline and posted date in a piece of text.

    labelled_only=True ignores dates that have no recognisable label (used for long PDFs).
    """
    text = clean(text)
    found = find_dates(text)
    deadlines, openings, posted, unlabeled, hardcopy = [], [], [], [], []
    prev_end, prev_label, prev_date = 0, None, None
    for d, start, end in found:
        window = text[max(prev_end, start - 80):start]
        label = _closest_label(window)
        if label is None and prev_date is not None and RANGE_RE.match(text[prev_end:start] or "x"):
            # "10.11.2026 to 15.12.2026" -> range
            label = "deadline"
            if prev_label in (None, "posted"):
                if prev_date in unlabeled:
                    unlabeled.remove(prev_date)
                if prev_date in posted:
                    posted.remove(prev_date)
                openings.append(prev_date)
        if label == "deadline" and _is_hardcopy(text[max(prev_end, start - 150):start]):
            hardcopy.append(d)
        elif label == "deadline":
            deadlines.append(d)
        elif label == "opening":
            openings.append(d)
        elif label == "posted":
            posted.append(d)
        else:
            unlabeled.append(d)
        prev_end, prev_label, prev_date = end, label, d

    deadline = max(deadlines) if deadlines else (max(hardcopy) if hardcopy else None)
    opening = min(openings) if openings else None
    if opening and deadline and opening > deadline:
        opening = None
    pub = None
    if not labelled_only:
        pub = posted[0] if posted else (min(unlabeled) if unlabeled else None)
    return {"opening": opening, "deadline": deadline, "posted": pub}


def classify_kind(text: str) -> str:
    if UPDATE_RE.search(text):
        return "update"
    if CORR_RE.search(text):
        return "corrigendum"
    return "advertisement"


def classify_nature(text: str) -> str:
    return "Contract/Guest" if CONTRACT_RE.search(text) else "Regular"


def compute_status(opening, deadline, posted, seen_ref, today=None, fresh_days=45):
    """seen_ref = date we first saw the notice (None for notices found on a site's very first scan)."""
    today = today or dt.date.today()
    if opening and opening > today:
        return "upcoming"
    if deadline:
        return "open" if deadline >= today else "closed"
    ref = posted or seen_ref
    if ref and (today - ref).days <= fresh_days:
        return "likely_open"
    return "unknown"


# ---------------------------------------------------------------- reading the notification itself

NON_TEACHING_RE = re.compile(
    r"non[\s-]*teaching|\bclerk|\bpeon\b|stenograph|\bdriver\b|lab(?:oratory)?\s*(?:assistant|attendant)|"
    r"multi[\s-]*tasking|\bMTS\b|junior\s*assistant|office\s*assistant|research\s*(?:assistant|associate|fellow)|"
    r"\bJRF\b|\bSRF\b|project\s*(?:assistant|associate|fellow)|field\s*investigator|staff\s*nurse|admission", re.I)
ADVT_HINT_RE = re.compile(r"advertisement|\badvt\b|notification|vacanc|recruitment|rolling|विज्ञापन|भर्ती|रिक्ति", re.I)
ADVT_NO_RE = re.compile(
    r"(?:advt\.?|advertisement|adv\.?|विज्ञापन)\s*(?:no\.?|number|संख्या|सं\.?)\s*[:.\-–]?\s*"
    r"(.{3,60}?)(?=\s+(?:dated?|date|दिनांक)\b|\s{2,}|[,;]|\s+(?:for|applications?|online)\b|$)", re.I)
POSTS_RE = re.compile(
    r"(?<![\d/.])(\d{1,4})\s+(?:\(\w+\)\s+)?(?:(?:regular|teaching|faculty|sanctioned|vacant|assistant|professors?)\s+){0,3}"
    r"(?:posts?|vacanc(?:y|ies)|पद)\b", re.I)
LEVEL_RE = re.compile(r"(?:academic\s+)?(?:pay\s+)?level\s*[-–:]?\s*(1[0-4]|10A)\b", re.I)
AMOUNT_RE = re.compile(r"(?:rs\.?|₹|inr)\s*([1-9][\d,]{4,8})", re.I)
FEE_RE = re.compile(r"(?:application|examination|registration|processing)?\s*fee[s]?\b(.{0,160})", re.I)
ONLINE_RE = re.compile(r"apply\s+online|online\s+(?:application|mode|portal|form|registration)|recruitment\s+portal|"
                       r"applications?\s+(?:must|should|will)\s+be\s+(?:submitted|filled)\s+online|ऑनलाइन", re.I)
OFFLINE_RE = re.compile(r"speed\s*post|registered\s*post|by\s+post|hard\s*copy|print[\s-]*out|physical\s+copy|"
                        r"in\s+person|by\s+hand|offline", re.I)
HARDCOPY_RE = re.compile(r"hard\s*copy|print[\s-]*out|physical\s+copy|speed\s*post|registered\s*post", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"')]+|www\.[^\s<>\"')]+", re.I)


def find_subjects(text, compiled):
    return [label for label, pats in compiled.items() if any(p.search(text) for p in pats)]


def other_subjects(text, limit=14):
    found = []
    for s in __import__("vacancywatch.config", fromlist=["OTHER_SUBJECTS"]).OTHER_SUBJECTS:
        if re.search(r"\b" + re.escape(s).replace(r"\ ", r"\s+") + r"\b", text, re.I):
            found.append(s)
    # "Ancient History" also contains "History": keep the more specific one
    found = [s for s in found if not any(s != o and s in o for o in found)]
    return found[:limit]


def subject_snippets(text, compiled, max_n=4):
    out = []
    for label, pats in compiled.items():
        for p in pats:
            for m in p.finditer(text):
                s = clean(text[max(0, m.start() - 110):m.end() + 170])
                if s and not any(s[:60] in o for o in out):
                    out.append("…" + s + "…")
                if len(out) >= max_n:
                    return out
    return out


def _date_near(text, rx, span=160):
    for m in rx.finditer(text):
        ds = find_dates(text[m.start():m.end() + span])
        if ds:
            return ds[-1][0].isoformat() if len(ds) > 1 else ds[0][0].isoformat()
    return None


def read_details(text):
    """Pull the useful facts out of a recruitment notification."""
    t = clean(text)
    d = {}
    m = ADVT_NO_RE.search(t)
    if m:
        d["advt_no"] = m.group(1).strip(" :.-–")[:60]
    posts = [int(x) for x in POSTS_RE.findall(t) if 0 < int(x) < 2000]
    if posts:
        d["posts"] = max(posts) if len(posts) < 4 else posts[0]
    lv = LEVEL_RE.search(t)
    amounts = []
    for am in AMOUNT_RE.finditer(t):
        ctx = t[max(0, am.start() - 120):am.start()].lower()
        value = int(am.group(1).replace(",", ""))
        if value >= 10000 and "fee" not in ctx[-60:] and any(
                k in ctx for k in ("pay", "level", "salary", "emolument", "remuneration", "honorarium", "per month",
                                   "consolidated")):
            amounts.append(am.group(1))
    pay = []
    if lv:
        pay.append(f"Academic Level {lv.group(1)}")
    if amounts:
        pay.append("Rs " + amounts[0])
    if pay:
        d["pay"] = ", ".join(pay)
    fm = FEE_RE.search(t)
    if fm and re.search(r"(?:rs\.?|₹|inr)\s*\d", fm.group(1), re.I):
        fee = clean(fm.group(0))
        last = list(re.finditer(r"(?:rs\.?|₹|inr)\s*[\d,]+(?:\s*/-)?", fee, re.I))
        d["fee"] = fee[:last[-1].end()][:170] if last else fee[:170]
    on, off = bool(ONLINE_RE.search(t)), bool(OFFLINE_RE.search(t))
    if on or off:
        d["mode"] = "Online + hard copy / post" if on and off else ("Online" if on else "Offline (by post / in person)")
    hc = _date_near(t, HARDCOPY_RE)
    if hc and off:
        d["hardcopy_by"] = hc
    q = []
    if re.search(r"\bNET\b|\bSET\b|\bSLET\b", t):
        q.append("NET/SET")
    if re.search(r"Ph\.?\s?D", t):
        q.append("Ph.D")
    if re.search(r"UGC\s+Regulations?,?\s*2018", t, re.I):
        q.append("as per UGC Regulations 2018")
    if q:
        d["qualification"] = ", ".join(q)
    for u in URL_RE.findall(t):
        if re.search(r"recruit|apply|portal|career|job", u, re.I):
            u = u.rstrip(".,;:")
            d["apply_url"] = u if u.startswith("http") else "https://" + u
            break
    return d


# ---------------------------------------------------------------- identity & titles (used to merge duplicates)

GENERIC_TITLE_RE = re.compile(
    r"^(?:view|download|click\s*here|read\s*more|details?|apply(?:\s*online|\s*now)?|notification|notice|"
    r"advertisement|advt\.?|pdf|link|here|more|open)(?:\s+(?:advertisement|advt|notification|notice|here|pdf|details?|link))*\W*$", re.I)
POST_BLOCK_RE = re.compile(r"^\s*designation\b|total\s+posts?\s+\d", re.I)


def advt_key(text):
    """Normalised advertisement number, e.g. 'CMP/Rec./02/Asst. Prof./2026' -> 'cmprec02asstprof2026'."""
    if not text:
        return None
    m = ADVT_NO_RE.search(clean(text))
    if not m:
        return None
    raw = re.split(r"\s+(?:dated?|date|दिनांक)\b", m.group(1), flags=re.I)[0]
    key = re.sub(r"[^a-z0-9]", "", raw.lower())
    return key if len(key) >= 4 and re.search(r"\d", key) else None


def title_score(title):
    t = title or ""
    score = 0
    if re.search(r"assistant\s*prof|asst\.?\s*prof|faculty|teaching", t, re.I):
        score += 3
    if ADVT_NO_RE.search(t):
        score += 2
    if GENERIC_TITLE_RE.match(t.strip()) or len(t) < 20:
        score -= 5
    if POST_BLOCK_RE.search(t):
        score -= 2
    if 30 <= len(t) <= 220:
        score += 1
    return score


def better_title(title, notice_text, details, subjects_found=(), others=()):
    """Replace link texts like 'View advertisement' with a meaningful title built from the notice."""
    if title_score(title) >= 2 or not (notice_text or details):
        return title
    parts = ["Assistant Professor recruitment"]
    subs = list(subjects_found) + [o for o in others if o not in subjects_found]
    if subs:
        parts[0] += " (" + ", ".join(subs[:8]) + ("…" if len(subs) > 8 else "") + ")"
    if details.get("advt_no"):
        parts.append("Advt " + details["advt_no"])
    if details.get("posts"):
        parts.append(f"{details['posts']} posts")
    return " — ".join(parts)

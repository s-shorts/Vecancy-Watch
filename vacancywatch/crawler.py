"""Fetches institution websites, finds recruitment pages and extracts vacancy notices."""
import hashlib
import io
import json
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

from . import extract

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 VacancyWatch/1.0")
NAV_RE = re.compile(
    r"recruit|career|\bjobs?\b|vacanc|employment|advertisement|\badvts?\b|opportunit|"
    r"positions?\s+open|open\s+positions?|work\s+with\s+us|join\s+(?:us|our)|faculty\s+position", re.I)
NAV_EXCLUDE = re.compile(r"placement|counsel|guidance|alumni|student|training|exchange|internship|"
                         r"login|tender|admission", re.I)
FACULTY_HINT = re.compile(r"faculty|teaching|professor|academic", re.I)
DOC_EXT = (".pdf", ".doc", ".docx")
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".zip", ".rar", ".xls", ".xlsx", ".mp4", ".mp3",
            ".ppt", ".pptx", ".csv", ".svg", ".webp")
EXTERNAL_OK = ("samarth.edu.in", "samarth.ac.in")   # central-university recruitment portals
BLOCK_TAGS = ["tr", "li", "p", "dd", "h3", "h4", "h5", "h6"]
BLOCK_SET = set(BLOCK_TAGS) | {"div"}
NAV_CLASS = re.compile(r"menu|navbar|nav-|dropdown|breadcrumb|footer|header", re.I)
TRAILING_JUNK = re.compile(
    r"(?:[\s|:\-–]*(?:download|pdf|view|details?|click\s*here|read\s*more|more\s*info|apply\s*(?:online|now|here)|"
    r"link|new|\(\s*\d+(?:\.\d+)?\s*[kKmM][bB]\s*\)|\d+(?:\.\d+)?\s*[kKmM][bB]))+\s*$", re.I)
MAX_HTML = 4_000_000
MAX_DOC = 8_000_000


class FetchError(Exception):
    pass


@dataclass
class Page:
    url: str
    ctype: str
    body: bytes

    @property
    def is_pdf(self):
        return "pdf" in self.ctype or self.url.lower().split("?")[0].endswith(".pdf") or self.body[:4] == b"%PDF"


@dataclass
class Item:
    uid: str
    title: str
    url: str
    page_url: str
    kind: str
    nature: str
    opening: object = None
    deadline: object = None
    posted: object = None
    confirm: bool = False          # found by a generic "Advertisement" link - must be confirmed by reading it
    deadline_src: str = ""         # listing (shown on the website) | notice (read from the PDF)
    notice_text: str = ""
    read_how: str = ""             # pdf | page | scanned | failed | ""


@dataclass
class CrawlResult:
    items: list
    pages: list
    error: str = None
    rediscovered: bool = False
    pages_fetched: int = 0
    colleges: list = field(default_factory=list)


class Fetcher:
    """HTTP client with retries, per-host politeness delay and fallback for broken SSL certificates
    (common on government sites)."""

    def __init__(self, timeout=25, min_gap=1.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})
        retry = urllib3.Retry(total=2, backoff_factor=1, status_forcelist=[502, 503, 504],
                              allowed_methods=["GET"])
        adapter = requests.adapters.HTTPAdapter(max_retries=retry, pool_maxsize=32)
        self.s.mount("http://", adapter)
        self.s.mount("https://", adapter)
        self.timeout = timeout
        self.min_gap = min_gap
        self._host_next = {}
        self._lock = threading.Lock()
        self._insecure = set()
        self._cache = {}             # url -> Page, for this check only (same PDF linked from several pages)
        self._cache_bytes = 0
        self.hits = 0

    def _wait(self, host):
        with self._lock:
            now = time.monotonic()
            at = max(now, self._host_next.get(host, 0))
            self._host_next[host] = at + self.min_gap
        if at > now:
            time.sleep(at - now)

    def get(self, url, max_bytes=MAX_HTML) -> Page:
        key = re.sub(r"^https?://(www\.)?", "", url)
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            self.hits += 1
            if isinstance(hit, FetchError):
                raise hit
            return hit
        try:
            page = self._get(url, max_bytes)
        except FetchError as e:
            with self._lock:
                self._cache[key] = e
            raise
        with self._lock:
            if len(page.body) < 3_000_000 and self._cache_bytes < 300_000_000:
                self._cache[key] = page
                self._cache_bytes += len(page.body)
        return page

    def _get(self, url, max_bytes=MAX_HTML) -> Page:
        host = urlparse(url).netloc
        self._wait(host)
        try:
            try:
                r = self.s.get(url, timeout=self.timeout, verify=host not in self._insecure, stream=True)
            except requests.exceptions.SSLError:
                self._insecure.add(host)
                r = self.s.get(url, timeout=self.timeout, verify=False, stream=True)
        except requests.exceptions.Timeout:
            raise FetchError("timed out")
        except requests.exceptions.ConnectionError:
            raise FetchError("could not connect")
        except requests.RequestException as e:
            raise FetchError(type(e).__name__)
        try:
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code}")
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                chunks.append(chunk)
                if size > max_bytes:
                    break
            return Page(url=r.url, ctype=r.headers.get("Content-Type", "").lower(), body=b"".join(chunks))
        except requests.RequestException as e:
            raise FetchError(type(e).__name__)
        finally:
            r.close()


# ---------------------------------------------------------------- helpers

def site_key(netloc: str) -> str:
    host = netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("ac", "edu", "gov", "nic", "org", "co", "res", "ernet") and parts[-1] == "in":
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def same_site(a: str, b: str) -> bool:
    return site_key(urlparse(a).netloc) == site_key(urlparse(b).netloc)


def allowed_link(url: str, base: str) -> bool:
    host = urlparse(url).netloc.lower()
    return same_site(url, base) or any(host.endswith(x) for x in EXTERNAL_OK)


def norm_url(href: str, base: str):
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#", "whatsapp:")):
        return None
    url = urldefrag(urljoin(base, href))[0]
    if not url.startswith(("http://", "https://")):
        return None
    if url.lower().split("?")[0].endswith(SKIP_EXT):
        return None
    return url.replace(" ", "%20")


def is_doc(url: str) -> bool:
    return url.lower().split("?")[0].endswith(DOC_EXT)


def soupify(page: Page) -> BeautifulSoup:
    soup = BeautifulSoup(page.body, "html.parser")
    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()
    return soup


def make_uid(source_id, title, url) -> str:
    # dates are stripped so a changed / extended last date is recognised as the same notice
    t = extract.DATE_RE.sub(" ", title.lower())
    t = re.sub(r"\b(?:new|extended|revised)\b|[^a-z0-9]+", "", t)[:200]
    return hashlib.sha1(f"{source_id}|{t}|{url}".encode()).hexdigest()


def _href_text(href: str) -> str:
    return re.sub(r"[_\-+]|%20", " ", href or "")


def _in_nav(tag) -> bool:
    for p in tag.parents:
        if p.name in ("nav", "header", "footer"):
            return True
        cls = " ".join(p.get("class", []) or []) + " " + (p.get("id") or "")
        if cls.strip() and NAV_CLASS.search(cls):
            return True
    return False


def pdf_text(body: bytes, max_pages=4) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        return " ".join((reader.pages[i].extract_text() or "") for i in range(min(max_pages, len(reader.pages))))
    except Exception:
        return ""


# ---------------------------------------------------------------- extraction

def is_recruit_page(url: str) -> bool:
    u = urlparse(url)
    return bool(re.match(r"(?:recruit|career|jobs?)", u.netloc.lower()) or NAV_RE.search(_href_text(u.path)))


def extract_items(soup: BeautifulSoup, page_url: str, patterns, source_id=0, recruit_page=None):
    """Return (items, follow_links). follow_links are short nav-like links worth crawling.

    On recruitment pages, generic links such as "Advertisement No. 02/2026" are also returned
    (with confirm=True); the scanner reads them and keeps them only if they are faculty posts."""
    if recruit_page is None:
        recruit_page = is_recruit_page(page_url)

    def strong(text):
        return any(p.search(text) for p in patterns) and not (
            extract.NON_TEACHING_RE.search(text) and not re.search(r"assistant\s*prof|asst\.?\s*prof|faculty", text, re.I))

    def matches(text):
        return strong(text) or (recruit_page and extract.ADVT_HINT_RE.search(text)
                                and not extract.NON_TEACHING_RE.search(text) and not extract.UPDATE_RE.search(text))

    cands = []
    for tag in soup.find_all(BLOCK_TAGS + ["div", "a"]):
        if tag.name == "div" and tag.find(["div", "table", "ul", "ol", "p", "li", "tr"]):
            continue
        txt = extract.clean(tag.get_text(" "))
        if tag.name == "a":
            txt = extract.clean(txt + " " + (tag.get("title") or ""))
        if not txt or len(txt) > 1500:
            continue
        if matches(txt) or (tag.name == "a" and matches(_href_text(tag.get("href", "")))):
            cands.append(tag)

    mset = {id(t) for t in cands}
    final = []
    for tag in cands:
        parents = list(tag.parents)
        if tag.name != "tr" and any(id(p) in mset and p.name == "tr" for p in parents):
            continue
        if tag.name == "a" and any(id(p) in mset for p in parents):
            continue
        if tag.name not in ("a", "tr") and any(id(d) in mset for d in tag.find_all(list(BLOCK_SET))):
            continue
        final.append(tag)

    items, follow = {}, []
    for tag in final:
        links = []
        for a in ([tag] if tag.name == "a" else tag.find_all("a", href=True)):
            u = norm_url(a.get("href"), page_url)
            if u:
                links.append((u, extract.clean(a.get_text(" ")) + " " + _href_text(a.get("href"))))
        best = next((u for u, t in links if matches(t)), None) \
            or next((u for u, t in links if is_doc(u)), None) \
            or (links[0][0] if links else None)

        extra = (tag.get("title") or "") if tag.name == "a" else ""
        text = extract.clean(tag.get_text(" ") + " " + extra)
        title = re.sub(r"^(?:\d{1,3}\s*[.)]?\s+)?(?:new|latest)?\s*[:\-–]?\s*", "", text, flags=re.I)
        title = TRAILING_JUNK.sub("", title).strip(" -–|:")[:300] or text[:300]
        dates = extract.extract_dates(text)
        has_date = any(dates.values())
        url = best or page_url

        if (len(title) < 32 and not has_date and not is_doc(url)) or (_in_nav(tag) and not has_date and not is_doc(url)):
            if best and allowed_link(best, page_url) and best not in follow:
                follow.append(best)
            continue

        confirm = not strong(text)
        if confirm and not links:
            continue  # generic text without a notice to read
        uid = make_uid(source_id, title, url)
        items[uid] = Item(uid=uid, title=title, url=url, page_url=page_url,
                          kind=extract.classify_kind(title), nature=extract.classify_nature(title),
                          opening=dates["opening"], deadline=dates["deadline"], posted=dates["posted"],
                          confirm=confirm, deadline_src="listing" if dates["deadline"] else "")
    return list(items.values()), follow


def discover_links(soup: BeautifulSoup, base: str, faculty_only=False, limit=6):
    """Find links to recruitment / careers pages on the same site."""
    scored = {}
    for a in soup.find_all("a", href=True):
        u = norm_url(a["href"], base)
        if not u or is_doc(u) or not allowed_link(u, base):
            continue
        text = extract.clean(a.get_text(" ") + " " + (a.get("title") or "")) + " " + _href_text(urlparse(u).path)
        if not NAV_RE.search(text) or NAV_EXCLUDE.search(text):
            continue
        if faculty_only and not FACULTY_HINT.search(text):
            continue
        score = 2 if FACULTY_HINT.search(text) else 1
        if re.search(r"non[\s-]*teaching", text, re.I):
            score = 0.5
        scored[u] = max(score, scored.get(u, 0))
    return [u for u, _ in sorted(scored.items(), key=lambda kv: -kv[1])][:limit]


def crawl_source(source: dict, patterns, fetcher: Fetcher, rediscover=False, max_pages=10,
                 find_colleges=False) -> CrawlResult:
    base = source["url"]
    pages = json.loads(source.get("recruitment_pages") or "[]")
    items, errors, seen, fetched, colleges = {}, [], set(), 0, []
    depth = {}
    did_discover = False

    if not pages or rediscover:
        did_discover = True
        try:
            home = fetcher.get(base)
        except FetchError as e:
            return CrawlResult([], pages, str(e))
        fetched = 1
        seen.add(home.url)
        if home.is_pdf:
            return CrawlResult([], pages, "start URL is a PDF, not a web page", pages_fetched=1)
        soup = soupify(home)
        its, follow = extract_items(soup, home.url, patterns, source["id"])
        colleges = []
        if find_colleges:
            try:
                colleges = discover_colleges(soup, home.url, fetcher)
            except Exception:
                colleges = []
        for it in its:
            items[it.uid] = it
        found = discover_links(soup, home.url)
        pages = [home.url] + [u for u in dict.fromkeys(follow + found) if u != home.url][:7]
        queue = pages[1:]
    else:
        queue = list(pages)
    for u in queue:
        depth[u] = 1

    while queue and fetched < max_pages:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        try:
            pg = fetcher.get(u)
        except FetchError as e:
            errors.append(f"{u}: {e}")
            continue
        fetched += 1
        seen.add(pg.url)
        if pg.is_pdf:
            continue
        soup = soupify(pg)
        its, follow = extract_items(soup, pg.url, patterns, source["id"])
        for it in its:
            items[it.uid] = it
        if depth.get(u, 1) < 2:
            for link in follow + discover_links(soup, pg.url, faculty_only=True, limit=4):
                if link not in seen and link not in depth:
                    depth[link] = 2
                    queue.append(link)

    error = None
    if not fetched:
        error = errors[0].split(": ", 1)[-1] if errors else "no pages could be fetched"
    return CrawlResult(list(items.values()), pages, error, did_discover, fetched, colleges)


def worth_fetching(item: Item, base: str) -> bool:
    if item.url == item.page_url:
        return False
    return is_doc(item.url) or allowed_link(item.url, base)


def _doc_text(pg: Page):
    if pg.is_pdf:
        txt = pdf_text(pg.body, max_pages=8)
        return (txt, "pdf") if len(txt.strip()) > 80 else ("", "scanned")
    if "html" in pg.ctype or not pg.ctype or "text" in pg.ctype:
        return extract.clean(soupify(pg).get_text(" "))[:60000], "page"
    if pg.url.lower().split("?")[0].endswith((".doc", ".docx")):
        return "", "failed"
    return "", "failed"


def fetch_notice(item: Item, fetcher: Fetcher, base: str, patterns=None):
    """Open the notification (PDF or detail page) and return (text, how).

    For a detail web page, the advertisement PDFs linked from it are read as well."""
    if item.url == item.page_url:
        return "", ""
    try:
        pg = fetcher.get(item.url, max_bytes=MAX_DOC)
    except FetchError:
        return "", "failed"
    text, how = _doc_text(pg)
    if how == "page":
        soup = soupify(pg)
        pdfs = []
        for a in soup.find_all("a", href=True):
            u = norm_url(a["href"], pg.url)
            if not u or not is_doc(u) or u in pdfs:
                continue
            label = extract.clean(a.get_text(" ")) + " " + _href_text(a["href"])
            score = 2 if (patterns and any(p.search(label) for p in patterns)) else (
                1 if extract.ADVT_HINT_RE.search(label) else 0)
            if score:
                pdfs.append((score, u))
        for _, u in sorted(pdfs, key=lambda x: -x[0])[:2]:
            try:
                t2, h2 = _doc_text(fetcher.get(u, max_bytes=MAX_DOC))
            except FetchError:
                continue
            if t2:
                text += "\n" + t2
                how = "pdf"
    return text, how


def enrich_item(item: Item, fetcher: Fetcher, base="", patterns=None):
    """Read the notification and fill in missing dates. Keeps the text for subject matching / details."""
    text, how = fetch_notice(item, fetcher, base, patterns)
    item.notice_text, item.read_how = text[:60000], how
    if text:
        d = extract.extract_dates(text, labelled_only=True)
        if not item.deadline and d["deadline"]:
            item.deadline, item.deadline_src = d["deadline"], "notice"
        item.opening = item.opening or d["opening"]
        if item.opening and item.deadline and item.opening > item.deadline:
            item.opening = None
    return item


# ---------------------------------------------------------------- colleges listed on university websites

COLLEGE_PAGE_RE = re.compile(r"constituent|affiliated|associated|\bcolleges\b|list\s+of\s+colleges|"
                             r"सम्बद्ध|संबद्ध|संघटक|महाविद्यालयों", re.I)
COLLEGE_NAME_RE = re.compile(r"college|mahavidyalaya|vidyalaya|institute|महाविद्यालय|vidyapeeth|vidyapith", re.I)
GOVT_RE = re.compile(r"\bgovt\.?|government|rajkiya|राजकीय|constituent", re.I)
SKIP_HOSTS = re.compile(r"facebook|twitter|youtube|instagram|linkedin|google|wikipedia|whatsapp|\.gov\.in$|nic\.in$|"
                        r"ugc\.|aicte|naac|nirf|digilocker|swayam|mygov|india\.gov", re.I)


def discover_colleges(soup: BeautifulSoup, base: str, fetcher: Fetcher, limit=150):
    """Find constituent colleges (all) and government colleges (by name) linked from a university website."""
    pages = []
    for a in soup.find_all("a", href=True):
        u = norm_url(a["href"], base)
        text = extract.clean(a.get_text(" ") + " " + (a.get("title") or ""))
        if u and not is_doc(u) and same_site(u, base) and COLLEGE_PAGE_RE.search(text) and len(text) < 80 \
                and not re.search(r"admission|exam|result|syllabus", text, re.I):
            pages.append((2 if re.search(r"constituent|संघटक", text, re.I) else 1, u, text))
    found = {}
    for _, u, label in sorted(pages, key=lambda x: -x[0])[:3]:
        try:
            pg = fetcher.get(u)
        except FetchError:
            continue
        if pg.is_pdf:
            continue
        constituent = bool(re.search(r"constituent|संघटक", label, re.I))
        for a in soupify(pg).find_all("a", href=True):
            cu = norm_url(a["href"], pg.url)
            name = extract.clean(a.get_text(" "))
            if not cu or is_doc(cu) or same_site(cu, base) or not (8 <= len(name) <= 120):
                continue
            host = urlparse(cu).netloc.lower()
            if SKIP_HOSTS.search(host) or not COLLEGE_NAME_RE.search(name):
                continue
            if not (constituent or GOVT_RE.search(name)):
                continue
            root = f"{urlparse(cu).scheme}://{urlparse(cu).netloc}"
            found.setdefault(root, (name, "Constituent college" if constituent else "Govt college", cu))
            if len(found) >= limit:
                break
    return [(name, url, kind) for name, kind, url in found.values()]

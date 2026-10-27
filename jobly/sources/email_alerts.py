"""Read job-alert emails from LinkedIn, Indeed, Handshake, Glassdoor, ZipRecruiter...

These sites block scrapers and their terms forbid it, but they all send instant
job-alert emails. Turn alerts on (set them to "daily" or, better, "as they're
posted"), point Jobly at your inbox over IMAP, and every alert becomes a job in
your feed within a minute or two of landing.

Gmail: create an App Password (Google Account → Security → App passwords) and
use it as ``password``. IMAP must be enabled in Gmail settings.
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import re
from datetime import timedelta
from email.header import decode_header, make_header
from email.message import Message
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from ..models import Job, utcnow
from ..textutil import looks_remote, parse_date, truncate
from .base import Source

DEFAULT_SENDERS = [
    "linkedin.com",
    "indeed.com",
    "joinhandshake.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "wellfound.com",
    "dice.com",
    "monster.com",
    "simplyhired.com",
    "builtin.com",
]

# (board name, regex on the link, canonical url template)
_JOB_LINKS: list[tuple[str, re.Pattern[str], str]] = [
    ("LinkedIn", re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[\w%-]*?-)?(\d{6,})"),
     "https://www.linkedin.com/jobs/view/{id}/"),
    ("Indeed", re.compile(r"indeed\.com/.*?[?&](?:jk|vjk)=([0-9a-f]{8,})"),
     "https://www.indeed.com/viewjob?jk={id}"),
    ("Handshake", re.compile(r"joinhandshake\.com/(?:stu/)?(?:jobs|postings|job-search)/(\d+)"),
     "https://app.joinhandshake.com/jobs/{id}"),
    ("Glassdoor", re.compile(r"glassdoor\.[\w.]+/.*?(?:jobListingId=|[?&]jl=)(\d+)"),
     "https://www.glassdoor.com/job-listing/index.htm?jl={id}"),
    ("Wellfound", re.compile(r"wellfound\.com/(?:jobs|l)/(\d+)"), "https://wellfound.com/jobs/{id}"),
    ("ZipRecruiter", re.compile(r"ziprecruiter\.com/(?:c/[^/]+/)?(?:Job|jobs|job)/([^?#\s]+)"),
     "https://www.ziprecruiter.com/jobs/{id}"),
    ("Greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([\w-]+/jobs/\d+)"),
     "https://job-boards.greenhouse.io/{id}"),
    ("Lever", re.compile(r"jobs\.lever\.co/([\w.-]+/[0-9a-f-]{36})"), "https://jobs.lever.co/{id}"),
    ("Ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+/[0-9a-f-]{36})"), "https://jobs.ashbyhq.com/{id}"),
]

_JUNK_TEXT = re.compile(
    r"^(view( job| all| more)?|apply( now)?|see (all|more)( jobs)?|easy apply|unsubscribe|"
    r"manage alerts?|save|learn more|search (for )?jobs|jobs|home|.{0,2})$",
    re.IGNORECASE,
)
_SEP = re.compile(r"\s+[·•|]\s+|\s+-\s+")


def _unwrap(url: str) -> str:
    """Pull the real destination out of common tracking redirects."""
    for _ in range(3):
        qs = parse_qs(urlparse(url).query)
        for key in ("url", "u", "dest", "destination", "redirect", "target", "r"):
            if key in qs and qs[key][0].startswith("http"):
                url = unquote(qs[key][0])
                break
        else:
            return url
    return url


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _html_part(msg: Message) -> tuple[str, bool]:
    """Return (body, is_html), preferring the HTML part."""
    html, text = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/html" and html is None:
            html = body
        elif ctype == "text/plain" and text is None:
            text = body
    if html is not None:
        return html, True
    return text or "", False


def _context_lines(anchor) -> list[str]:
    """Text lines of the smallest container around a job link (the 'job card')."""
    node = anchor
    for _ in range(6):
        node = node.parent
        if node is None:
            break
        text = node.get_text("\n", strip=True)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if len(lines) >= 3 or len(text) > 160:
            return lines[:12]
    return []


def parse_alert_email(msg: Message) -> list[Job]:
    subject = _decode(msg.get("Subject"))
    received = parse_date(msg.get("Date"))
    body, is_html = _html_part(msg)
    links: list[tuple[str, str, object]] = []  # (url, text, anchor)
    if is_html:
        soup = BeautifulSoup(body, "html.parser")
        for a in soup.find_all("a", href=True):
            links.append((a["href"], a.get_text(" ", strip=True), a))
    else:
        for m in re.finditer(r"https?://\S+", body):
            links.append((m.group(0).rstrip(").,>"), "", None))

    found: dict[str, dict] = {}
    for href, text, anchor in links:
        url = _unwrap(href)
        for board, pattern, template in _JOB_LINKS:
            m = pattern.search(url)
            if not m:
                continue
            job_key = f"{board}:{m.group(1)}"
            entry = found.setdefault(job_key, {
                "board": board, "id": m.group(1), "url": template.format(id=m.group(1)),
                "title": "", "context": [],
            })
            if text and not _JUNK_TEXT.match(text) and len(text) > len(entry["title"]):
                entry["title"] = text
                if anchor is not None:
                    entry["context"] = _context_lines(anchor)
            break

    jobs = []
    for entry in found.values():
        title = entry["title"]
        company, location = "", ""
        ctx = entry["context"]
        if title and ctx:
            try:
                idx = next(i for i, ln in enumerate(ctx) if title in ln or ln in title)
            except StopIteration:
                idx = -1
            rest = [ln for ln in ctx[idx + 1:] if not _JUNK_TEXT.match(ln)]
            if rest:
                pieces = _SEP.split(rest[0])
                company = pieces[0]
                if len(pieces) > 1:
                    location = pieces[1]
                elif len(rest) > 1:
                    location = rest[1]
        if not title:
            # Single-job alerts often put the role in the subject: "Data Analyst at Acme"
            m = re.match(r"(?:.*?:\s*)?(.+?)\s+at\s+(.+?)(?:\s*[-–|:].*)?$", subject)
            if m and len(found) == 1:
                title, company = m.group(1), m.group(2)
        if not title:
            continue
        jobs.append(Job(
            source=f"email:{entry['board'].lower()}",
            external_id=hashlib.sha1(entry["id"].encode()).hexdigest()[:20] if "/" in entry["id"] else entry["id"],
            title=truncate(title, 160),
            company=truncate(company, 100),
            location=truncate(location, 120),
            remote=looks_remote(location, title),
            url=entry["url"],
            posted_at=received,
            board=entry["board"],
            description="",
        ))
    return jobs


class EmailAlertsSource(Source):
    kind = "email_alerts"
    board = "Email alerts"
    default_interval_minutes = 2

    def validate(self) -> None:
        self.require("username", "password")

    @property
    def name(self) -> str:
        return self.spec["username"]

    def fetch(self) -> list[Job]:
        host = self.spec.get("host", "imap.gmail.com")
        senders = self.spec.get("senders") or DEFAULT_SENDERS
        days = int(self.spec.get("lookback_days", 3))
        since = (utcnow() - timedelta(days=days)).strftime("%d-%b-%Y")
        jobs: list[Job] = []
        conn = imaplib.IMAP4_SSL(host, int(self.spec.get("port", 993)))
        try:
            conn.login(self.spec["username"], self.spec["password"])
            conn.select(self.spec.get("folder", "INBOX"), readonly=True)
            uids: set[bytes] = set()
            for sender in senders:
                status, data = conn.uid("search", None, f'(SINCE {since} FROM "{sender}")')
                if status == "OK" and data and data[0]:
                    uids.update(data[0].split())
            for uid in sorted(uids):
                status, data = conn.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not data or not isinstance(data[0], tuple):
                    continue
                msg = email.message_from_bytes(data[0][1])
                jobs.extend(parse_alert_email(msg))
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        return jobs

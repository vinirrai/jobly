"""Small text helpers shared by sources, matcher and renderers."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

_BLOCK_TAGS = ["p", "div", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"]


def html_to_text(raw: str | None) -> str:
    """Convert job-description HTML into readable plain text with bullets."""
    if not raw:
        return ""
    if "&lt;" in raw and "<" not in raw:  # Greenhouse double-escapes its HTML
        raw = html.unescape(raw)
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_after("\n")
    text = soup.get_text()
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln == "•":
            continue
        if not ln and (not out or not out[-1]):
            continue  # collapse blank runs
        if ln.startswith("• ") and len(out) >= 2 and not out[-1] and out[-2].startswith("• "):
            out.pop()  # keep list items together
        out.append(ln)
    return "\n".join(out).strip()


def norm_key(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|the)\b\.?", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def skill_pattern(skill: str) -> re.Pattern[str]:
    """Regex that finds a skill as a whole term (handles C++, C#, .NET, Node.js)."""
    esc = re.escape(skill.strip())
    flags = 0 if len(skill.strip()) <= 2 else re.IGNORECASE
    return re.compile(rf"(?<![\w+#.]){esc}(?![\w+#])", flags)


def contains_term(text: str, term: str) -> bool:
    return bool(term.strip()) and bool(skill_pattern(term).search(text or ""))


def parse_date(value) -> datetime | None:
    """Parse ISO strings, epoch seconds / milliseconds. Returns aware UTC datetimes."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(value).strip()
    if s.isdigit():
        return parse_date(int(s))
    s = s.replace("Z", "+00:00")
    for candidate in (s, s[:19], s[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|anywhere|distributed)\b", re.IGNORECASE)


def looks_remote(*texts: str | None) -> bool:
    return any(t and REMOTE_RE.search(t) for t in texts)


def truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"

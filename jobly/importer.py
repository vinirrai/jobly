"""Turn any URL into jobs: detect the ATS behind a careers page, or import one posting.

Most company career pages (including startups) are powered by Greenhouse, Lever,
Ashby, Workday or SmartRecruiters. When we recognise one we use its public API.
Otherwise we read the schema.org ``JobPosting`` data that sites publish for
Google for Jobs, and finally fall back to the page text.
"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .http import Http
from .models import Job
from .sources.ats import (
    AshbySource,
    GreenhouseSource,
    LeverSource,
    SmartRecruitersSource,
    WorkdaySource,
    parse_workday_url,
)
from .textutil import html_to_text, looks_remote, parse_date, truncate


class NeedsManualPaste(RuntimeError):
    """The page can't be read automatically (login wall or bot protection)."""


# ---------------------------------------------------------------- detection

_BOARD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)")),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)")),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)")),
]
_NOT_COMPANY = {"embed", "v1", "api", "jobs", "job_board", "static", "assets"}


def detect_board(url: str) -> dict | None:
    """Return a source spec for a careers-board URL, e.g. {'type': 'lever', 'company': 'acme'}."""
    url = url.strip()
    wd = parse_workday_url(url)
    if wd:
        host, _tenant, site = wd
        return {"type": "workday", "url": f"https://{host}/{site}"}
    for kind, pat in _BOARD_PATTERNS:
        m = pat.search(url)
        if m and m.group(1).lower() not in _NOT_COMPANY:
            spec = {"type": kind, "company": m.group(1)}
            if kind == "lever" and ".eu.lever.co" in url:
                spec["region"] = "eu"
            return spec
    return None


def detect_board_in_html(html: str) -> dict | None:
    """Find an embedded ATS (iframe, script or links) inside a company careers page."""
    for m in re.finditer(r"https?://[^\s\"'<>]+", html):
        spec = detect_board(m.group(0))
        if spec:
            return spec
    m = re.search(r"greenhouse\.io/embed/job_board/js\?for=([\w-]+)", html)
    if m:
        return {"type": "greenhouse", "company": m.group(1)}
    return None


# -------------------------------------------------------------- JSON-LD

def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node:
                    stack.append(node["@graph"])
                yield node


def _jsonld_location(node: dict) -> str:
    locs = node.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    out = []
    for loc in locs:
        addr = (loc or {}).get("address") or {}
        if isinstance(addr, str):
            out.append(addr)
            continue
        parts = [addr.get("addressLocality"), addr.get("addressRegion")]
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts.append(country)
        out.append(", ".join(p for p in parts if p))
    return "; ".join(o for o in out if o)


def _jsonld_salary(node: dict) -> str | None:
    sal = node.get("baseSalary")
    if not isinstance(sal, dict):
        return None
    val = sal.get("value") or {}
    cur = sal.get("currency", "")
    if isinstance(val, dict):
        lo, hi, unit = val.get("minValue"), val.get("maxValue"), val.get("unitText", "")
        if lo and hi:
            return f"{cur} {lo:,}–{hi:,} {unit}".strip() if isinstance(lo, (int, float)) else f"{cur} {lo}–{hi} {unit}"
        if val.get("value"):
            return f"{cur} {val['value']} {unit}".strip()
    return None


def jobs_from_jsonld(html: str, page_url: str, source: str, board: str = "Company site") -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for node in _iter_jsonld(soup):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "JobPosting" not in types:
            continue
        org = node.get("hiringOrganization") or {}
        company = org.get("name", "") if isinstance(org, dict) else str(org)
        location = _jsonld_location(node)
        remote = node.get("jobLocationType") == "TELECOMMUTE" or looks_remote(location)
        url = node.get("url") or page_url
        ident = node.get("identifier")
        if isinstance(ident, dict):
            ident = ident.get("value")
        external_id = str(ident or hashlib.sha1(f"{url}|{node.get('title')}".encode()).hexdigest()[:20])
        emp = node.get("employmentType")
        jobs.append(Job(
            source=source,
            external_id=external_id,
            title=html_to_text(node.get("title", "")),
            company=company,
            location=location or ("Remote" if remote else ""),
            remote=remote,
            url=urljoin(page_url, url),
            description=html_to_text(node.get("description")),
            posted_at=parse_date(node.get("datePosted")),
            salary=_jsonld_salary(node),
            employment_type=", ".join(emp) if isinstance(emp, list) else emp,
            board=board,
        ))
    return jobs


# ------------------------------------------------------------ single job

_LINKEDIN_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[\w%-]*?-)?(\d{6,})")
_GH_JOB_RE = re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)/jobs/(\d+)")
_LEVER_JOB_RE = re.compile(r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)/([0-9a-f-]{36})")
_ASHBY_JOB_RE = re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})")
_SR_JOB_RE = re.compile(r"jobs\.smartrecruiters\.com/([\w-]+)/(\d+)")

BOARD_NAMES = {
    "linkedin.com": "LinkedIn",
    "indeed.com": "Indeed",
    "joinhandshake.com": "Handshake",
    "glassdoor.com": "Glassdoor",
    "ziprecruiter.com": "ZipRecruiter",
    "wellfound.com": "Wellfound",
    "builtin.com": "Built In",
    "ycombinator.com": "Y Combinator",
}


def board_for_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for domain, name in BOARD_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return "Company site"


def _page_fallback(html: str, url: str) -> Job:
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        "div.show-more-less-html__markup", "div.description__text",  # LinkedIn public page
        "#jobDescriptionText",  # Indeed
        "[data-automation-id=jobPostingDescription]",  # Workday
        "main", "article", "[role=main]",
    ]
    body = next((el for sel in selectors if (el := soup.select_one(sel))), soup.body or soup)
    description = html_to_text(str(body))
    title_el = (soup.select_one("h1.top-card-layout__title") or soup.select_one("h1")
                or soup.find("meta", property="og:title") or soup.title)
    title = title_el.get("content") if title_el and title_el.name == "meta" else (
        title_el.get_text(" ", strip=True) if title_el else "")
    company_el = soup.select_one("a.topcard__org-name-link") or soup.find("meta", property="og:site_name")
    company = company_el.get("content") if company_el and company_el.name == "meta" else (
        company_el.get_text(" ", strip=True) if company_el else "")
    loc_el = soup.select_one("span.topcard__flavor--bullet")
    location = loc_el.get_text(" ", strip=True) if loc_el else ""
    if len(description) < 200:
        raise NeedsManualPaste("Couldn't read the job description from this page.")
    return Job(
        source="manual",
        external_id=hashlib.sha1(url.encode()).hexdigest()[:20],
        title=truncate(title or "Untitled role", 160),
        company=company,
        location=location,
        remote=looks_remote(location, description[:2000]),
        url=url,
        description=description[:20000],
        board=board_for_url(url),
    )


def fetch_job_from_url(url: str, http: Http) -> Job:
    """Fetch a single job posting from any URL."""
    url = url.strip()
    if m := _GH_JOB_RE.search(url):
        token, jid = m.groups()
        item = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}")
        return GreenhouseSource.parse_job(item, token.replace("-", " ").title(), token)
    if m := _LEVER_JOB_RE.search(url):
        slug, pid = m.groups()
        host = "api.eu.lever.co" if ".eu.lever.co" in url else "api.lever.co"
        item = http.get_json(f"https://{host}/v0/postings/{slug}/{pid}?mode=json")
        return LeverSource.parse_job(item, slug.replace("-", " ").title(), slug)
    if m := _ASHBY_JOB_RE.search(url):
        slug, pid = m.groups()
        data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
        for item in data.get("jobs", []):
            if item.get("id") == pid:
                return AshbySource.parse_job(item, slug.replace("-", " ").title(), slug)
    if m := _SR_JOB_RE.search(url):
        company_id, pid = m.groups()
        item = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{pid}")
        return SmartRecruitersSource.parse_job(item, company_id, SmartRecruitersSource.sections_text(item))
    if (wd := parse_workday_url(url)) and "/job/" in url:
        host, tenant, site = wd
        path = url[url.index("/job/"):].split("?")[0]
        detail = http.get_json(f"https://{host}/wday/cxs/{tenant}/{site}{path}")
        return WorkdaySource.parse_detail(detail, host, site, path, tenant.title())
    if "joinhandshake.com" in url:
        raise NeedsManualPaste("Handshake postings need you to be logged in — paste the description instead.")
    if m := _LINKEDIN_RE.search(url) or re.search(r"currentJobId=(\d+)", url):
        url = f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
    if jk := parse_qs(urlparse(url).query).get("jk"):
        url = f"https://www.indeed.com/viewjob?jk={jk[0]}"

    try:
        html = http.get_text(url, browser=True)
    except Exception as e:
        raise NeedsManualPaste(f"Couldn't open the page ({e}). Paste the description instead.") from e
    found = jobs_from_jsonld(html, url, source="manual", board=board_for_url(url))
    if found:
        job = found[0]
        if len(job.description) >= 200:
            job.url = url
            job.external_id = hashlib.sha1(url.encode()).hexdigest()[:20]
            return job
    return _page_fallback(html, url)


def manual_job(title: str, company: str, description: str, url: str = "", location: str = "") -> Job:
    key = url or f"{company}|{title}|{description[:200]}"
    return Job(
        source="manual",
        external_id=hashlib.sha1(key.encode()).hexdigest()[:20],
        title=title.strip() or "Untitled role",
        company=company.strip(),
        location=location.strip(),
        remote=looks_remote(location, description[:2000]),
        url=url.strip() or "about:blank",
        description=description.strip(),
        board=board_for_url(url) if url else "Manual",
    )


# ------------------------------------------------------ careers page links

_JOB_PATH_RE = re.compile(r"/(job|jobs|careers|positions|openings|vacancies|opportunities|roles)/[^/?#]+", re.I)
_ROLE_HINT = re.compile(
    r"\b(engineer|developer|scientist|analyst|designer|manager|intern|internship|associate|"
    r"specialist|coordinator|lead|architect|researcher|consultant|representative|technician|"
    r"administrator|assistant|director|marketing|sales|operations|product)\b",
    re.I,
)


def jobs_from_links(html: str, page_url: str, source: str, company: str) -> list[Job]:
    """Last resort for hand-built careers pages: links that look like job postings."""
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    page_path = urlparse(page_url).path.rstrip("/")
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"]).split("#")[0]
        text = a.get_text(" ", strip=True)
        path = urlparse(href).path.rstrip("/")
        if href in seen or path == page_path or not (4 <= len(text) <= 120):
            continue
        if not (_JOB_PATH_RE.search(path) and _ROLE_HINT.search(text)):
            continue
        seen.add(href)
        jobs.append(Job(
            source=source,
            external_id=hashlib.sha1(href.encode()).hexdigest()[:20],
            title=text,
            company=company,
            url=href,
            remote=looks_remote(text),
            board="Company site",
        ))
    return jobs

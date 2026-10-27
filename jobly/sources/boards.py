"""Job boards and aggregators with public or key-based APIs."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

from ..models import Job
from ..textutil import html_to_text, looks_remote, parse_date, truncate
from .base import Source


class RemotiveSource(Source):
    """Remote jobs from remotive.com (free public API)."""

    kind = "remotive"
    board = "Remotive"
    default_interval_minutes = 30

    @property
    def name(self) -> str:
        return self.spec.get("search") or self.spec.get("category") or "all"

    @staticmethod
    def parse_job(item: dict) -> Job:
        return Job(
            source="remotive",
            external_id=str(item["id"]),
            title=item.get("title", "").strip(),
            company=item.get("company_name", ""),
            location=item.get("candidate_required_location") or "Remote",
            remote=True,
            url=item.get("url", ""),
            description=html_to_text(item.get("description")),
            posted_at=parse_date(item.get("publication_date")),
            salary=item.get("salary") or None,
            employment_type=item.get("job_type"),
            department=item.get("category"),
            board="Remotive",
        )

    def fetch(self) -> list[Job]:
        params = []
        if self.spec.get("search"):
            params.append(f"search={quote_plus(self.spec['search'])}")
        if self.spec.get("category"):
            params.append(f"category={quote_plus(self.spec['category'])}")
        params.append(f"limit={int(self.spec.get('limit', 100))}")
        data = self.http.get_json("https://remotive.com/api/remote-jobs?" + "&".join(params))
        return [self.parse_job(it) for it in data.get("jobs", [])]


class RemoteOKSource(Source):
    """Remote jobs from remoteok.com (free public API; links back to RemoteOK as required)."""

    kind = "remoteok"
    board = "RemoteOK"
    default_interval_minutes = 30

    @property
    def name(self) -> str:
        return ",".join(self.spec.get("tags") or []) or "all"

    @staticmethod
    def parse_job(item: dict) -> Job:
        salary = None
        if item.get("salary_min") and item.get("salary_max"):
            salary = f"${int(item['salary_min']):,}–${int(item['salary_max']):,}"
        return Job(
            source="remoteok",
            external_id=str(item.get("id") or item.get("slug")),
            title=(item.get("position") or "").strip(),
            company=item.get("company", ""),
            location=item.get("location") or "Remote",
            remote=True,
            url=item.get("url") or f"https://remoteok.com/remote-jobs/{item.get('slug')}",
            apply_url=item.get("apply_url"),
            description=html_to_text(item.get("description")),
            posted_at=parse_date(item.get("epoch") or item.get("date")),
            salary=salary,
            board="RemoteOK",
        )

    def fetch(self) -> list[Job]:
        tags = self.spec.get("tags") or []
        url = "https://remoteok.com/api" + (f"?tags={quote_plus(','.join(tags))}" if tags else "")
        data = self.http.get_json(url)
        return [self.parse_job(it) for it in data if isinstance(it, dict) and it.get("position")]


_ROLE_WORDS = re.compile(
    r"\b(engineer|developer|scientist|analyst|designer|manager|intern|lead|architect|"
    r"researcher|specialist|consultant|sre|devops|founding|head of|associate|director)\b",
    re.IGNORECASE,
)


class HackerNewsHiringSource(Source):
    """Top-level comments in the monthly "Ask HN: Who is hiring?" thread (great for startups)."""

    kind = "hn_hiring"
    board = "Hacker News"
    default_interval_minutes = 30

    @property
    def name(self) -> str:
        return "whoishiring"

    @staticmethod
    def parse_comment(hit: dict) -> Job | None:
        text = html_to_text(hit.get("comment_text"))
        if not text:
            return None
        first = text.splitlines()[0]
        parts = [p.strip() for p in first.split("|") if p.strip()]
        company = parts[0] if parts else ""
        title = next((p for p in parts[1:] if _ROLE_WORDS.search(p)), "")
        location = next((p for p in parts[1:] if p != title and (looks_remote(p) or re.search(
            r"\b(onsite|on-site|hybrid|[A-Z][a-z]+,\s*[A-Z]{2})\b", p))), "")
        return Job(
            source="hn_hiring",
            external_id=str(hit["objectID"]),
            title=truncate(title or first, 140),
            company=truncate(company, 80),
            location=location,
            remote=looks_remote(first),
            url=f"https://news.ycombinator.com/item?id={hit['objectID']}",
            description=text,
            posted_at=parse_date(hit.get("created_at_i") or hit.get("created_at")),
            board="Hacker News",
        )

    def fetch(self) -> list[Job]:
        stories = self.http.get_json(
            "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10"
        )
        story = next(
            (h for h in stories.get("hits", []) if "who is hiring" in (h.get("title") or "").lower()),
            None,
        )
        if not story:
            return []
        story_id = story["objectID"]
        jobs: list[Job] = []
        for page in range(int(self.spec.get("max_pages", 5))):
            data = self.http.get_json(
                f"https://hn.algolia.com/api/v1/search_by_date?tags=comment,story_{story_id}"
                f"&hitsPerPage=200&page={page}"
            )
            for hit in data.get("hits", []):
                if str(hit.get("parent_id")) != str(story_id):
                    continue  # replies, not job posts
                job = self.parse_comment(hit)
                if job:
                    jobs.append(job)
            if page + 1 >= int(data.get("nbPages", 1)):
                break
        return jobs


class RSSSource(Source):
    """Any RSS/Atom job feed (e.g. We Work Remotely, Dice, company blogs)."""

    kind = "rss"
    board = "RSS"
    default_interval_minutes = 15

    def validate(self) -> None:
        self.require("url")

    @property
    def name(self) -> str:
        return self.spec.get("label") or self.spec["url"]

    def parse_feed(self, xml_text: str) -> list[Job]:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        jobs = []
        label = self.spec.get("label") or "RSS"
        for it in items:
            def text(tag: str) -> str:
                el = it.find(tag) if not tag.startswith("atom:") else it.find(tag, ns)
                return (el.text or "").strip() if el is not None and el.text else ""

            title = text("title") or text("atom:title")
            link = text("link")
            if not link:
                el = it.find("atom:link", ns)
                link = el.get("href", "") if el is not None else ""
            guid = text("guid") or text("atom:id") or link
            desc = text("description") or text("atom:content") or text("atom:summary")
            company = text("company") or ""
            # "Company: Title" is a common feed convention (We Work Remotely uses it).
            if not company and ": " in title:
                company, title = title.split(": ", 1)
            region = text("region") or text("location")
            jobs.append(Job(
                source=f"rss:{self.spec['url']}",
                external_id=hashlib.sha1(guid.encode()).hexdigest()[:20],
                title=title,
                company=company,
                location=region,
                remote=looks_remote(region, title, self.spec["url"]),
                url=link,
                description=html_to_text(desc),
                posted_at=parse_date(text("pubDate") or text("atom:published") or text("atom:updated")),
                board=label,
            ))
        return jobs

    def fetch(self) -> list[Job]:
        return self.parse_feed(self.http.get_text(self.spec["url"]))


class JSearchSource(Source):
    """LinkedIn, Indeed, Glassdoor, ZipRecruiter and more through Google for Jobs.

    Uses the JSearch API on RapidAPI (free tier available). This is the reliable,
    terms-friendly way to search the big boards that block scrapers.
    """

    kind = "jsearch"
    board = "JSearch"
    default_interval_minutes = 60  # free tier has a monthly request quota

    def validate(self) -> None:
        self.require("query", "api_key")

    @property
    def name(self) -> str:
        return self.spec["query"]

    @staticmethod
    def parse_job(item: dict) -> Job:
        loc = ", ".join(x for x in (item.get("job_city"), item.get("job_state"), item.get("job_country")) if x)
        salary = None
        if item.get("job_min_salary") and item.get("job_max_salary"):
            period = (item.get("job_salary_period") or "").lower()
            salary = f"${int(item['job_min_salary']):,}–${int(item['job_max_salary']):,} {period}".strip()
        return Job(
            source="jsearch",
            external_id=str(item["job_id"]),
            title=(item.get("job_title") or "").strip(),
            company=item.get("employer_name", ""),
            location=loc or ("Remote" if item.get("job_is_remote") else ""),
            remote=bool(item.get("job_is_remote")),
            url=item.get("job_apply_link") or item.get("job_google_link") or "",
            apply_url=item.get("job_apply_link"),
            description=item.get("job_description") or "",
            posted_at=parse_date(item.get("job_posted_at_datetime_utc") or item.get("job_posted_at_timestamp")),
            salary=salary,
            employment_type=item.get("job_employment_type"),
            board=item.get("job_publisher") or "JSearch",
        )

    def fetch(self) -> list[Job]:
        params = {
            "query": self.spec["query"],
            "page": "1",
            "num_pages": str(self.spec.get("pages", 1)),
            "date_posted": self.spec.get("date_posted", "today"),
        }
        if self.spec.get("remote_only"):
            params["remote_jobs_only"] = "true"
        if self.spec.get("country"):
            params["country"] = self.spec["country"]
        data = self.http.get_json(
            "https://jsearch.p.rapidapi.com/search",
            params=params,
            headers={"X-RapidAPI-Key": self.spec["api_key"], "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
        )
        return [self.parse_job(it) for it in data.get("data", [])]


class AdzunaSource(Source):
    """Adzuna job search API (free key at developer.adzuna.com)."""

    kind = "adzuna"
    board = "Adzuna"
    default_interval_minutes = 30

    def validate(self) -> None:
        self.require("what", "app_id", "app_key")

    @property
    def name(self) -> str:
        return f"{self.spec['what']}@{self.spec.get('where', '')}"

    @staticmethod
    def parse_job(item: dict) -> Job:
        location = (item.get("location") or {}).get("display_name", "")
        salary = None
        if item.get("salary_min") and item.get("salary_max"):
            salary = f"{int(item['salary_min']):,}–{int(item['salary_max']):,}"
        return Job(
            source="adzuna",
            external_id=str(item["id"]),
            title=html_to_text(item.get("title")),
            company=(item.get("company") or {}).get("display_name", ""),
            location=location,
            remote=looks_remote(location, item.get("title")),
            url=item.get("redirect_url", ""),
            description=html_to_text(item.get("description")),
            posted_at=parse_date(item.get("created")),
            salary=salary,
            employment_type=item.get("contract_time"),
            board="Adzuna",
        )

    def fetch(self) -> list[Job]:
        country = self.spec.get("country", "us")
        params = {
            "app_id": self.spec["app_id"],
            "app_key": self.spec["app_key"],
            "what": self.spec["what"],
            "results_per_page": "50",
            "max_days_old": str(self.spec.get("max_days_old", 2)),
            "sort_by": "date",
            "content-type": "application/json",
        }
        if self.spec.get("where"):
            params["where"] = self.spec["where"]
        data = self.http.get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params=params)
        return [self.parse_job(it) for it in data.get("results", [])]

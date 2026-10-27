"""Company applicant-tracking systems with public job-board APIs.

These are the same endpoints the companies' own career pages call, so they are
fast, reliable and meant to be read. New postings show up within minutes.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..models import Job
from ..textutil import html_to_text, looks_remote, parse_date
from .base import Source, SourceError


class GreenhouseSource(Source):
    kind = "greenhouse"
    board = "Greenhouse"

    def validate(self) -> None:
        self.require("company")

    @staticmethod
    def parse_job(item: dict, company: str, token: str) -> Job:
        location = (item.get("location") or {}).get("name", "")
        depts = item.get("departments") or []
        return Job(
            source=f"greenhouse:{token}",
            external_id=str(item["id"]),
            title=item.get("title", "").strip(),
            company=item.get("company_name") or company,
            location=location,
            remote=looks_remote(location),
            url=item.get("absolute_url") or f"https://job-boards.greenhouse.io/{token}/jobs/{item['id']}",
            description=html_to_text(item.get("content")),
            posted_at=parse_date(item.get("first_published") or item.get("updated_at")),
            department=depts[0].get("name") if depts else None,
            board="Greenhouse",
        )

    def fetch(self) -> list[Job]:
        token = self.spec["company"]
        data = self.http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
        return [self.parse_job(it, self.company_label, token) for it in data.get("jobs", [])]


class LeverSource(Source):
    kind = "lever"
    board = "Lever"

    def validate(self) -> None:
        self.require("company")

    @staticmethod
    def parse_job(item: dict, company: str, slug: str) -> Job:
        cats = item.get("categories") or {}
        location = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        parts = [item.get("descriptionPlain") or html_to_text(item.get("description"))]
        for lst in item.get("lists") or []:
            parts.append(lst.get("text", ""))
            parts.append(html_to_text(f"<ul>{lst.get('content', '')}</ul>"))
        parts.append(item.get("additionalPlain") or html_to_text(item.get("additional")))
        workplace = item.get("workplaceType") or ""
        return Job(
            source=f"lever:{slug}",
            external_id=item["id"],
            title=item.get("text", "").strip(),
            company=company,
            location=location,
            remote=workplace == "remote" or looks_remote(location),
            url=item.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{item['id']}",
            apply_url=item.get("applyUrl"),
            description="\n\n".join(p for p in parts if p).strip(),
            posted_at=parse_date(item.get("createdAt")),
            employment_type=cats.get("commitment"),
            department=cats.get("team") or cats.get("department"),
            board="Lever",
        )

    def fetch(self) -> list[Job]:
        slug = self.spec["company"]
        host = "api.eu.lever.co" if self.spec.get("region") == "eu" else "api.lever.co"
        data = self.http.get_json(f"https://{host}/v0/postings/{slug}?mode=json")
        return [self.parse_job(it, self.company_label, slug) for it in data]


class AshbySource(Source):
    kind = "ashby"
    board = "Ashby"

    def validate(self) -> None:
        self.require("company")

    @staticmethod
    def parse_job(item: dict, company: str, slug: str) -> Job:
        location = item.get("location") or ""
        extra = [s.get("location", "") for s in item.get("secondaryLocations") or [] if isinstance(s, dict)]
        if extra:
            location = "; ".join([location, *extra])
        comp = (item.get("compensation") or {}).get("compensationTierSummary")
        return Job(
            source=f"ashby:{slug}",
            external_id=item["id"],
            title=item.get("title", "").strip(),
            company=company,
            location=location,
            remote=bool(item.get("isRemote")) or item.get("workplaceType") == "Remote" or looks_remote(location),
            url=item.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{item['id']}",
            apply_url=item.get("applyUrl"),
            description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml")),
            posted_at=parse_date(item.get("publishedAt")),
            salary=comp,
            employment_type=item.get("employmentType"),
            department=item.get("department") or item.get("team"),
            board="Ashby",
        )

    def fetch(self) -> list[Job]:
        slug = self.spec["company"]
        data = self.http.get_json(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
        )
        return [
            self.parse_job(it, self.company_label, slug)
            for it in data.get("jobs", [])
            if it.get("isListed", True)
        ]


class SmartRecruitersSource(Source):
    kind = "smartrecruiters"
    board = "SmartRecruiters"

    def validate(self) -> None:
        self.require("company")

    def _detail(self, company_id: str, posting_id: str) -> str:
        try:
            data = self.http.get_json(
                f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{posting_id}"
            )
        except Exception:
            return ""
        return self.sections_text(data)

    @staticmethod
    def sections_text(data: dict) -> str:
        sections = ((data.get("jobAd") or {}).get("sections")) or {}
        parts = []
        for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
            sec = sections.get(key) or {}
            if sec.get("text"):
                parts.append(f"{sec.get('title', '')}\n{html_to_text(sec['text'])}".strip())
        return "\n\n".join(parts)

    @staticmethod
    def parse_job(item: dict, company_id: str, description: str = "") -> Job:
        loc = item.get("location") or {}
        location = loc.get("fullLocation") or ", ".join(
            x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x
        )
        return Job(
            source=f"smartrecruiters:{company_id}",
            external_id=str(item["id"]),
            title=item.get("name", "").strip(),
            company=(item.get("company") or {}).get("name") or company_id,
            location=location,
            remote=bool(loc.get("remote")) or looks_remote(location),
            url=f"https://jobs.smartrecruiters.com/{company_id}/{item['id']}",
            description=description,
            posted_at=parse_date(item.get("releasedDate")),
            employment_type=(item.get("typeOfEmployment") or {}).get("label"),
            department=(item.get("department") or {}).get("label"),
            board="SmartRecruiters",
        )

    def fetch(self) -> list[Job]:
        company_id = self.spec["company"]
        url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings?limit=100"
        if self.spec.get("search"):
            url += f"&q={self.spec['search']}"
        data = self.http.get_json(url)
        jobs = []
        # Descriptions need one request each, so only fetch them for jobs we haven't stored yet.
        for item in data.get("content", []):
            desc = ""
            if not self.is_known(f"smartrecruiters:{company_id}", str(item["id"])):
                desc = self._detail(company_id, item["id"])
            jobs.append(self.parse_job(item, company_id, desc))
        return jobs


_WORKDAY_RE = re.compile(
    r"^https?://(?P<tenant>[\w-]+)\.(?P<dc>wd\d+)\.myworkdayjobs\.com/(?:(?P<locale>[a-z]{2}-[A-Z]{2})/)?(?P<site>[\w-]+)"
)


def parse_workday_url(url: str) -> tuple[str, str, str] | None:
    """Return (host, tenant, site) for a Workday career-site URL."""
    m = _WORKDAY_RE.match(url.strip())
    if not m:
        return None
    host = urlparse(url).netloc
    return host, m.group("tenant"), m.group("site")


class WorkdaySource(Source):
    kind = "workday"
    board = "Workday"

    def validate(self) -> None:
        self.require("url")
        if not parse_workday_url(self.spec["url"]):
            raise SourceError(
                "workday url should look like https://acme.wd5.myworkdayjobs.com/AcmeCareers"
            )

    @property
    def name(self) -> str:
        parsed = parse_workday_url(self.spec["url"])
        return f"{parsed[1]}/{parsed[2]}" if parsed else self.spec["url"]

    @staticmethod
    def parse_detail(data: dict, host: str, site: str, path: str, company: str) -> Job:
        info = data.get("jobPostingInfo") or {}
        org = (data.get("hiringOrganization") or {}).get("name")
        location = info.get("location") or ""
        extra = info.get("additionalLocations") or []
        if extra:
            location = "; ".join([location, *extra])
        return Job(
            source=f"workday:{host}/{site}",
            external_id=path,
            title=info.get("title", "").strip(),
            company=org or company,
            location=location,
            remote=looks_remote(location, info.get("remoteType")),
            url=info.get("externalUrl") or f"https://{host}/{site}{path}",
            description=html_to_text(info.get("jobDescription")),
            posted_at=parse_date(info.get("startDate")),
            employment_type=info.get("timeType"),
            board="Workday",
        )

    def fetch(self) -> list[Job]:
        host, tenant, site = parse_workday_url(self.spec["url"])
        api = f"https://{host}/wday/cxs/{tenant}/{site}"
        payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": self.spec.get("search", "")}
        listings: list[dict] = []
        max_jobs = int(self.spec.get("max_jobs", 60))
        while len(listings) < max_jobs:
            page = self.http.post_json(f"{api}/jobs", payload)
            batch = page.get("jobPostings") or []
            listings.extend(batch)
            if len(batch) < payload["limit"] or len(listings) >= int(page.get("total") or 0):
                break
            payload["offset"] += payload["limit"]
        company = self.company_label or tenant.title()
        source = f"workday:{host}/{site}"
        jobs = []
        for item in listings[:max_jobs]:
            path = item.get("externalPath")
            if not path or self.is_known(source, path):
                continue
            try:
                detail = self.http.get_json(f"{api}{path}")
                job = self.parse_detail(detail, host, site, path, company)
            except Exception:
                job = Job(
                    source=source,
                    external_id=path,
                    title=item.get("title", ""),
                    company=company,
                    location=item.get("locationsText", ""),
                    remote=looks_remote(item.get("locationsText")),
                    url=f"https://{host}/{site}{path}",
                    board="Workday",
                )
            jobs.append(job)
        return jobs

    @property
    def company_label(self) -> str:
        return str(self.spec.get("label") or "")

"""Watch any company careers page, including small startups with hand-built pages."""

from __future__ import annotations

from urllib.parse import urlparse

from ..models import Job
from .base import Source


class CareersPageSource(Source):
    """Point this at https://company.com/careers and Jobly figures out the rest.

    1. If the page is powered by Greenhouse / Lever / Ashby / Workday / SmartRecruiters,
       Jobly switches to that system's API automatically.
    2. Otherwise it reads schema.org JobPosting data on the page.
    3. Otherwise it collects links that look like job postings.
    """

    kind = "careers_page"
    board = "Company site"
    default_interval_minutes = 30

    def validate(self) -> None:
        self.require("url")
        self._delegate: Source | None = None
        self._resolved = False

    @property
    def name(self) -> str:
        return self.spec["url"]

    @property
    def company_label(self) -> str:
        if self.spec.get("label"):
            return self.spec["label"]
        host = urlparse(self.spec["url"]).netloc.removeprefix("www.")
        return host.split(".")[0].replace("-", " ").title()

    def _resolve(self, html: str) -> None:
        from ..importer import detect_board, detect_board_in_html
        from ..registry import build_source

        spec = detect_board(self.spec["url"]) or detect_board_in_html(html)
        if spec:
            spec.setdefault("label", self.company_label)
            if self.spec.get("search"):
                spec["search"] = self.spec["search"]
            self._delegate = build_source(spec, self.http)
            self._delegate.known_checker = self.known_checker
        self._resolved = True

    def fetch(self) -> list[Job]:
        from ..importer import jobs_from_jsonld, jobs_from_links

        if self._delegate:
            return self._delegate.fetch()
        html = self.http.get_text(self.spec["url"], browser=True)
        if not self._resolved:
            self._resolve(html)
            if self._delegate:
                return self._delegate.fetch()
        source = f"careers_page:{self.spec['url']}"
        jobs = jobs_from_jsonld(html, self.spec["url"], source=source)
        if not jobs:
            jobs = jobs_from_links(html, self.spec["url"], source, self.company_label)
        for job in jobs:
            job.company = job.company or self.company_label
        return jobs

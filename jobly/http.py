"""Shared HTTP client with sane timeouts, a clear user agent and polite retries."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; jobly/0.1; personal job search)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class Http:
    def __init__(self, client: httpx.Client | None = None, retries: int = 2):
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        self.retries = retries

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.request(method, url, **kw)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    wait = float(resp.headers.get("retry-after", 2 ** (attempt + 1)) or 2)
                    time.sleep(min(wait, 30))
                    continue
                resp.raise_for_status()
                return resp
            except httpx.TransportError as e:
                last = e
                if attempt < self.retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise
        raise last or RuntimeError("request failed")  # pragma: no cover

    def get_json(self, url: str, **kw):
        return self.request("GET", url, **kw).json()

    def post_json(self, url: str, payload: dict, **kw):
        return self.request("POST", url, json=payload, **kw).json()

    def get_text(self, url: str, browser: bool = False, **kw) -> str:
        if browser:
            headers = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml"}
            headers.update(kw.pop("headers", {}))
            kw["headers"] = headers
        return self.request("GET", url, **kw).text

    def close(self) -> None:
        self.client.close()

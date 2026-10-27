"""Build Source objects from config entries."""

from __future__ import annotations

import logging
from typing import Any

from .http import Http
from .sources.ats import AshbySource, GreenhouseSource, LeverSource, SmartRecruitersSource, WorkdaySource
from .sources.base import Source, SourceError
from .sources.boards import (
    AdzunaSource,
    HackerNewsHiringSource,
    JSearchSource,
    RemoteOKSource,
    RemotiveSource,
    RSSSource,
)
from .sources.careers import CareersPageSource
from .sources.email_alerts import EmailAlertsSource

log = logging.getLogger(__name__)

SOURCE_TYPES: dict[str, type[Source]] = {
    cls.kind: cls
    for cls in (
        GreenhouseSource,
        LeverSource,
        AshbySource,
        WorkdaySource,
        SmartRecruitersSource,
        CareersPageSource,
        EmailAlertsSource,
        JSearchSource,
        AdzunaSource,
        RemotiveSource,
        RemoteOKSource,
        HackerNewsHiringSource,
        RSSSource,
    )
}


def build_source(spec: dict[str, Any], http: Http) -> Source:
    kind = spec.get("type")
    cls = SOURCE_TYPES.get(kind or "")
    if not cls:
        raise SourceError(f"unknown source type {kind!r}; choose from {', '.join(sorted(SOURCE_TYPES))}")
    return cls(spec, http)


def build_sources(specs: list[dict[str, Any]], http: Http) -> tuple[list[Source], list[str]]:
    """Build every valid source; return (sources, error messages for invalid ones)."""
    sources, errors = [], []
    for spec in specs:
        if spec.get("enabled") is False:
            continue
        try:
            sources.append(build_source(spec, http))
        except SourceError as e:
            errors.append(str(e))
            log.warning("skipping source %s: %s", spec, e)
    return sources, errors

"""Base class every job source implements."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, ClassVar

from ..http import Http
from ..models import Job, make_job_id

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    pass


class Source(ABC):
    """A place jobs come from. ``fetch()`` returns the jobs currently listed there."""

    kind: ClassVar[str]
    board: ClassVar[str]
    default_interval_minutes: ClassVar[float | None] = None

    def __init__(self, spec: dict[str, Any], http: Http):
        self.spec = spec
        self.http = http
        # Set by the scout so sources can skip detail requests for jobs already stored.
        self.known_checker: Callable[[str], bool] = lambda job_id: False
        self.validate()

    def is_known(self, source: str, external_id: str) -> bool:
        return self.known_checker(make_job_id(source, external_id))

    def validate(self) -> None:
        """Raise SourceError if the spec is missing required options."""

    def require(self, *names: str) -> None:
        missing = [n for n in names if not self.spec.get(n)]
        if missing:
            raise SourceError(f"{self.kind} source needs: {', '.join(missing)}")

    @property
    def name(self) -> str:
        return str(self.spec.get("company") or self.spec.get("name") or self.spec.get("url") or self.kind)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"

    def interval_minutes(self, default: float) -> float:
        return float(self.spec.get("interval_minutes") or self.default_interval_minutes or default)

    @property
    def company_label(self) -> str:
        """Display name for single-company sources (``label:`` overrides)."""
        if self.spec.get("label"):
            return str(self.spec["label"])
        raw = str(self.spec.get("company") or "")
        return raw.replace("-", " ").replace("_", " ").title() if raw.islower() else raw

    @abstractmethod
    def fetch(self) -> list[Job]:
        ...

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.key}>"

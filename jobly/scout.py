"""The scout: polls every source on its own schedule, stores new matches, sends alerts."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from .config import Config
from .http import Http
from .matcher import evaluate
from .models import Job, Profile, utcnow
from .notify import Notifier
from .registry import build_sources
from .sources.base import Source
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class SourceRun:
    key: str
    fetched: int = 0
    stored: int = 0
    new: int = 0
    error: str = ""


@dataclass
class ScoutReport:
    runs: list[SourceRun] = field(default_factory=list)
    new_matches: list[tuple[Job, int]] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return sum(r.new for r in self.runs)


class Scout:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        http: Http | None = None,
        profile_provider: Callable[[], Profile | None] = lambda: None,
        notifier: Notifier | None = None,
        on_new_match: Callable[[Job, int], None] | None = None,
        sources: list[Source] | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.http = http or Http()
        self.profile_provider = profile_provider
        self.notifier = notifier
        self.on_new_match = on_new_match
        self.source_errors: list[str] = []
        self._fixed_sources = sources
        self._sources: list[Source] = []
        self._next_due: dict[str, float] = {}
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.reload_sources()

    # ------------------------------------------------------------- sources

    def reload_sources(self) -> None:
        if self._fixed_sources is not None:
            sources = self._fixed_sources
            self.source_errors = []
        else:
            specs = list(self.cfg.sources) + [spec for _id, spec in self.store.custom_sources()]
            sources, self.source_errors = build_sources(specs, self.http)
        for src in sources:
            src.known_checker = self.store.has_job
        with self._lock:
            self._sources = sources
            self._next_due = {s.key: self._next_due.get(s.key, 0.0) for s in sources}

    @property
    def sources(self) -> list[Source]:
        return list(self._sources)

    # ----------------------------------------------------------- scanning

    def _profile(self) -> Profile | None:
        try:
            return self.profile_provider()
        except Exception as e:
            log.warning("could not load profile: %s", e)
            return None

    def run_source(self, src: Source, profile: Profile | None = None) -> tuple[SourceRun, list[tuple[Job, int]]]:
        run = SourceRun(src.key)
        matches: list[tuple[Job, int]] = []
        first_run = not self.store.source_has_succeeded(src.key)
        try:
            jobs = src.fetch()
        except Exception as e:
            run.error = f"{type(e).__name__}: {e}"[:500]
            log.warning("source %s failed: %s", src.key, run.error)
            self.store.record_source_run(src.key, 0, run.error)
            return run, matches
        run.fetched = len(jobs)
        alert_cutoff = utcnow() - timedelta(hours=self.cfg.scout.first_run_alert_hours)
        for job in jobs:
            if not job.title or not job.url:
                continue
            result = evaluate(job, self.cfg.search, profile)
            if not result.passed:
                continue
            if not self.store.has_job(job.id) and self.store.find_duplicate(job):
                continue  # same role already found on another board
            run.stored += 1
            if self.store.upsert_job(job, result.score, result.reasons):
                run.new += 1
                if first_run and not (job.posted_at and job.posted_at >= alert_cutoff):
                    continue  # don't flood you with the backlog on the first scan
                matches.append((job, result.score))
        self.store.record_source_run(src.key, run.fetched)
        return run, matches

    def _claim(self, only_due: bool) -> list[Source]:
        now = time.monotonic()
        with self._lock:
            todo = [
                s for s in self._sources
                if s.key not in self._in_flight and (not only_due or self._next_due.get(s.key, 0) <= now)
            ]
            self._in_flight.update(s.key for s in todo)
        return todo

    def _execute(self, src: Source, profile: Profile | None) -> tuple[SourceRun, list[tuple[Job, int]]]:
        try:
            return self.run_source(src, profile)
        finally:
            minutes = src.interval_minutes(self.cfg.scout.default_interval_minutes)
            with self._lock:
                self._in_flight.discard(src.key)
                self._next_due[src.key] = time.monotonic() + minutes * 60

    def _after(self, matches: list[tuple[Job, int]]) -> None:
        if not matches:
            return
        if self.notifier:
            self.notifier.notify(matches)
        if self.on_new_match:
            for job, score in matches:
                try:
                    self.on_new_match(job, score)
                except Exception as e:
                    log.warning("on_new_match failed for %s: %s", job.id, e)

    def run_once(self, only_due: bool = False) -> ScoutReport:
        """Scan sources now (all of them, or only those whose interval has elapsed)."""
        report = ScoutReport()
        todo = self._claim(only_due)
        if not todo:
            return report
        profile = self._profile()
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.scout.max_workers)) as pool:
            for run, matches in pool.map(lambda s: self._execute(s, profile), todo):
                report.runs.append(run)
                report.new_matches.extend(matches)
        self._after(report.new_matches)
        log.info("scan: %d sources, %d new jobs, %d alerts", len(report.runs), report.new_count,
                 len(report.new_matches))
        return report

    # ------------------------------------------------------- background loop

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="jobly-scout", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        # Each source runs on its own schedule, so a slow site never delays fast ones.
        pool = ThreadPoolExecutor(max_workers=max(1, self.cfg.scout.max_workers), thread_name_prefix="jobly-src")
        try:
            while not self._stop.is_set():
                todo = self._claim(only_due=True)
                if todo:
                    profile = self._profile()
                    for src in todo:
                        pool.submit(self._loop_task, src, profile)
                self._stop.wait(5)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _loop_task(self, src: Source, profile: Profile | None) -> None:
        try:
            _run, matches = self._execute(src, profile)
            self._after(matches)
        except Exception:  # pragma: no cover - keep the loop alive no matter what
            log.exception("error scanning %s", src.key)

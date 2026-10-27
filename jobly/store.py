"""SQLite storage for jobs, generated application kits and source health."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Job, utcnow
from .textutil import parse_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    fingerprint TEXT,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    remote INTEGER DEFAULT 0,
    url TEXT,
    apply_url TEXT,
    description TEXT,
    posted_at TEXT,
    discovered_at TEXT NOT NULL,
    salary TEXT,
    employment_type TEXT,
    department TEXT,
    board TEXT,
    score INTEGER DEFAULT 0,
    match_reasons TEXT DEFAULT '[]',
    status TEXT DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered ON jobs(discovered_at);
CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS kits (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    stage TEXT DEFAULT '',
    error TEXT DEFAULT '',
    analysis TEXT,
    resume TEXT,
    cover_letter TEXT,
    learning_plan TEXT,
    model TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS source_status (
    key TEXT PRIMARY KEY,
    last_run TEXT,
    last_ok TEXT,
    last_error TEXT,
    last_count INTEGER DEFAULT 0,
    runs INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS custom_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

JOB_STATUSES = ("new", "saved", "applied", "interviewing", "offer", "dismissed")
KIT_PARTS = ("analysis", "resume", "cover_letter", "learning_plan")


class StoredJob(Job):
    discovered_at: datetime
    score: int = 0
    match_reasons: list[str] = []
    status: str = "new"
    kit_status: str | None = None


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ jobs

    def has_job(self, job_id: str) -> bool:
        return bool(self._query("SELECT 1 FROM jobs WHERE id=?", (job_id,)))

    def find_duplicate(self, job: Job, within_days: int = 45) -> str | None:
        """Same company + title seen recently on another board."""
        if not job.company.strip():
            return None
        since = _iso(utcnow() - timedelta(days=within_days))
        rows = self._query(
            "SELECT id FROM jobs WHERE fingerprint=? AND id<>? AND discovered_at>=? LIMIT 1",
            (job.fingerprint, job.id, since),
        )
        return rows[0]["id"] if rows else None

    def upsert_job(self, job: Job, score: int = 0, reasons: list[str] | None = None) -> bool:
        """Insert a job. Returns True if it was new; otherwise fills in missing details."""
        with self._lock:
            existing = self._conn.execute(
                "SELECT description, company, location FROM jobs WHERE id=?", (job.id,)
            ).fetchone()
            if existing:
                updates: dict[str, Any] = {}
                if len(job.description) > len(existing["description"] or ""):
                    updates["description"] = job.description
                    updates["score"] = score
                    updates["match_reasons"] = json.dumps(reasons or [])
                if job.company and not existing["company"]:
                    updates["company"] = job.company
                    updates["fingerprint"] = job.fingerprint
                if job.location and not existing["location"]:
                    updates["location"] = job.location
                if updates:
                    sets = ", ".join(f"{k}=:{k}" for k in updates)
                    self._conn.execute(f"UPDATE jobs SET {sets} WHERE id=:id", {**updates, "id": job.id})
                    self._conn.commit()
                return False
            self._conn.execute(
                """INSERT INTO jobs (id, source, external_id, fingerprint, title, company, location,
                       remote, url, apply_url, description, posted_at, discovered_at, salary,
                       employment_type, department, board, score, match_reasons, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new')""",
                (
                    job.id, job.source, job.external_id, job.fingerprint, job.title, job.company,
                    job.location, int(job.remote), job.url, job.apply_url, job.description,
                    _iso(job.posted_at), _iso(utcnow()), job.salary, job.employment_type,
                    job.department, job.board, score, json.dumps(reasons or []),
                ),
            )
            self._conn.commit()
            return True

    def _row_to_job(self, r: sqlite3.Row) -> StoredJob:
        d = dict(r)
        d["remote"] = bool(d.get("remote"))
        d["posted_at"] = parse_date(d.get("posted_at"))
        d["discovered_at"] = parse_date(d.get("discovered_at"))
        d["match_reasons"] = json.loads(d.get("match_reasons") or "[]")
        d.pop("id", None)
        d.pop("fingerprint", None)
        return StoredJob.model_validate(d)

    def get_job(self, job_id: str) -> StoredJob | None:
        rows = self._query(
            "SELECT j.*, k.status AS kit_status FROM jobs j LEFT JOIN kits k ON k.job_id=j.id WHERE j.id=?",
            (job_id,),
        )
        return self._row_to_job(rows[0]) if rows else None

    def list_jobs(
        self,
        status: str | None = None,
        min_score: int | None = None,
        q: str | None = None,
        since: datetime | None = None,
        include_dismissed: bool = False,
        limit: int = 300,
    ) -> list[StoredJob]:
        where, params = [], []
        if status:
            where.append("j.status=?")
            params.append(status)
        elif not include_dismissed:
            where.append("j.status<>'dismissed'")
        if min_score:
            where.append("j.score>=?")
            params.append(min_score)
        if q:
            where.append("(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ? OR j.description LIKE ?)")
            params.extend([f"%{q}%"] * 4)
        if since:
            where.append("j.discovered_at>?")
            params.append(_iso(since))
        sql = "SELECT j.*, k.status AS kit_status FROM jobs j LEFT JOIN kits k ON k.job_id=j.id"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY j.discovered_at DESC, COALESCE(j.posted_at, '') DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_job(r) for r in self._query(sql, tuple(params))]

    def set_status(self, job_id: str, status: str) -> None:
        if status not in JOB_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        self._exec("UPDATE jobs SET status=? WHERE id=?", (status, job_id))

    def update_description(self, job_id: str, description: str, score: int | None = None,
                           reasons: list[str] | None = None) -> None:
        self._exec("UPDATE jobs SET description=? WHERE id=?", (description, job_id))
        if score is not None:
            self._exec("UPDATE jobs SET score=?, match_reasons=? WHERE id=?",
                       (score, json.dumps(reasons or []), job_id))

    def counts(self) -> dict[str, int]:
        rows = self._query("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    # ------------------------------------------------------------------ kits

    def get_kit(self, job_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM kits WHERE job_id=?", (job_id,))
        if not rows:
            return None
        kit = dict(rows[0])
        for part in KIT_PARTS:
            kit[part] = json.loads(kit[part]) if kit.get(part) else None
        return kit

    def set_kit_state(self, job_id: str, status: str, stage: str = "", error: str = "",
                      model: str | None = None) -> None:
        now = _iso(utcnow())
        self._exec(
            """INSERT INTO kits (job_id, status, stage, error, model, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, stage=excluded.stage,
                   error=excluded.error, model=COALESCE(excluded.model, kits.model),
                   updated_at=excluded.updated_at""",
            (job_id, status, stage, error, model, now, now),
        )

    def save_kit_part(self, job_id: str, part: str, data: Any) -> None:
        if part not in KIT_PARTS:
            raise ValueError(part)
        self._exec(f"UPDATE kits SET {part}=?, updated_at=? WHERE job_id=?",
                   (json.dumps(data), _iso(utcnow()), job_id))

    def all_analyses(self) -> list[tuple[str, dict]]:
        rows = self._query("SELECT job_id, analysis FROM kits WHERE analysis IS NOT NULL")
        return [(r["job_id"], json.loads(r["analysis"])) for r in rows]

    # ---------------------------------------------------------- source health

    def record_source_run(self, key: str, count: int = 0, error: str | None = None) -> None:
        now = _iso(utcnow())
        self._exec(
            """INSERT INTO source_status (key, last_run, last_ok, last_error, last_count, runs)
               VALUES (?,?,?,?,?,1)
               ON CONFLICT(key) DO UPDATE SET last_run=excluded.last_run,
                   last_ok=COALESCE(excluded.last_ok, source_status.last_ok),
                   last_error=excluded.last_error, last_count=excluded.last_count,
                   runs=source_status.runs+1""",
            (key, now, None if error else now, error or "", count),
        )

    def source_has_succeeded(self, key: str) -> bool:
        rows = self._query("SELECT last_ok FROM source_status WHERE key=?", (key,))
        return bool(rows and rows[0]["last_ok"])

    def source_statuses(self) -> dict[str, dict]:
        return {r["key"]: dict(r) for r in self._query("SELECT * FROM source_status")}

    # -------------------------------------------------------- custom sources

    def add_custom_source(self, spec: dict) -> int:
        cur = self._exec("INSERT INTO custom_sources (spec, created_at) VALUES (?,?)",
                         (json.dumps(spec), _iso(utcnow())))
        return int(cur.lastrowid)

    def custom_sources(self) -> list[tuple[int, dict]]:
        return [(r["id"], json.loads(r["spec"])) for r in self._query("SELECT * FROM custom_sources ORDER BY id")]

    def delete_custom_source(self, source_id: int) -> None:
        self._exec("DELETE FROM custom_sources WHERE id=?", (source_id,))

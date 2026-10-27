"""Application service: wires config, storage, the scout and the AI kit builder together."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from pydantic import ValidationError

from .config import Config, Paths, load_config, load_profile
from .http import Http
from .importer import NeedsManualPaste, detect_board, fetch_job_from_url, manual_job
from .matcher import evaluate
from .models import Job, Profile
from .notify import Notifier
from .scout import Scout
from .store import Store, StoredJob
from .tailor import render
from .tailor.assemble import ResumeDoc
from .tailor.llm import LLM, ClaudeLLM
from .tailor.pipeline import Tailor
from .tailor.schemas import CoverLetter, LearningPlan

log = logging.getLogger(__name__)

MIN_DESCRIPTION = 200


class JoblyError(RuntimeError):
    pass


def slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-") or "job"


class Jobly:
    def __init__(self, paths: Paths | None = None, llm: LLM | None = None, http: Http | None = None,
                 app_url: str = "http://localhost:8000"):
        self.paths = paths or Paths()
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.cfg: Config = load_config(self.paths.config)
        self.store = Store(self.paths.db)
        self.http = http or Http()
        self._llm = llm
        self.notifier = Notifier(self.cfg.notify, app_url=app_url)
        self.scout = Scout(
            self.cfg, self.store, self.http,
            profile_provider=self.load_profile,
            notifier=self.notifier,
            on_new_match=self._on_new_match,
        )
        self._kit_pool = ThreadPoolExecutor(max_workers=max(1, self.cfg.ai.max_parallel_kits),
                                            thread_name_prefix="jobly-kit")

    # -------------------------------------------------------------- profile

    def load_profile(self) -> Profile | None:
        if not self.paths.profile.exists():
            return None
        return load_profile(self.paths.profile)

    def load_profile_safe(self) -> Profile | None:
        try:
            return self.load_profile()
        except (ValidationError, yaml.YAMLError):
            return None

    def require_profile(self) -> Profile:
        try:
            profile = self.load_profile()
        except (ValidationError, yaml.YAMLError) as e:
            raise JoblyError(f"profile.yaml has a problem: {e}") from e
        if not profile:
            raise JoblyError("No profile yet — open the Profile page or run `jobly init`.")
        return profile

    def save_profile_yaml(self, text: str) -> Profile:
        try:
            profile = Profile.model_validate(yaml.safe_load(text) or {})
        except (ValidationError, yaml.YAMLError) as e:
            raise JoblyError(str(e)) from e
        self.paths.profile.write_text(text, encoding="utf-8")
        return profile

    # ------------------------------------------------------------------- AI

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = ClaudeLLM(model=self.cfg.ai.model, effort=self.cfg.ai.effort)
        return self._llm

    @property
    def tailor(self) -> Tailor:
        return Tailor(self.llm)

    def _on_new_match(self, job: Job, score: int) -> None:
        threshold = self.cfg.ai.auto_tailor_min_score
        if threshold and score >= threshold:
            self.request_kit(job.id)

    def request_kit(self, job_id: str) -> None:
        """Queue kit generation in the background (the web UI polls for progress)."""
        kit = self.store.get_kit(job_id)
        if kit and kit["status"] in ("queued", "running"):
            return
        self.store.set_kit_state(job_id, "queued", "Waiting to start", model=self.cfg.ai.model)
        self._kit_pool.submit(self._build_kit_safe, job_id)

    def _build_kit_safe(self, job_id: str) -> None:
        try:
            self.build_kit(job_id)
        except Exception as e:
            log.exception("kit generation failed for %s", job_id)
            self.store.set_kit_state(job_id, "error", "", str(e))

    def ensure_description(self, job: StoredJob) -> StoredJob:
        """Fetch the full posting when a source only gave us a title and link (e.g. email alerts)."""
        if len(job.description) >= MIN_DESCRIPTION or job.source == "manual":
            return job
        try:
            full = fetch_job_from_url(job.url, self.http)
        except NeedsManualPaste as e:
            raise JoblyError(f"{e} Use “Paste description” on the job page.") from e
        except Exception as e:
            raise JoblyError(f"Couldn't load the full posting ({e}). Use “Paste description” on the job page.") from e
        result = evaluate(full, self.cfg.search, self.load_profile())
        self.store.update_description(job.id, full.description, result.score, result.reasons)
        return self.store.get_job(job.id)

    def build_kit(self, job_id: str) -> Path:
        job = self.store.get_job(job_id)
        if not job:
            raise JoblyError(f"No job with id {job_id}")
        profile = self.require_profile()
        self.store.set_kit_state(job_id, "running", "Loading the full job description", model=self.cfg.ai.model)
        job = self.ensure_description(job)
        if len(job.description) < MIN_DESCRIPTION:
            raise JoblyError("This job has no description yet — paste it on the job page first.")
        kit = self.tailor.build(
            profile, job,
            on_part=lambda name, data: self.store.save_kit_part(job_id, name, data.model_dump(mode="json")),
            on_stage=lambda stage: self.store.set_kit_state(job_id, "running", stage),
        )
        folder = self.export_kit(job, profile, kit.resume, kit.cover_letter, kit.learning_plan)
        self.store.set_kit_state(job_id, "done", f"Saved to {folder}")
        return folder

    def kit_folder(self, job: Job) -> Path:
        return self.paths.kits / f"{slug(job.company or 'company', 30)}--{slug(job.title)}--{job.id[:6]}"

    def export_kit(self, job: Job, profile: Profile, resume: ResumeDoc, letter: CoverLetter,
                   plan: LearningPlan) -> Path:
        folder = self.kit_folder(job)
        folder.mkdir(parents=True, exist_ok=True)
        stem = slug(profile.name, 30)
        (folder / f"{stem}-resume.docx").write_bytes(render.resume_docx(resume))
        (folder / f"{stem}-resume.html").write_text(render.resume_html(resume), encoding="utf-8")
        (folder / f"{stem}-resume.md").write_text(render.resume_markdown(resume), encoding="utf-8")
        (folder / f"{stem}-cover-letter.docx").write_bytes(render.cover_letter_docx(letter, profile))
        (folder / f"{stem}-cover-letter.txt").write_text(render.cover_letter_text(letter, profile), encoding="utf-8")
        (folder / "learning-plan.md").write_text(
            render.learning_plan_markdown(plan, job.title, job.company), encoding="utf-8")
        return folder

    def kit_parts(self, job_id: str) -> dict | None:
        """Kit as typed objects (whatever parts are finished so far)."""
        kit = self.store.get_kit(job_id)
        if not kit:
            return None
        from .tailor.schemas import JobAnalysis

        types = {"analysis": JobAnalysis, "resume": ResumeDoc, "cover_letter": CoverLetter,
                 "learning_plan": LearningPlan}
        for part, cls in types.items():
            if kit.get(part):
                kit[part] = cls.model_validate(kit[part])
        return kit

    # ------------------------------------------------------------- adding

    def score_and_store(self, job: Job) -> StoredJob:
        result = evaluate(job, self.cfg.search, self.load_profile())
        self.store.upsert_job(job, result.score, result.reasons)
        if job.description:
            self.store.update_description(job.id, job.description, result.score, result.reasons)
        return self.store.get_job(job.id)

    def import_url(self, url: str) -> StoredJob:
        try:
            job = fetch_job_from_url(url, self.http)
        except NeedsManualPaste as e:
            raise JoblyError(str(e)) from e
        except Exception as e:
            raise JoblyError(f"Couldn't import that link: {e}") from e
        return self.score_and_store(job)

    def add_manual(self, title: str, company: str, description: str, url: str = "",
                   location: str = "") -> StoredJob:
        if len(description.strip()) < 50:
            raise JoblyError("Paste the full job description (it looks too short).")
        return self.score_and_store(manual_job(title, company, description, url, location))

    def paste_description(self, job_id: str, description: str) -> StoredJob:
        job = self.store.get_job(job_id)
        if not job:
            raise JoblyError("Job not found")
        job.description = description.strip()
        result = evaluate(job, self.cfg.search, self.load_profile())
        self.store.update_description(job_id, job.description, result.score, result.reasons)
        return self.store.get_job(job_id)

    def add_company(self, url: str, search: str = "") -> dict:
        """Watch a company: detects its ATS from the careers URL, else watches the page itself."""
        url = url.strip()
        if not url.startswith("http"):
            url = "https://" + url
        spec = detect_board(url) or {"type": "careers_page", "url": url}
        if search:
            spec["search"] = search
        self.store.add_custom_source(spec)
        self.scout.reload_sources()
        return spec

    def remove_company(self, source_id: int) -> None:
        self.store.delete_custom_source(source_id)
        self.scout.reload_sources()

    # --------------------------------------------------------- skill gaps

    def skill_gaps(self) -> list[dict]:
        """Which skills keep showing up in jobs you're targeting, and which you lack."""
        agg: dict[str, dict] = defaultdict(lambda: {"name": "", "have": 0, "learnable": 0, "stretch": 0,
                                                     "required": 0, "hours": [], "jobs": []})
        for job_id, analysis in self.store.all_analyses():
            for req in analysis.get("requirements", []):
                key = " ".join(req["name"].lower().split())
                row = agg[key]
                row["name"] = row["name"] or req["name"]
                row[req["status"]] += 1
                row["required"] += req.get("priority") == "required"
                if req.get("hours_to_interview_ready"):
                    row["hours"].append(req["hours_to_interview_ready"])
                if job_id not in row["jobs"]:
                    row["jobs"].append(job_id)
        rows = []
        for row in agg.values():
            gap = row["learnable"] + row["stretch"]
            if not gap and not row["have"]:
                continue
            row["gap"] = gap
            row["avg_hours"] = round(sum(row["hours"]) / len(row["hours"])) if row["hours"] else 0
            rows.append(row)
        rows.sort(key=lambda r: (-r["gap"], -r["required"], r["name"].lower()))
        return rows

    def close(self) -> None:
        self.scout.stop()
        self._kit_pool.shutdown(wait=False, cancel_futures=True)

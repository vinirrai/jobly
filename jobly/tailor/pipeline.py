"""Build a full application kit for one job: fit analysis, resume, cover letter, learning plan."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from pydantic import BaseModel

from ..models import Job, Profile
from . import prompts
from .assemble import ResumeDoc, assemble_resume
from .llm import LLM, T
from .schemas import CoverLetter, JobAnalysis, LearningPlan, TailoredResume


class Kit(BaseModel):
    analysis: JobAnalysis
    resume: ResumeDoc
    cover_letter: CoverLetter
    learning_plan: LearningPlan


class Tailor:
    def __init__(self, llm: LLM):
        self.llm = llm

    def _ask(self, profile: Profile, prompt: str, schema: type[T]) -> T:
        return self.llm.structured(
            system=prompts.system_blocks(profile),
            messages=[{"role": "user", "content": prompt}],
            schema=schema,
        )

    def analyze(self, profile: Profile, job: Job) -> JobAnalysis:
        return self._ask(profile, prompts.analyze_prompt(job), JobAnalysis)

    def resume(self, profile: Profile, job: Job, analysis: JobAnalysis) -> ResumeDoc:
        tailored = self._ask(profile, prompts.resume_prompt(profile, job, analysis), TailoredResume)
        return assemble_resume(profile, tailored, analysis)

    def cover_letter(self, profile: Profile, job: Job, analysis: JobAnalysis) -> CoverLetter:
        return self._ask(profile, prompts.cover_letter_prompt(profile, job, analysis), CoverLetter)

    def learning_plan(self, profile: Profile, job: Job, analysis: JobAnalysis) -> LearningPlan:
        return self._ask(profile, prompts.learning_plan_prompt(profile, job, analysis), LearningPlan)

    def build(
        self,
        profile: Profile,
        job: Job,
        on_part: Callable[[str, BaseModel], None] = lambda name, data: None,
        on_stage: Callable[[str], None] = lambda stage: None,
    ) -> Kit:
        on_stage("Analyzing the job against your profile")
        analysis = self.analyze(profile, job)
        on_part("analysis", analysis)
        on_stage("Writing your resume, cover letter and learning plan")
        steps = {
            "resume": self.resume,
            "cover_letter": self.cover_letter,
            "learning_plan": self.learning_plan,
        }
        results: dict[str, BaseModel] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {name: pool.submit(fn, profile, job, analysis) for name, fn in steps.items()}
            for name, fut in futures.items():
                results[name] = fut.result()
                on_part(name, results[name])
        return Kit(analysis=analysis, **results)

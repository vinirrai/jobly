"""Core data models: job postings and the candidate's master profile."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_job_id(source: str, external_id: str) -> str:
    return hashlib.sha1(f"{source}|{external_id}".encode()).hexdigest()[:16]


class Job(BaseModel):
    """A normalized job posting, whatever board it came from."""

    source: str  # e.g. "greenhouse:stripe" or "email:linkedin"
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    remote: bool = False
    url: str
    apply_url: str | None = None
    description: str = ""  # plain text
    posted_at: datetime | None = None
    salary: str | None = None
    employment_type: str | None = None
    department: str | None = None
    board: str = ""  # human label shown in the UI: "LinkedIn", "Greenhouse", ...

    @property
    def id(self) -> str:
        return make_job_id(self.source, self.external_id)

    @property
    def fingerprint(self) -> str:
        """Identifies the same role posted on several boards."""
        from .textutil import norm_key

        return f"{norm_key(self.company)}|{norm_key(self.title)}"

    @field_validator("posted_at")
    @classmethod
    def _tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v


# --------------------------------------------------------------------------
# Candidate profile (the "master resume" every tailored version is built from)
# --------------------------------------------------------------------------


class Skill(BaseModel):
    name: str
    level: str = "intermediate"  # beginner | intermediate | advanced | expert
    years: float | None = None


class Experience(BaseModel):
    company: str
    title: str
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str
    link: str = ""
    tech: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    school: str
    degree: str = ""
    field: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    gpa: str = ""
    coursework: list[str] = Field(default_factory=list)
    honors: list[str] = Field(default_factory=list)


class Preferences(BaseModel):
    # How skills that are easy-to-moderate to learn show up on a tailored resume:
    #   list     -> listed in Skills like any other skill (learn them before the interview)
    #   learning -> listed under a "Currently learning" line
    #   off      -> left off the resume (still in the learning plan)
    learnable_skills_mode: Literal["list", "learning", "off"] = "list"
    learning_hours_per_week: int = 10
    cover_letter_tone: str = "warm, confident and specific"
    resume_pages: int = 1


class Profile(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    location: str = ""
    links: dict[str, str] = Field(default_factory=dict)
    headline: str = ""
    summary: str = ""
    skills: list[Skill] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    # Short true stories the cover letter can draw on: times you showed grit,
    # learned something fast, worked hard, or solved a problem creatively.
    stories: list[str] = Field(default_factory=list)
    motivations: list[str] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)

    @field_validator("skills", mode="before")
    @classmethod
    def _coerce_skills(cls, v):
        out = []
        for item in v or []:
            out.append({"name": item} if isinstance(item, str) else item)
        return out

    def skill_names(self) -> list[str]:
        names = [s.name for s in self.skills]
        for p in self.projects:
            names.extend(p.tech)
        seen: set[str] = set()
        uniq = []
        for n in names:
            if n.lower() not in seen:
                seen.add(n.lower())
                uniq.append(n)
        return uniq

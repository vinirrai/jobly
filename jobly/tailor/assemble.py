"""Combine the profile with Claude's tailoring into the final resume, enforcing the rules in code.

Claude only chooses *which* profile entries to show and how to word the bullets. Names,
employers, titles, dates, schools and contact details are always copied from the profile, so
a tailored resume can never contain a job or degree you don't have.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import Education, Profile
from .schemas import JobAnalysis, SkillGroup, TailoredResume

LISTABLE_CATEGORIES = {"hard_skill", "tool", "domain_knowledge"}


class ExperienceOut(BaseModel):
    company: str
    title: str
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[str]


class ProjectOut(BaseModel):
    name: str
    link: str = ""
    tech: list[str] = Field(default_factory=list)
    bullets: list[str]


class ResumeDoc(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    location: str = ""
    links: dict[str, str] = Field(default_factory=dict)
    headline: str = ""
    summary: str = ""
    skills: list[SkillGroup] = Field(default_factory=list)
    currently_learning: list[str] = Field(default_factory=list)
    experience: list[ExperienceOut] = Field(default_factory=list)
    projects: list[ProjectOut] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    # Skills added because they're learnable before the interview (shown in the prep checklist)
    added_to_learn: list[str] = Field(default_factory=list)
    tailoring_notes: list[str] = Field(default_factory=list)


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _mentions(groups: list[SkillGroup], name: str) -> bool:
    n = _norm(name)
    return any(n == _norm(item) or n in _norm(item) for g in groups for item in g.items)


def assemble_resume(profile: Profile, tailored: TailoredResume, analysis: JobAnalysis) -> ResumeDoc:
    mode = profile.preferences.learnable_skills_mode
    learnable = [r.name for r in analysis.requirements
                 if r.status == "learnable" and r.category in LISTABLE_CATEGORIES]
    stretch = {_norm(r.name) for r in analysis.requirements if r.status == "stretch"}
    learnable_norm = {_norm(n) for n in learnable}

    # Experience / projects: only real profile entries, facts copied from the profile.
    experience, seen = [], set()
    for t in tailored.experience:
        if 0 <= t.source_index < len(profile.experience) and t.source_index not in seen:
            seen.add(t.source_index)
            src = profile.experience[t.source_index]
            experience.append(ExperienceOut(
                company=src.company, title=src.title, location=src.location, start=src.start, end=src.end,
                bullets=[b for b in t.bullets if b.strip()] or src.bullets,
            ))
    if not experience:
        experience = [ExperienceOut(**e.model_dump()) for e in profile.experience]

    projects, seen = [], set()
    for t in tailored.projects:
        if 0 <= t.source_index < len(profile.projects) and t.source_index not in seen:
            seen.add(t.source_index)
            src = profile.projects[t.source_index]
            projects.append(ProjectOut(name=src.name, link=src.link, tech=src.tech,
                                       bullets=[b for b in t.bullets if b.strip()] or src.bullets))

    # Skills: never claim stretch skills; handle learnable ones per the user's preference.
    groups: list[SkillGroup] = []
    for g in tailored.skills:
        items = []
        for item in g.items:
            n = _norm(item)
            if n in stretch and n not in learnable_norm:
                continue
            if mode != "list" and n in learnable_norm:
                continue
            if item.strip() and n not in {_norm(i) for i in items}:
                items.append(item.strip())
        if items:
            groups.append(SkillGroup(category=g.category, items=items))
    added: list[str] = []
    currently_learning: list[str] = []
    if mode == "list":
        missing = [n for n in learnable if not _mentions(groups, n)]
        if missing:
            groups.append(SkillGroup(category="Additional", items=missing))
        added = list(learnable)
    elif mode == "learning":
        currently_learning = list(learnable)

    # Education is always copied; coursework limited to what's actually in the profile.
    wanted = {_norm(c) for c in tailored.relevant_coursework}
    education = []
    for edu in profile.education:
        e = edu.model_copy(deep=True)
        if wanted:
            picked = [c for c in edu.coursework if _norm(c) in wanted]
            e.coursework = picked or edu.coursework[:6]
        education.append(e)

    return ResumeDoc(
        name=profile.name, email=profile.email, phone=profile.phone, location=profile.location,
        links=profile.links, headline=tailored.headline.strip() or profile.headline,
        summary=tailored.summary.strip() or profile.summary, skills=groups,
        currently_learning=currently_learning, experience=experience, projects=projects,
        education=education, certifications=profile.certifications, awards=profile.awards,
        added_to_learn=added, tailoring_notes=tailored.tailoring_notes,
    )

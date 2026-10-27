"""Structured outputs Claude returns for each step of building an application kit."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ analysis

Status = Literal["have", "learnable", "stretch"]


class Requirement(BaseModel):
    name: str = Field(description="Short skill/requirement name as a recruiter would write it, e.g. 'SQL', 'A/B testing'.")
    category: Literal["hard_skill", "tool", "domain_knowledge", "soft_skill", "credential", "experience"]
    priority: Literal["required", "preferred"]
    status: Status = Field(
        description=(
            "have = clearly evidenced in the profile; "
            "learnable = easy-to-moderate: the candidate can get interview-ready in about 40 focused hours or less "
            "given their background; "
            "stretch = hard: needs weeks/months, deep experience, or a credential."
        )
    )
    evidence: str = Field(description="For 'have': where the profile shows it. Otherwise: the closest related experience.")
    transferable_from: list[str] = Field(description="Profile skills/experiences that transfer to this requirement.")
    hours_to_interview_ready: int = Field(description="Realistic focused hours to speak confidently about it in an interview; 0 if 'have'.")


class JobAnalysis(BaseModel):
    role_summary: str = Field(description="2-3 sentences: what this person would actually do day to day.")
    company_snapshot: str = Field(description="What the company does and what it seems to care about, based only on the posting.")
    values_signals: list[str] = Field(description="Culture/values signals in the posting (e.g. 'ownership', 'customer obsession').")
    fit_score: int = Field(description="0-100 overall fit after considering learnable gaps.")
    fit_summary: str = Field(description="One honest paragraph on fit: strengths, gaps, and how to position.")
    requirements: list[Requirement]
    ats_keywords: list[str] = Field(description="Exact phrases an applicant-tracking system will scan for.")
    concerns: list[str] = Field(description="Possible dealbreakers: years of experience, clearance, visa, degree, relocation. Empty if none.")
    interview_topics: list[str] = Field(description="Topics likely to come up in interviews for this role.")


# -------------------------------------------------------------------- resume

class SkillGroup(BaseModel):
    category: str
    items: list[str]


class TailoredExperience(BaseModel):
    source_index: int = Field(description="Index of the entry in the profile's experience list (0-based).")
    bullets: list[str] = Field(description="Rewritten bullets for this job, most relevant first.")


class TailoredProject(BaseModel):
    source_index: int = Field(description="Index of the entry in the profile's projects list (0-based).")
    bullets: list[str]


class TailoredResume(BaseModel):
    headline: str = Field(description="One-line professional headline aimed at this role.")
    summary: str = Field(description="2-3 sentence summary aimed at this role; no first-person pronouns.")
    skills: list[SkillGroup]
    experience: list[TailoredExperience] = Field(description="Profile experience entries to include, in display order.")
    projects: list[TailoredProject] = Field(description="Profile projects to include, in display order.")
    relevant_coursework: list[str] = Field(description="Coursework from the profile worth showing for this role (may be empty).")
    tailoring_notes: list[str] = Field(description="Short notes for the candidate on what was changed and why.")


# --------------------------------------------------------------- cover letter

class CoverLetter(BaseModel):
    greeting: str = Field(description="e.g. 'Dear Hiring Team at Acme,' — use a name only if the posting gives one.")
    paragraphs: list[str] = Field(description="3-4 paragraphs, 250-380 words total.")
    closing: str = Field(description="e.g. 'With enthusiasm,'")
    stretch_framing: list[str] = Field(description="For the candidate: how each hard requirement was addressed in the letter.")


# -------------------------------------------------------------- learning plan

class Resource(BaseModel):
    title: str
    kind: Literal["docs", "course", "video", "book", "practice", "article", "project"]
    link: str = Field(description="Official URL you are certain of, otherwise 'search: <query>'.")


class SprintItem(BaseModel):
    skill: str
    why_it_matters: str = Field(description="How the role uses it.")
    hours: int
    steps: list[str] = Field(description="Concrete ordered steps.")
    resources: list[Resource]
    mini_project: str = Field(description="A small thing to build/do that proves the skill and gives an interview story.")
    talking_points: list[str] = Field(description="What to say in the interview about this skill, honestly.")
    ready_when: str = Field(description="A self-check that tells the candidate they are interview-ready.")


class StretchItem(BaseModel):
    skill: str
    transfer_bridge: str = Field(description="How the candidate's existing skills map onto this one.")
    weeks: int
    milestones: list[str] = Field(description="Weekly milestones.")
    resources: list[Resource]
    portfolio_project: str
    how_to_frame_it_now: str = Field(description="What to say about this gap today, emphasizing transferability, adaptability and grit.")


class DayPlan(BaseModel):
    label: str = Field(description="e.g. 'Day 1' or 'Week 2'.")
    tasks: list[str]


class LearningPlan(BaseModel):
    overview: str
    interview_sprint: list[SprintItem] = Field(description="One item per 'learnable' requirement.")
    sprint_schedule: list[DayPlan] = Field(description="Day-by-day plan to be interview-ready on the learnable skills.")
    long_term: list[StretchItem] = Field(description="One item per 'stretch' requirement.")
    interview_framing: list[str] = Field(description="Talking points that show adaptability, grit and fast learning, drawn from real profile stories.")

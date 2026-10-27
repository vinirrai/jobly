"""Prompts for each step. The profile sits in a cached system block so it's reused across jobs."""

from __future__ import annotations

import json

import yaml

from ..models import Job, Profile
from .schemas import JobAnalysis

SYSTEM = """\
You are an expert career coach, technical recruiter and resume writer helping one candidate \
apply to jobs. You know how applicant-tracking systems parse resumes and what hiring managers \
look for.

Ground rules that apply to everything you write:
- Every fact about the candidate comes from their profile. Never invent employers, titles, \
dates, degrees, metrics, tools used on the job, or responsibilities. Rephrasing real work in \
the posting's vocabulary is good; claiming work that didn't happen is not.
- Facts about the company come only from the job posting.
- Write plainly and specifically. Concrete details and numbers from the profile beat adjectives.
"""


def profile_block(profile: Profile) -> str:
    data = profile.model_dump(mode="json", exclude={"preferences"})
    data["experience"] = {f"experience[{i}]": e for i, e in enumerate(data["experience"])}
    data["projects"] = {f"projects[{i}]": p for i, p in enumerate(data["projects"])}
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110)
    return f"<candidate_profile>\n{body}</candidate_profile>"


def system_blocks(profile: Profile) -> list[dict]:
    return [
        {"type": "text", "text": SYSTEM},
        {"type": "text", "text": profile_block(profile), "cache_control": {"type": "ephemeral"}},
    ]


def job_block(job: Job) -> str:
    meta = [
        f"Title: {job.title}",
        f"Company: {job.company or 'unknown'}",
        f"Location: {job.location or 'unknown'}{' (remote)' if job.remote else ''}",
        f"Found on: {job.board}",
    ]
    if job.salary:
        meta.append(f"Salary: {job.salary}")
    if job.employment_type:
        meta.append(f"Type: {job.employment_type}")
    return "<job_posting>\n" + "\n".join(meta) + f"\n\n{job.description.strip()}\n</job_posting>"


def analysis_block(analysis: JobAnalysis) -> str:
    return "<fit_analysis>\n" + json.dumps(analysis.model_dump(), indent=1) + "\n</fit_analysis>"


def _names(analysis: JobAnalysis, status: str) -> list[str]:
    return [r.name for r in analysis.requirements if r.status == status]


def _fmt(names: list[str]) -> str:
    return ", ".join(names) if names else "(none)"


ANALYZE = """\
Analyze this job posting against the candidate's profile.

List the meaningful requirements, required and preferred (skip boilerplate unless the posting \
stresses it). Classify each one honestly for this specific candidate:
- have: clearly evidenced in the profile.
- learnable: easy to moderate — with their background they could speak to it confidently in an \
interview after roughly 40 focused hours or less. Judge relative to the candidate: Tableau is \
learnable for someone fluent in Excel and SQL; running Kubernetes in production is a stretch for \
someone who has never deployed a service.
- stretch: hard — needs weeks or months, deep hands-on experience, or a credential.

Then score overall fit from 0 to 100, treating learnable gaps as mostly closable."""


def analyze_prompt(job: Job) -> str:
    return f"{job_block(job)}\n\n{ANALYZE}"


_MODE = {
    "list": (
        "List these learnable skills in the Skills section, worded the way the posting words them: {names}. "
        "The candidate will learn them before interviewing (a learning plan is created alongside this resume). "
        "They belong only in Skills: no experience or project bullet may claim they were used."
    ),
    "learning": (
        "Leave these learnable skills out of the skill groups — the resume shows them separately under "
        "'Currently learning': {names}."
    ),
    "off": "Leave these learnable skills off the resume: {names}.",
}


def resume_prompt(profile: Profile, job: Job, analysis: JobAnalysis) -> str:
    prefs = profile.preferences
    learnable = _names(analysis, "learnable")
    stretch = _names(analysis, "stretch")
    mode = _MODE[prefs.learnable_skills_mode].format(names=_fmt(learnable))
    return f"""{job_block(job)}

{analysis_block(analysis)}

Write a tailored version of the candidate's resume for this job.

- Pick experience and project entries by their index in the profile and order them for relevance \
(keep the most recent role). Rewrite their bullets to mirror the posting's language and lead with \
the most relevant impact, but keep every claim true to the profile. Merge, split or drop bullets \
as needed.
- Skills: lead with the skills the candidate has that the posting asks for, using the posting's \
exact wording where it's accurate, so applicant-tracking systems match them.
- {mode}
- Never list these stretch requirements as skills: {_fmt(stretch)}. Instead, make sure the \
adjacent experience that transfers to them is visible.
- It must fit on {prefs.resume_pages} page(s) — about 3-5 bullets for recent roles, fewer for older ones.
- Headline and summary: aim them at this role and show drive; no first-person pronouns."""


def cover_letter_prompt(profile: Profile, job: Job, analysis: JobAnalysis) -> str:
    stretch = _names(analysis, "stretch")
    learnable = _names(analysis, "learnable")
    return f"""{job_block(job)}

{analysis_block(analysis)}

Write the cover letter for this application.

The reader should come away feeling three things about the candidate: genuine passion for this \
company and this work, a strong work ethic, and creativity. Show each through specific stories \
and details from the profile rather than by naming the trait. Connect the passion to something \
specific about what this company does, taken from the posting.

Hard requirements the candidate doesn't have yet: {_fmt(stretch)}. Don't hide these gaps and \
don't claim the skills. Bridge them: name the transferable experience, and show adaptability and \
grit with a real example from the profile of learning something hard quickly or pushing through \
difficulty.
Skills they're ramping up on now: {_fmt(learnable)}. You may say the candidate is actively \
learning these, but not that they've used them.

Tone: {profile.preferences.cover_letter_tone}. 250-380 words in 3-4 paragraphs. Don't restate the \
resume line by line, avoid stock phrases ("I am writing to apply", "I believe I would be a great \
fit"), and leave no placeholders."""


def learning_plan_prompt(profile: Profile, job: Job, analysis: JobAnalysis) -> str:
    learnable = _names(analysis, "learnable")
    stretch = _names(analysis, "stretch")
    hours = profile.preferences.learning_hours_per_week
    return f"""{job_block(job)}

{analysis_block(analysis)}

Build a learning plan for this candidate and this job.

- interview_sprint: one item per learnable requirement ({_fmt(learnable)}), required ones first. \
Interviews can come within days, so the sprint_schedule should get them interview-ready in about \
7-10 days at roughly {max(hours, 10)} hours per week, front-loading what the role uses most.
- long_term: one item per stretch requirement ({_fmt(stretch)}), with weekly milestones at about \
{hours} hours per week, a portfolio project, and how to talk about the gap today in a way that \
shows transferable skills, adaptability and grit.
- Prefer free, reputable resources: official documentation and tutorials and well-known free \
courses. Give a URL only when you're certain it exists; otherwise write "search: <query>".
- interview_framing: 3-5 talking points built on the candidate's real stories that show they \
learn fast and push through hard things.
If there are no learnable or no stretch requirements, leave that list empty."""


IMPORT_RESUME = """\
Convert the resume above into the candidate profile structure.
Copy facts exactly — don't embellish, summarize away numbers, or invent anything. Leave fields \
empty when the resume doesn't say. Put each skill in skills (level 'intermediate' unless the \
resume makes it clear). Put notable anecdotes of persistence, fast learning or creative problem \
solving into stories only if the resume actually describes them."""

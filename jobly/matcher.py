"""Fast, free relevance filter. Runs on every scraped job before any AI is used."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from .config import SearchConfig
from .models import Job, Profile, utcnow
from .textutil import contains_term, looks_remote


@dataclass
class MatchResult:
    passed: bool
    score: int
    reasons: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)


def _phrase(term: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(term.strip())}\b", re.IGNORECASE)


def evaluate(job: Job, search: SearchConfig, profile: Profile | None = None) -> MatchResult:
    title = job.title or ""
    text = f"{title}\n{job.description}"
    reasons: list[str] = []

    for bad in search.titles_exclude:
        if bad.strip() and _phrase(bad).search(title):
            return MatchResult(False, 0, [f"title contains '{bad}'"])

    title_hit = next((t for t in search.titles_include if t.strip() and _phrase(t).search(title)), None)
    if search.titles_include and not title_hit:
        # Some boards (e.g. HN threads) have messy titles; give the first lines a chance.
        head = (job.description or "")[:300]
        title_hit = next((t for t in search.titles_include if t.strip() and _phrase(t).search(head)), None)
        if not title_hit:
            return MatchResult(False, 0, ["title doesn't match your target roles"])

    if search.max_age_days and job.posted_at:
        if job.posted_at < utcnow() - timedelta(days=search.max_age_days):
            return MatchResult(False, 0, [f"posted more than {search.max_age_days:g} days ago"])

    remote = job.remote or looks_remote(job.location)
    location_hit = None
    if search.locations:
        loc = job.location or ""
        location_hit = next((l for l in search.locations if l.strip() and _phrase(l).search(loc)), None)
        if not location_hit and remote and search.remote_ok:
            location_hit = "remote"
        if not location_hit and loc.strip():
            return MatchResult(False, 0, [f"location '{loc}' isn't in your list"])

    score = 0
    if title_hit:
        score += 35
        reasons.append(f"title matches '{title_hit}'")
    elif not search.titles_include:
        score += 20

    skills = profile.skill_names() if profile else []
    matched = [s for s in skills if contains_term(text, s)]
    if skills:
        score += round(45 * min(1.0, len(matched) / 6))
        if matched:
            reasons.append(f"mentions your skills: {', '.join(matched[:8])}")
    kw = [k for k in search.keywords if contains_term(text, k)]
    if kw:
        score += min(10, 4 * len(kw))
        reasons.append(f"keywords: {', '.join(kw[:6])}")
    if location_hit or (remote and search.remote_ok):
        score += 10
        reasons.append("remote" if (location_hit == "remote" or (remote and not location_hit)) else f"in {location_hit}")
    if not job.description:
        reasons.append("description not loaded yet")

    score = max(0, min(100, score))
    passed = score >= search.min_score
    if not passed:
        reasons.append(f"score {score} below your minimum {search.min_score}")
    return MatchResult(passed, score, reasons, matched)

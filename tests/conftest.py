from __future__ import annotations

import pytest
import yaml

from jobly.config import Paths, example_file
from jobly.models import Job, Profile
from jobly.tailor.schemas import (
    CoverLetter,
    DayPlan,
    JobAnalysis,
    LearningPlan,
    Requirement,
    Resource,
    SkillGroup,
    SprintItem,
    StretchItem,
    TailoredExperience,
    TailoredProject,
    TailoredResume,
)

LONG_DESC = (
    "We are looking for a Data Analyst Intern to join our growth team. You will build dashboards in Tableau, "
    "write SQL against our Snowflake warehouse, and partner with product managers. Requirements: Python, SQL, "
    "Tableau, experience with A/B testing, and familiarity with Kubernetes is a plus. "
) * 2


@pytest.fixture
def profile() -> Profile:
    return Profile.model_validate(yaml.safe_load(example_file("profile.example.yaml")))


@pytest.fixture
def job() -> Job:
    return Job(
        source="greenhouse:acme",
        external_id="123",
        title="Data Analyst Intern",
        company="Acme",
        location="New York, NY",
        url="https://job-boards.greenhouse.io/acme/jobs/123",
        description=LONG_DESC,
        board="Greenhouse",
    )


def make_analysis() -> JobAnalysis:
    return JobAnalysis(
        role_summary="Build dashboards and analyze experiments.",
        company_snapshot="Acme makes widgets.",
        values_signals=["ownership"],
        fit_score=78,
        fit_summary="Strong Python/SQL; Tableau is quick to learn; Kubernetes is a stretch.",
        requirements=[
            Requirement(name="Python", category="hard_skill", priority="required", status="have",
                        evidence="ETL pipeline at Brightline", transferable_from=[], hours_to_interview_ready=0),
            Requirement(name="SQL", category="hard_skill", priority="required", status="have",
                        evidence="PostgreSQL pipeline", transferable_from=[], hours_to_interview_ready=0),
            Requirement(name="Tableau", category="tool", priority="required", status="learnable",
                        evidence="React dashboards", transferable_from=["React", "Excel"], hours_to_interview_ready=12),
            Requirement(name="A/B testing", category="domain_knowledge", priority="required", status="learnable",
                        evidence="Statistics coursework", transferable_from=["Statistics"], hours_to_interview_ready=15),
            Requirement(name="AWS Certified Cloud Practitioner", category="credential", priority="preferred",
                        status="learnable", evidence="", transferable_from=[], hours_to_interview_ready=30),
            Requirement(name="Kubernetes", category="tool", priority="preferred", status="stretch",
                        evidence="No deployment experience", transferable_from=["Python"], hours_to_interview_ready=80),
        ],
        ats_keywords=["SQL", "Tableau", "A/B testing"],
        concerns=[],
        interview_topics=["SQL joins", "experiment design"],
    )


def make_tailored() -> TailoredResume:
    return TailoredResume(
        headline="Data-minded CS student",
        summary="Builds data pipelines and dashboards.",
        skills=[
            SkillGroup(category="Languages", items=["Python", "SQL", "Kubernetes"]),
            SkillGroup(category="Analytics", items=["pandas", "Tableau"]),
        ],
        experience=[
            TailoredExperience(source_index=0, bullets=["Built Python ETL into PostgreSQL, cutting reporting time 95%"]),
            TailoredExperience(source_index=99, bullets=["Invented job"]),
            TailoredExperience(source_index=0, bullets=["duplicate"]),
        ],
        projects=[TailoredProject(source_index=0, bullets=[])],
        relevant_coursework=["Statistics", "Databases", "Quantum Basket Weaving"],
        tailoring_notes=["Led with data work"],
    )


def make_letter() -> CoverLetter:
    return CoverLetter(
        greeting="Dear Hiring Team at Acme,",
        paragraphs=["I love Acme's widgets.", "At Brightline I cut reporting from 6 hours to 20 minutes.",
                    "I taught myself React in three weeks."],
        closing="With enthusiasm,",
        stretch_framing=["Kubernetes: bridged via Python deployment curiosity"],
    )


def make_plan() -> LearningPlan:
    res = Resource(title="Tableau free training", kind="course", link="search: tableau free training videos")
    return LearningPlan(
        overview="Get interview-ready on Tableau and A/B testing in a week.",
        interview_sprint=[SprintItem(skill="Tableau", why_it_matters="Dashboards", hours=12, steps=["Install"],
                                     resources=[res], mini_project="Rebuild a dashboard",
                                     talking_points=["Rebuilt my React dashboard in Tableau"], ready_when="Can build a dashboard in 30 min")],
        sprint_schedule=[DayPlan(label="Day 1", tasks=["Tableau basics"])],
        long_term=[StretchItem(skill="Kubernetes", transfer_bridge="Python services", weeks=6, milestones=["Pods"],
                               resources=[Resource(title="Kubernetes docs", kind="docs", link="https://kubernetes.io/docs/home/")],
                               portfolio_project="Deploy StudySpot", how_to_frame_it_now="I learn fast")],
        interview_framing=["Took over a project mid-summer"],
    )


class FakeLLM:
    model = "fake-model"

    def __init__(self):
        self.calls: list[tuple[str, list, list]] = []
        self.responses = {
            "JobAnalysis": make_analysis(),
            "TailoredResume": make_tailored(),
            "CoverLetter": make_letter(),
            "LearningPlan": make_plan(),
        }

    def structured(self, *, system, messages, schema):
        self.calls.append((schema.__name__, system, messages))
        return self.responses[schema.__name__]


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def home(tmp_path) -> Paths:
    paths = Paths(tmp_path / "home")
    paths.root.mkdir()
    paths.config.write_text(yaml.safe_dump({
        "scout": {"enabled": False},
        "search": {"titles_include": ["intern", "analyst"], "titles_exclude": ["senior"]},
        "sources": [],
    }))
    paths.profile.write_text(example_file("profile.example.yaml"))
    return paths

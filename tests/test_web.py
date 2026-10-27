from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from jobly.models import Job
from jobly.service import Jobly
from jobly.web.app import create_app

from .conftest import LONG_DESC


@pytest.fixture
def jobly(home, fake_llm):
    j = Jobly(home, llm=fake_llm)
    j.scout.run_once = lambda *a, **k: None  # no network in tests
    yield j
    j.close()


@pytest.fixture
def client(jobly):
    with TestClient(create_app(jobly, start_scout=False)) as c:
        yield c


def _add(client) -> str:
    r = client.post("/add/manual", data={"title": "Data Analyst Intern", "company": "Acme", "location": "Remote",
                                         "description": LONG_DESC, "url": ""}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].split("/jobs/")[1].split("?")[0]


def _wait_for_kit(jobly, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        kit = jobly.store.get_kit(job_id)
        if kit and kit["status"] in ("done", "error"):
            return kit
        time.sleep(0.05)
    raise AssertionError("kit never finished")


def test_dashboard_and_onboarding(client, jobly):
    r = client.get("/")
    assert r.status_code == 200 and "No sources yet" in r.text and "No jobs yet" in r.text
    jobly.paths.profile.unlink()
    assert "Set up profile" in client.get("/").text


def test_full_kit_flow(client, jobly):
    job_id = _add(client)
    page = client.get(f"/jobs/{job_id}")
    assert "Generate application kit" in page.text and "Data Analyst Intern" in page.text

    r = client.post(f"/jobs/{job_id}/kit", follow_redirects=False)
    assert r.status_code == 303
    kit = _wait_for_kit(jobly, job_id)
    assert kit["status"] == "done", kit["error"]
    assert client.get(f"/api/jobs/{job_id}/kit").json()["parts"] == [
        "analysis", "resume", "cover_letter", "learning_plan"]

    fit = client.get(f"/jobs/{job_id}?tab=fit").text
    assert "easy to learn" in fit and "Kubernetes" in fit and "Learn before your interview" in fit
    assert "Brightline" in client.get(f"/jobs/{job_id}/resume.html").text
    assert "Download .docx" in client.get(f"/jobs/{job_id}?tab=resume").text
    assert "Dear Hiring Team" in client.get(f"/jobs/{job_id}/cover-letter.html").text
    assert "Interview sprint" in client.get(f"/jobs/{job_id}?tab=plan").text
    assert "Tableau" in client.get(f"/jobs/{job_id}?tab=posting").text

    docx = client.get(f"/jobs/{job_id}/resume.docx")
    assert docx.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert 'filename="alex-rivera-resume-acme.docx"' in docx.headers["content-disposition"]
    assert client.get(f"/jobs/{job_id}/cover-letter.docx").status_code == 200
    assert "With enthusiasm" in client.get(f"/jobs/{job_id}/cover-letter.txt").text
    assert "Learning plan" in client.get(f"/jobs/{job_id}/learning-plan.md").text

    files = sorted(p.name for p in next(jobly.paths.kits.iterdir()).iterdir())
    assert files == ["alex-rivera-cover-letter.docx", "alex-rivera-cover-letter.txt", "alex-rivera-resume.docx",
                     "alex-rivera-resume.html", "alex-rivera-resume.md", "learning-plan.md"]

    skills = client.get("/skills").text
    assert "Tableau" in skills and "Kubernetes" in skills


def test_status_and_description(client, jobly):
    job_id = _add(client)
    client.post(f"/jobs/{job_id}/status", data={"status": "saved", "back": "/"})
    assert jobly.store.get_job(job_id).status == "saved"
    client.post(f"/jobs/{job_id}/description", data={"description": "New description " * 30})
    assert jobly.store.get_job(job_id).description.startswith("New description")
    assert client.get("/jobs/nope").status_code == 404
    assert client.get(f"/jobs/{job_id}/resume.docx").status_code == 404  # no kit yet


def test_kit_error_for_login_walled_job(client, jobly):
    job = Job(source="email:handshake", external_id="77", title="Analyst Intern", company="Hooli",
              url="https://app.joinhandshake.com/jobs/77", board="Handshake")
    jobly.store.upsert_job(job, 50)
    client.post(f"/jobs/{job.id}/kit")
    kit = _wait_for_kit(jobly, job.id)
    assert kit["status"] == "error" and "paste" in kit["error"].lower()
    assert "Paste or replace the description" in client.get(f"/jobs/{job.id}").text


def test_sources_page(client, jobly):
    r = client.post("/sources", data={"url": "https://jobs.lever.co/acme", "search": ""}, follow_redirects=False)
    assert r.status_code == 303
    assert jobly.store.custom_sources()[0][1] == {"type": "lever", "company": "acme"}
    assert [s.key for s in jobly.scout.sources] == ["lever:acme"]
    page = client.get("/sources").text
    assert "lever:acme" in page
    client.post("/sources", data={"url": "stripe.com/jobs", "search": ""})
    assert jobly.store.custom_sources()[1][1] == {"type": "careers_page", "url": "https://stripe.com/jobs"}
    source_id = jobly.store.custom_sources()[0][0]
    client.post(f"/sources/{source_id}/delete")
    assert len(jobly.scout.sources) == 1


def test_profile_page(client, jobly):
    assert "Alex Rivera" in client.get("/profile").text
    r = client.post("/profile", data={"text": "name: [unclosed"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = client.post("/profile", data={"text": "name: Sam Lee\nskills: [Python, SQL]\n"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    assert jobly.load_profile().skills[1].name == "SQL"


def test_new_count_api(client):
    assert client.get("/api/new-count?since=2000-01-01T00:00:00Z").json() == {"count": 0}
    _add(client)
    assert client.get("/api/new-count?since=2000-01-01T00:00:00Z").json() == {"count": 1}


def test_unsafe_links_are_not_rendered(client, jobly):
    job = Job(source="manual", external_id="x1", title="Analyst Intern", company="Evil",
              url="javascript:alert(1)", apply_url="javascript:alert(2)", description="<script>alert(3)</script>",
              board="Manual")
    jobly.store.upsert_job(job, 10)
    page = client.get(f"/jobs/{job.id}").text
    assert "javascript:" not in page and "<script>alert(3)" not in page

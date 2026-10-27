from __future__ import annotations

import json
from email.message import EmailMessage

import pytest
import respx

from jobly.http import Http
from jobly.importer import (
    NeedsManualPaste,
    detect_board,
    detect_board_in_html,
    fetch_job_from_url,
    jobs_from_jsonld,
    jobs_from_links,
)
from jobly.sources.email_alerts import _unwrap, parse_alert_email

http = Http(retries=0)


def _email(subject: str, html: str, sender: str = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = "Mon, 26 Oct 2026 14:05:00 +0000"
    msg.set_content("plain version")
    msg.add_alternative(html, subtype="html")
    return msg


LINKEDIN_ALERT = """
<table>
 <tr><td>
   <a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc&refId=x"><img alt="logo"></a>
   <table><tr><td>
     <a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc">Data Analyst Intern</a>
     <p>Acme Corp · New York, NY (Hybrid)</p>
     <p>Actively recruiting</p>
   </td></tr></table>
 </td></tr>
 <tr><td>
   <table><tr><td>
     <a href="https://www.linkedin.com/comm/jobs/view/4099999999/?trackingId=def">Junior Software Engineer</a>
     <p>Globex</p><p>Remote</p>
   </td></tr></table>
 </td></tr>
 <tr><td><a href="https://www.linkedin.com/comm/jobs/search?keywords=analyst">See all jobs</a></td></tr>
</table>
"""


def test_linkedin_alert_email():
    jobs = parse_alert_email(_email("“data analyst”: Acme Corp and more", LINKEDIN_ALERT))
    assert len(jobs) == 2
    a, b = jobs
    assert a.title == "Data Analyst Intern" and a.company == "Acme Corp" and a.location == "New York, NY (Hybrid)"
    assert a.url == "https://www.linkedin.com/jobs/view/4012345678/" and a.board == "LinkedIn"
    assert a.source == "email:linkedin" and a.external_id == "4012345678"
    assert b.company == "Globex" and b.location == "Remote" and b.remote
    assert a.posted_at.hour == 14


def test_indeed_and_handshake_and_redirects():
    html = """
    <div><a href="https://cts.indeed.com/v3/click?url=https%3A%2F%2Fwww.indeed.com%2Frc%2Fclk%2Fdl%3Fjk%3D9f8e7d6c5b4a3f21%26from%3Dja">
      Junior Data Analyst</a><div>Initech - Austin, TX</div><div>$25 an hour</div></div>
    <div><a href="https://app.joinhandshake.com/stu/jobs/8765432?ref=email">Marketing Analyst Intern</a>
      <div>Hooli</div><div>San Francisco, CA</div></div>
    <a href="https://www.indeed.com/unsubscribe">Unsubscribe</a>
    """
    jobs = parse_alert_email(_email("New jobs for you", html, sender="Indeed <alert@indeed.com>"))
    by_board = {j.board: j for j in jobs}
    indeed = by_board["Indeed"]
    assert indeed.url == "https://www.indeed.com/viewjob?jk=9f8e7d6c5b4a3f21"
    assert indeed.company == "Initech" and indeed.location == "Austin, TX"
    hs = by_board["Handshake"]
    assert hs.url == "https://app.joinhandshake.com/jobs/8765432" and hs.company == "Hooli"


def test_single_job_alert_uses_subject():
    html = '<a href="https://www.linkedin.com/comm/jobs/view/4011111111/"><img src="x"></a>'
    jobs = parse_alert_email(_email("Business Analyst at Umbrella Co: Apply now", html))
    assert jobs[0].title == "Business Analyst" and jobs[0].company == "Umbrella Co"


def test_unwrap_nested_redirects():
    url = "https://t.co/r?u=https%3A%2F%2Fclick.x.com%2F%3Furl%3Dhttps%253A%252F%252Fwww.linkedin.com%252Fjobs%252Fview%252F4000000001%252F"
    assert _unwrap(url) == "https://www.linkedin.com/jobs/view/4000000001/"


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/stripe", {"type": "greenhouse", "company": "stripe"}),
    ("https://job-boards.greenhouse.io/figma/jobs/123", {"type": "greenhouse", "company": "figma"}),
    ("https://boards.greenhouse.io/embed/job_board?for=acme&b=x", {"type": "greenhouse", "company": "acme"}),
    ("https://jobs.lever.co/palantir", {"type": "lever", "company": "palantir"}),
    ("https://jobs.eu.lever.co/acme/abc", {"type": "lever", "company": "acme", "region": "eu"}),
    ("https://jobs.ashbyhq.com/ramp", {"type": "ashby", "company": "ramp"}),
    ("https://jobs.smartrecruiters.com/Visa", {"type": "smartrecruiters", "company": "Visa"}),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite?q=x",
     {"type": "workday", "url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"}),
    ("https://example.com/careers", None),
])
def test_detect_board(url, expected):
    assert detect_board(url) == expected


def test_detect_board_in_html():
    html = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'
    assert detect_board_in_html(html) == {"type": "greenhouse", "company": "acme"}
    assert detect_board_in_html('<a href="https://jobs.lever.co/initech">Jobs</a>') == {"type": "lever", "company": "initech"}
    assert detect_board_in_html("<p>no ats</p>") is None


JSONLD_PAGE = """<html><head><script type="application/ld+json">
""" + json.dumps({"@context": "https://schema.org", "@graph": [{
    "@type": "JobPosting", "title": "Product Analyst", "datePosted": "2026-10-20",
    "description": "<p>" + "Analyze product usage with SQL and Python. " * 10 + "</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Startup Inc"},
    "jobLocation": {"@type": "Place", "address": {"addressLocality": "Brooklyn", "addressRegion": "NY",
                                                  "addressCountry": {"name": "US"}}},
    "baseSalary": {"currency": "USD", "value": {"minValue": 80000, "maxValue": 95000, "unitText": "YEAR"}},
    "employmentType": ["FULL_TIME"], "identifier": {"value": "PA-7"},
}]}) + "</script></head><body></body></html>"


def test_jobs_from_jsonld():
    jobs = jobs_from_jsonld(JSONLD_PAGE, "https://startup.com/careers/pa", source="careers_page:x")
    j = jobs[0]
    assert j.title == "Product Analyst" and j.company == "Startup Inc" and j.location == "Brooklyn, NY, US"
    assert j.salary == "USD 80,000–95,000 YEAR" and j.employment_type == "FULL_TIME" and j.external_id == "PA-7"


def test_jobs_from_links():
    html = """<a href="/careers">Careers</a><a href="/careers/data-analyst-intern">Data Analyst Intern</a>
              <a href="/blog/post">Our engineer blog</a><a href="https://x.com/jobs/123">Backend Engineer</a>"""
    jobs = jobs_from_links(html, "https://x.com/careers", "careers_page:x", "X")
    assert [j.title for j in jobs] == ["Data Analyst Intern", "Backend Engineer"]


@respx.mock
def test_fetch_job_from_url_greenhouse_and_lever():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/55").respond(json={
        "id": 55, "title": "Analyst", "location": {"name": "NYC"}, "content": "&lt;p&gt;Hi&lt;/p&gt;",
        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/55"})
    assert fetch_job_from_url("https://job-boards.greenhouse.io/acme/jobs/55", http).title == "Analyst"
    pid = "0f3c9c1e-1111-2222-3333-444455556666"
    respx.get(f"https://api.lever.co/v0/postings/acme/{pid}?mode=json").respond(json={
        "id": pid, "text": "Intern", "categories": {}, "descriptionPlain": "x"})
    assert fetch_job_from_url(f"https://jobs.lever.co/acme/{pid}/apply", http).title == "Intern"


@respx.mock
def test_fetch_linkedin_public_page_fallback():
    page = """<html><body><h1 class="top-card-layout__title">Data Analyst</h1>
      <a class="topcard__org-name-link">Acme</a><span class="topcard__flavor--bullet">Remote</span>
      <div class="show-more-less-html__markup">""" + "<p>Use SQL and Python daily.</p>" * 20 + "</div></body></html>"
    route = respx.get("https://www.linkedin.com/jobs/view/4012345678/").respond(text=page)
    job = fetch_job_from_url("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4012345678", http)
    assert route.called
    assert job.title == "Data Analyst" and job.company == "Acme" and job.board == "LinkedIn" and job.remote
    assert "SQL" in job.description


@respx.mock
def test_fetch_job_needs_paste():
    with pytest.raises(NeedsManualPaste):
        fetch_job_from_url("https://app.joinhandshake.com/stu/jobs/123", http)
    respx.get("https://blocked.example/job/1").respond(status_code=403)
    with pytest.raises(NeedsManualPaste):
        fetch_job_from_url("https://blocked.example/job/1", http)
    respx.get("https://thin.example/job/2").respond(text="<html><body>Sign in to continue</body></html>")
    with pytest.raises(NeedsManualPaste):
        fetch_job_from_url("https://thin.example/job/2", http)

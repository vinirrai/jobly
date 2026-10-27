from __future__ import annotations

import httpx
import respx

from jobly.http import Http
from jobly.registry import build_source, build_sources
from jobly.sources.ats import (
    AshbySource,
    GreenhouseSource,
    LeverSource,
    SmartRecruitersSource,
    WorkdaySource,
    parse_workday_url,
)
from jobly.sources.boards import (
    AdzunaSource,
    HackerNewsHiringSource,
    JSearchSource,
    RemoteOKSource,
    RemotiveSource,
    RSSSource,
)

http = Http(retries=0)


@respx.mock
def test_greenhouse_fetch_unescapes_html():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").respond(json={"jobs": [{
        "id": 42, "title": " Data Analyst Intern ", "updated_at": "2026-10-20T10:00:00-04:00",
        "first_published": "2026-10-19T09:00:00-04:00",
        "location": {"name": "Remote - US"}, "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/42",
        "content": "&lt;p&gt;Do &lt;strong&gt;SQL&lt;/strong&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;",
        "departments": [{"name": "Data"}], "company_name": "Acme Inc",
    }]})
    jobs = GreenhouseSource({"company": "acme"}, http).fetch()
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "Data Analyst Intern" and j.company == "Acme Inc" and j.remote
    assert "SQL" in j.description and "• Python" in j.description and "<" not in j.description
    assert j.posted_at.year == 2026 and j.department == "Data"


@respx.mock
def test_lever_fetch_combines_lists():
    respx.get("https://api.lever.co/v0/postings/acme?mode=json").respond(json=[{
        "id": "abc-123", "text": "Software Engineer Intern", "categories": {"location": "Boston, MA", "team": "Eng",
                                                                            "commitment": "Internship"},
        "descriptionPlain": "Build things.", "lists": [{"text": "Requirements", "content": "<li>Python</li><li>Go</li>"}],
        "additionalPlain": "We sponsor visas.", "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "applyUrl": "https://jobs.lever.co/acme/abc-123/apply", "createdAt": 1761000000000, "workplaceType": "hybrid",
    }])
    j = LeverSource({"company": "acme"}, http).fetch()[0]
    assert j.company == "Acme"  # derived from slug
    assert "Requirements" in j.description and "• Go" in j.description and "sponsor" in j.description
    assert j.employment_type == "Internship" and not j.remote
    assert j.posted_at is not None


@respx.mock
def test_ashby_skips_unlisted():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true").respond(json={"jobs": [
        {"id": "1", "title": "Analyst", "location": "NYC", "isRemote": False, "descriptionPlain": "x",
         "jobUrl": "https://jobs.ashbyhq.com/acme/1", "publishedAt": "2026-10-01T00:00:00Z", "isListed": True,
         "compensation": {"compensationTierSummary": "$30/hr"}, "secondaryLocations": [{"location": "Remote"}]},
        {"id": "2", "title": "Hidden", "isListed": False},
    ]})
    jobs = AshbySource({"company": "acme", "label": "Acme Labs"}, http).fetch()
    assert [j.title for j in jobs] == ["Analyst"]
    assert jobs[0].company == "Acme Labs" and jobs[0].salary == "$30/hr" and jobs[0].remote


@respx.mock
def test_smartrecruiters_fetches_details_only_for_unknown():
    respx.get("https://api.smartrecruiters.com/v1/companies/Visa/postings?limit=100").respond(json={"content": [
        {"id": "111", "name": "Data Intern", "releasedDate": "2026-10-10T00:00:00.000Z",
         "location": {"city": "Austin", "region": "TX", "country": "us", "remote": False},
         "company": {"name": "Visa"}},
        {"id": "222", "name": "Old Intern", "location": {"fullLocation": "Remote"}, "company": {"name": "Visa"}},
    ]})
    detail = respx.get("https://api.smartrecruiters.com/v1/companies/Visa/postings/111").respond(json={
        "jobAd": {"sections": {"jobDescription": {"title": "Job", "text": "<p>Analyze data</p>"}}}})
    other = respx.get("https://api.smartrecruiters.com/v1/companies/Visa/postings/222").respond(json={})
    src = SmartRecruitersSource({"company": "Visa"}, http)
    from jobly.models import make_job_id
    src.known_checker = lambda job_id: job_id == make_job_id("smartrecruiters:Visa", "222")
    jobs = src.fetch()
    assert detail.called and not other.called
    assert "Analyze data" in jobs[0].description and jobs[0].location == "Austin, TX, us"
    assert jobs[1].remote


def test_workday_url_parsing():
    assert parse_workday_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/details/x") == (
        "nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite")
    assert parse_workday_url("https://acme.wd1.myworkdayjobs.com/Careers") == ("acme.wd1.myworkdayjobs.com", "acme", "Careers")
    assert parse_workday_url("https://example.com/careers") is None


@respx.mock
def test_workday_fetch_pages_and_details():
    api = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers"
    respx.post(f"{api}/jobs").respond(json={"total": 2, "jobPostings": [
        {"title": "Intern A", "externalPath": "/job/NYC/Intern-A_R1", "locationsText": "New York"},
        {"title": "Intern B", "externalPath": "/job/Remote/Intern-B_R2", "locationsText": "Remote"},
    ]})
    respx.get(f"{api}/job/NYC/Intern-A_R1").respond(json={
        "jobPostingInfo": {"title": "Intern A", "jobDescription": "<p>SQL all day</p>", "location": "New York",
                           "startDate": "2026-10-25", "jobReqId": "R1",
                           "externalUrl": "https://acme.wd1.myworkdayjobs.com/Careers/job/NYC/Intern-A_R1"},
        "hiringOrganization": {"name": "Acme Corp"}})
    respx.get(f"{api}/job/Remote/Intern-B_R2").respond(status_code=500)
    jobs = WorkdaySource({"url": "https://acme.wd1.myworkdayjobs.com/en-US/Careers", "search": "intern"}, http).fetch()
    assert [j.title for j in jobs] == ["Intern A", "Intern B"]
    assert jobs[0].company == "Acme Corp" and "SQL" in jobs[0].description
    assert jobs[1].remote and jobs[1].url.endswith("/Careers/job/Remote/Intern-B_R2")  # fallback when detail fails
    assert jobs[0].external_id == "/job/NYC/Intern-A_R1"


@respx.mock
def test_remotive_remoteok_jsearch_adzuna():
    respx.get(url__startswith="https://remotive.com/api/remote-jobs").respond(json={"jobs": [
        {"id": 1, "url": "https://remotive.com/1", "title": "Junior Dev", "company_name": "R",
         "publication_date": "2026-10-20T12:00:00", "candidate_required_location": "USA", "description": "<p>Hi</p>"}]})
    respx.get("https://remoteok.com/api").respond(json=[
        {"legal": "notice"},
        {"id": "9", "position": "Data Analyst", "company": "OK", "epoch": 1761000000, "salary_min": 50000,
         "salary_max": 70000, "url": "https://remoteok.com/9", "description": "x"}])
    respx.get(url__startswith="https://jsearch.p.rapidapi.com/search").respond(json={"data": [
        {"job_id": "js1", "employer_name": "LinkCo", "job_publisher": "LinkedIn", "job_title": "Analyst Intern",
         "job_apply_link": "https://linkedin.com/jobs/view/1", "job_description": "desc", "job_city": "NYC",
         "job_state": "NY", "job_country": "US", "job_is_remote": False,
         "job_posted_at_datetime_utc": "2026-10-26T08:00:00.000Z"}]})
    respx.get(url__startswith="https://api.adzuna.com/").respond(json={"results": [
        {"id": 5, "title": "<strong>Junior</strong> Analyst", "company": {"display_name": "Adz"},
         "location": {"display_name": "Boston"}, "description": "d", "redirect_url": "https://adzuna/5",
         "created": "2026-10-26T00:00:00Z"}]})
    assert RemotiveSource({"search": "python"}, http).fetch()[0].remote
    ok = RemoteOKSource({}, http).fetch()
    assert len(ok) == 1 and ok[0].salary == "$50,000–$70,000"
    js = JSearchSource({"query": "analyst", "api_key": "k"}, http).fetch()[0]
    assert js.board == "LinkedIn" and js.location == "NYC, NY, US"
    assert respx.calls[-1].request.headers["X-RapidAPI-Key"] == "k"
    adz = AdzunaSource({"what": "analyst", "app_id": "a", "app_key": "b"}, http).fetch()[0]
    assert adz.title == "Junior Analyst"


@respx.mock
def test_hn_hiring_only_top_level_comments():
    respx.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date?tags=story").respond(json={"hits": [
        {"objectID": "100", "title": "Ask HN: Who is hiring? (October 2026)"}]})
    respx.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date?tags=comment").respond(json={
        "nbPages": 1, "hits": [
            {"objectID": "101", "parent_id": 100, "created_at_i": 1761000000,
             "comment_text": "Acme | Software Engineer Intern | Remote (US) | $40/hr<p>We build rockets with Python."},
            {"objectID": "102", "parent_id": 101, "comment_text": "Is this still open?"},
        ]})
    jobs = HackerNewsHiringSource({}, http).fetch()
    assert len(jobs) == 1
    assert jobs[0].company == "Acme" and jobs[0].title == "Software Engineer Intern"
    assert jobs[0].remote and jobs[0].location.startswith("Remote")


def test_rss_feed_parsing():
    xml = """<?xml version="1.0"?><rss><channel>
      <item><title>Acme: Junior Python Developer</title><link>https://wwr.com/1</link><guid>g1</guid>
        <region>Anywhere in the World</region><pubDate>Mon, 26 Oct 2026 10:00:00 +0000</pubDate>
        <description>&lt;p&gt;Write Python&lt;/p&gt;</description></item>
    </channel></rss>"""
    jobs = RSSSource({"url": "https://weworkremotely.com/x.rss"}, http).parse_feed(xml)
    assert jobs[0].company == "Acme" and jobs[0].title == "Junior Python Developer"
    assert jobs[0].description == "Write Python" and jobs[0].posted_at.day == 26


def test_registry_reports_bad_specs():
    sources, errors = build_sources([
        {"type": "greenhouse", "company": "acme"},
        {"type": "lever"},
        {"type": "nope"},
        {"type": "remotive", "enabled": False},
    ], http)
    assert [s.key for s in sources] == ["greenhouse:acme"]
    assert len(errors) == 2
    assert build_source({"type": "careers_page", "url": "https://x.com/careers"}, http).company_label == "X"


@respx.mock
def test_http_retries_on_429(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    route = respx.get("https://example.com/x").mock(side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": 1})])
    assert Http(retries=2).get_json("https://example.com/x") == {"ok": 1}
    assert route.call_count == 2

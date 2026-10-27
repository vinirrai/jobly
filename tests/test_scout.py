from __future__ import annotations

import json
from datetime import timedelta

import httpx

from jobly.config import Config, NotifyConfig, SearchConfig
from jobly.matcher import evaluate
from jobly.models import Job, utcnow
from jobly.notify import Notifier
from jobly.scout import Scout
from jobly.sources.base import Source
from jobly.store import Store
from jobly.textutil import contains_term, html_to_text


def _job(n: int, **kw) -> Job:
    base = dict(source="fake", external_id=str(n), title=f"Data Analyst Intern {n}", company=f"Co{n}",
                location="Remote", remote=True, url=f"https://x.com/{n}",
                description="Python and SQL and Tableau", board="Fake")
    base.update(kw)
    return Job(**base)


class FakeSource(Source):
    kind = "fake"
    board = "Fake"

    def __init__(self, jobs, name="a"):
        super().__init__({"name": name}, http=None)
        self.jobs = jobs
        self.calls = 0

    def fetch(self):
        self.calls += 1
        return list(self.jobs)


class BoomSource(FakeSource):
    def fetch(self):
        raise RuntimeError("site down")


# ---------------------------------------------------------------- matcher

def test_matcher_filters_and_scores(profile):
    s = SearchConfig(titles_include=["intern"], titles_exclude=["senior"], locations=["new york"],
                     keywords=["tableau"], remote_ok=True)
    good = evaluate(_job(1), s, profile)
    assert good.passed and good.score >= 60 and "Python" in good.matched_skills
    assert not evaluate(_job(2, title="Senior Data Analyst Intern"), s, profile).passed
    assert not evaluate(_job(3, title="Accountant"), s, profile).passed
    assert not evaluate(_job(4, location="Austin, TX", remote=False), s, profile).passed
    assert evaluate(_job(5, location="New York, NY", remote=False), s, profile).passed
    old = _job(6, posted_at=utcnow() - timedelta(days=90))
    assert not evaluate(old, s, profile).passed
    assert evaluate(_job(7), SearchConfig(min_score=101), profile).passed is False


def test_skill_term_matching():
    assert contains_term("We use C++ and Go", "C++")
    assert not contains_term("We use C# daily", "C")
    assert contains_term("Node.js services", "Node.js")
    assert not contains_term("javascript", "Java")


def test_html_to_text_bullets():
    assert html_to_text("<p>Hello</p><ul><li>A</li><li>B</li></ul>") == "Hello\n\n• A\n• B"
    assert html_to_text("<ul><li><p>A</p></li><li><p>B</p></li></ul><p>End</p>") == "• A\n• B\n\nEnd"


# ------------------------------------------------------------------ store

def test_store_upsert_and_fill_in(tmp_path):
    store = Store(tmp_path / "j.db")
    j = _job(1, description="")
    assert store.upsert_job(j, 50, ["r"]) is True
    assert store.upsert_job(_job(1, description="longer text"), 70) is False
    got = store.get_job(j.id)
    assert got.description == "longer text" and got.score == 70 and got.status == "new"
    store.set_status(j.id, "saved")
    assert store.list_jobs(status="saved")[0].id == j.id
    store.set_status(j.id, "dismissed")
    assert store.list_jobs() == []
    assert store.find_duplicate(_job(1, source="other", external_id="zz")) == j.id
    store.set_kit_state(j.id, "running", "x")
    store.save_kit_part(j.id, "analysis", {"requirements": []})
    assert store.get_kit(j.id)["analysis"] == {"requirements": []}
    assert store.get_job(j.id).kit_status == "running"


# ------------------------------------------------------------------ scout

def test_scout_first_run_is_quiet_then_alerts(tmp_path):
    store = Store(tmp_path / "j.db")
    cfg = Config(search=SearchConfig(titles_include=["intern"]))
    fresh = _job(1, posted_at=utcnow() - timedelta(hours=1))
    backlog = _job(2, posted_at=utcnow() - timedelta(days=5))
    unknown_date = _job(3)
    src = FakeSource([fresh, backlog, unknown_date, _job(9, title="Senior Manager")])
    alerts = []
    scout = Scout(cfg, store, sources=[src], on_new_match=lambda j, s: alerts.append(j.id))
    r1 = scout.run_once()
    assert r1.runs[0].fetched == 4 and r1.runs[0].new == 3
    assert alerts == [fresh.id]  # first scan: only the recent posting alerts
    src.jobs.append(_job(4))
    r2 = scout.run_once()
    assert r2.runs[0].new == 1 and alerts[-1] == _job(4).id
    assert len(store.list_jobs()) == 4


def test_scout_dedupes_across_boards_and_records_errors(tmp_path):
    store = Store(tmp_path / "j.db")
    cfg = Config()
    a = FakeSource([_job(1)], name="a")
    b = FakeSource([_job(1, source="fake2", external_id="other", board="LinkedIn")], name="b")
    bad = BoomSource([], name="bad")
    scout = Scout(cfg, store, sources=[a, bad])
    scout.run_once()
    scout2 = Scout(cfg, store, sources=[b])
    report = scout2.run_once()
    assert report.runs[0].new == 0  # same company + title already stored
    st = store.source_statuses()
    assert "site down" in st["fake:bad"]["last_error"]
    assert st["fake:a"]["last_error"] == ""


def test_scout_only_due_respects_intervals(tmp_path):
    store = Store(tmp_path / "j.db")
    src = FakeSource([_job(1)])
    scout = Scout(Config(), store, sources=[src])
    scout.run_once(only_due=True)
    scout.run_once(only_due=True)
    assert src.calls == 1
    scout.run_once()
    assert src.calls == 2


def test_scout_marks_known_jobs(tmp_path):
    store = Store(tmp_path / "j.db")
    src = FakeSource([_job(1)])
    Scout(Config(), store, sources=[src]).run_once()
    assert src.is_known("fake", "1") and not src.is_known("fake", "2")


# ---------------------------------------------------------------- notify

def test_notifier_ntfy_and_discord():
    sent = []

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = NotifyConfig(min_score=50, ntfy_topic="my-topic", webhook_url="https://discord.com/api/webhooks/1/x")
    n = Notifier(cfg, app_url="http://localhost:8000", client=client)
    n.notify([(_job(1), 80), (_job(2), 10)])
    ntfy, discord = sent
    assert str(ntfy.url) == "https://ntfy.sh/my-topic"
    assert ntfy.headers["Click"].startswith("http://localhost:8000/jobs/")
    assert b"Data Analyst Intern 1" in ntfy.content and b"Intern 2" not in ntfy.content
    assert "content" in json.loads(discord.content)
    sent.clear()
    n.notify([(_job(i), 90) for i in range(20)])
    assert b"20 new job matches" not in sent[0].content  # title goes in the header
    assert sent[0].headers["Title"] == "20 new job matches"
    assert b"and 5 more" in sent[0].content


def test_notifier_failure_is_swallowed():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    Notifier(NotifyConfig(min_score=0, webhook_url="https://hooks.slack.com/x"), client=client).notify([(_job(1), 5)])


def test_failed_first_scan_still_counts_as_first(tmp_path):
    store = Store(tmp_path / "j.db")
    alerts = []

    class Flaky(FakeSource):
        def fetch(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("timeout")
            return list(self.jobs)

    src = Flaky([_job(1, posted_at=utcnow() - timedelta(days=5))])
    scout = Scout(Config(), store, sources=[src], on_new_match=lambda j, s: alerts.append(j))
    scout.run_once()
    scout.run_once()
    assert len(store.list_jobs()) == 1 and alerts == []  # old backlog stored quietly

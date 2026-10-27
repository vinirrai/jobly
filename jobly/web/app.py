"""Web dashboard: live job feed, one-click application kits, sources, profile and skill gaps."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..config import example_file, profile_to_yaml
from ..registry import SOURCE_TYPES
from ..service import Jobly, JoblyError, slug
from ..store import JOB_STATUSES
from ..tailor import render
from ..tailor.llm import LLMError
from ..tailor.resume_import import import_resume
from ..textutil import parse_date

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 90:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return ""


def _safe_url(url: str | None) -> str:
    """Only render http(s) links — postings come from outside sources."""
    return url if url and urlparse(url.strip()).scheme in ("http", "https") else "#"


TEMPLATES.env.filters["ago"] = _ago
TEMPLATES.env.filters["safe_url"] = _safe_url


def _redirect(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if msg:
        url += f"{sep}msg={quote(msg)}"
        sep = "&"
    if err:
        url += f"{sep}err={quote(err)}"
    return RedirectResponse(url, status_code=303)


def create_app(jobly: Jobly, start_scout: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scout and jobly.cfg.scout.enabled:
            jobly.scout.start()
        yield
        jobly.close()

    app = FastAPI(title="Jobly", lifespan=lifespan)

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.setdefault("msg", request.query_params.get("msg", ""))
        ctx.setdefault("err", request.query_params.get("err", ""))
        ctx["scout_running"] = jobly.scout.running
        ctx["counts"] = jobly.store.counts()
        return TEMPLATES.TemplateResponse(request, name, ctx)

    def get_job_or_404(job_id: str):
        job = jobly.store.get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job

    # ---------------------------------------------------------------- feed

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, status: str = "", q: str = "", min_score: int = 0):
        jobs = jobly.store.list_jobs(status=status or None, q=q or None, min_score=min_score or None)
        return page(request, "dashboard.html", jobs=jobs, status=status, q=q, min_score=min_score,
                    statuses=JOB_STATUSES, now=datetime.now(timezone.utc).isoformat(),
                    has_profile=jobly.paths.profile.exists(), source_count=len(jobly.scout.sources))

    @app.get("/api/new-count")
    def new_count(since: str):
        dt = parse_date(since)
        return {"count": len(jobly.store.list_jobs(since=dt, limit=500)) if dt else 0}

    @app.post("/scan")
    def scan():
        threading.Thread(target=jobly.scout.run_once, daemon=True).start()
        return _redirect("/", msg="Scanning all sources now — new jobs will appear in a moment.")

    # ----------------------------------------------------------------- job

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str, tab: str = "fit"):
        job = get_job_or_404(job_id)
        kit = jobly.kit_parts(job_id)
        return page(request, "job.html", job=job, kit=kit, tab=tab, statuses=JOB_STATUSES)

    @app.post("/jobs/{job_id}/status")
    def set_status(job_id: str, status: str = Form(...), back: str = Form("")):
        get_job_or_404(job_id)
        jobly.store.set_status(job_id, status)
        return _redirect(back or f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/description")
    def paste_description(job_id: str, description: str = Form(...)):
        try:
            jobly.paste_description(job_id, description)
        except JoblyError as e:
            return _redirect(f"/jobs/{job_id}", err=str(e))
        return _redirect(f"/jobs/{job_id}", msg="Description saved.")

    @app.post("/jobs/{job_id}/kit")
    def make_kit(job_id: str):
        get_job_or_404(job_id)
        jobly.request_kit(job_id)
        return _redirect(f"/jobs/{job_id}")

    @app.get("/api/jobs/{job_id}/kit")
    def kit_status(job_id: str):
        kit = jobly.store.get_kit(job_id)
        if not kit:
            return {"status": "none"}
        return {"status": kit["status"], "stage": kit["stage"], "error": kit["error"],
                "parts": [p for p in ("analysis", "resume", "cover_letter", "learning_plan") if kit.get(p)]}

    def _kit_or_404(job_id: str, part: str):
        kit = jobly.kit_parts(job_id)
        if not kit or not kit.get(part):
            raise HTTPException(404, "Not generated yet")
        return kit[part]

    def _download(content: bytes | str, filename: str, media: str) -> Response:
        return Response(content, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def _stem(job_id: str, what: str) -> str:
        job = get_job_or_404(job_id)
        profile = jobly.load_profile_safe()
        who = slug(profile.name, 20) if profile else "me"
        return f"{who}-{what}-{slug(job.company or job.title, 24)}"

    @app.get("/jobs/{job_id}/resume.html", response_class=HTMLResponse)
    def resume_html(job_id: str):
        return render.resume_html(_kit_or_404(job_id, "resume"))

    @app.get("/jobs/{job_id}/resume.docx")
    def resume_docx(job_id: str):
        return _download(render.resume_docx(_kit_or_404(job_id, "resume")), _stem(job_id, "resume") + ".docx", DOCX)

    @app.get("/jobs/{job_id}/resume.md")
    def resume_md(job_id: str):
        return _download(render.resume_markdown(_kit_or_404(job_id, "resume")),
                         _stem(job_id, "resume") + ".md", "text/markdown")

    @app.get("/jobs/{job_id}/cover-letter.html", response_class=HTMLResponse)
    def letter_html(job_id: str):
        return render.cover_letter_html(_kit_or_404(job_id, "cover_letter"), jobly.require_profile())

    @app.get("/jobs/{job_id}/cover-letter.docx")
    def letter_docx(job_id: str):
        data = render.cover_letter_docx(_kit_or_404(job_id, "cover_letter"), jobly.require_profile())
        return _download(data, _stem(job_id, "cover-letter") + ".docx", DOCX)

    @app.get("/jobs/{job_id}/cover-letter.txt", response_class=PlainTextResponse)
    def letter_txt(job_id: str):
        return render.cover_letter_text(_kit_or_404(job_id, "cover_letter"), jobly.require_profile())

    @app.get("/jobs/{job_id}/learning-plan.md")
    def plan_md(job_id: str):
        job = get_job_or_404(job_id)
        text = render.learning_plan_markdown(_kit_or_404(job_id, "learning_plan"), job.title, job.company)
        return _download(text, _stem(job_id, "learning-plan") + ".md", "text/markdown")

    # ----------------------------------------------------------------- add

    @app.get("/add", response_class=HTMLResponse)
    def add_page(request: Request):
        return page(request, "add.html")

    @app.post("/add/url")
    def add_url(url: str = Form(...)):
        try:
            job = jobly.import_url(url)
        except JoblyError as e:
            return _redirect("/add", err=str(e))
        return _redirect(f"/jobs/{job.id}", msg="Job imported.")

    @app.post("/add/manual")
    def add_manual(title: str = Form(...), company: str = Form(""), description: str = Form(...),
                   url: str = Form(""), location: str = Form("")):
        try:
            job = jobly.add_manual(title, company, description, url, location)
        except JoblyError as e:
            return _redirect("/add", err=str(e))
        return _redirect(f"/jobs/{job.id}", msg="Job added.")

    # ------------------------------------------------------------- sources

    @app.get("/sources", response_class=HTMLResponse)
    def sources_page(request: Request):
        status = jobly.store.source_statuses()
        rows = [{"key": s.key, "kind": s.kind, "board": s.board,
                 "interval": s.interval_minutes(jobly.cfg.scout.default_interval_minutes),
                 "status": status.get(s.key, {})} for s in jobly.scout.sources]
        return page(request, "sources.html", rows=rows, custom=jobly.store.custom_sources(),
                    errors=jobly.scout.source_errors, types=sorted(SOURCE_TYPES),
                    config_path=str(jobly.paths.config))

    @app.post("/sources")
    def add_source(url: str = Form(...), search: str = Form("")):
        spec = jobly.add_company(url, search)
        what = spec["type"].replace("_", " ")
        threading.Thread(target=jobly.scout.run_once, daemon=True).start()
        return _redirect("/sources", msg=f"Watching it now (detected: {what}). First scan started.")

    @app.post("/sources/{source_id}/delete")
    def delete_source(source_id: int):
        jobly.remove_company(source_id)
        return _redirect("/sources", msg="Stopped watching that source.")

    # ------------------------------------------------------------- profile

    @app.get("/profile", response_class=HTMLResponse)
    def profile_page(request: Request):
        if jobly.paths.profile.exists():
            text = jobly.paths.profile.read_text(encoding="utf-8")
        else:
            text = example_file("profile.example.yaml")
        return page(request, "profile.html", text=text, exists=jobly.paths.profile.exists(),
                    path=str(jobly.paths.profile))

    @app.post("/profile")
    def save_profile(text: str = Form(...)):
        try:
            jobly.save_profile_yaml(text)
        except JoblyError as e:
            return _redirect("/profile", err=f"Not saved — {e}")
        return _redirect("/profile", msg="Profile saved.")

    @app.post("/profile/import")
    def import_profile(file: UploadFile = File(...)):
        data = file.file.read()
        if jobly.paths.profile.exists():
            backup = jobly.paths.profile.with_suffix(".backup.yaml")
            backup.write_text(jobly.paths.profile.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            profile = import_resume(jobly.llm, file.filename or "resume.txt", data)
        except LLMError as e:
            return _redirect("/profile", err=str(e))
        jobly.paths.profile.write_text(profile_to_yaml(profile), encoding="utf-8")
        return _redirect("/profile", msg="Imported! Review it, then add your stories — they power the cover letters.")

    # -------------------------------------------------------------- skills

    @app.get("/skills", response_class=HTMLResponse)
    def skills_page(request: Request):
        return page(request, "skills.html", rows=jobly.skill_gaps(), analyzed=len(jobly.store.all_analyses()))

    @app.get("/healthz")
    def health():
        return JSONResponse({"ok": True, "scout_running": jobly.scout.running})

    return app

"""Command line: `jobly init`, `jobly serve`, `jobly scout`, `jobly kit <job>`, ..."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import Paths, init_home, profile_to_yaml


def _jobly(args):
    from .service import Jobly

    return Jobly(Paths(Path(args.home)) if args.home else Paths(), app_url=f"http://localhost:{getattr(args, 'port', 8000)}")


def cmd_init(args) -> int:
    paths = Paths(Path(args.home)) if args.home else Paths()
    created = init_home(paths, overwrite=args.force)
    print(f"Jobly home: {paths.root}")
    for p in created:
        print(f"  created {p}")
    if not created:
        print("  (config.yaml and profile.yaml already exist — use --force to reset them)")
    print("\nNext steps:\n"
          f"  1. Edit {paths.profile} (or import your resume: jobly import-resume resume.pdf)\n"
          f"  2. Edit {paths.config} — your target roles, locations and sources\n"
          "  3. export ANTHROPIC_API_KEY=...   (for tailored resumes and cover letters)\n"
          "  4. jobly serve   → open http://localhost:8000")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .web.app import create_app

    jobly = _jobly(args)
    app = create_app(jobly, start_scout=not args.no_scout)
    print(f"Jobly running at http://{args.host}:{args.port}  (data: {jobly.paths.root})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_scout(args) -> int:
    jobly = _jobly(args)
    if not jobly.scout.sources:
        print("No sources configured. Add some to", jobly.paths.config)
        return 1
    for err in jobly.scout.source_errors:
        print("  skipped:", err)
    if args.once:
        report = jobly.scout.run_once()
        for run in report.runs:
            status = f"ERROR {run.error}" if run.error else f"{run.fetched} listed, {run.stored} matched, {run.new} new"
            print(f"  {run.key}: {status}")
        print(f"{report.new_count} new matching jobs.")
        return 0
    print(f"Scouting {len(jobly.scout.sources)} sources. Ctrl+C to stop.")
    jobly.scout.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        jobly.close()
    return 0


def cmd_list(args) -> int:
    jobly = _jobly(args)
    jobs = jobly.store.list_jobs(status=args.status or None, min_score=args.min_score or None, limit=args.limit)
    for j in jobs:
        where = f" — {j.location}" if j.location else ""
        print(f"{j.id}  [{j.score:>3}]  {j.title} @ {j.company or '?'}{where}  ({j.board})")
    if not jobs:
        print("No jobs yet. Run `jobly scout --once`.")
    return 0


def cmd_add_url(args) -> int:
    from .service import JoblyError

    jobly = _jobly(args)
    try:
        job = jobly.import_url(args.url)
    except JoblyError as e:
        print("Error:", e)
        return 1
    print(f"Added {job.id}: {job.title} @ {job.company} (match {job.score})")
    return 0


def cmd_add_company(args) -> int:
    jobly = _jobly(args)
    spec = jobly.add_company(args.url, args.search or "")
    print("Watching:", spec)
    return 0


def cmd_kit(args) -> int:
    from .service import JoblyError
    from .tailor.llm import LLMError

    jobly = _jobly(args)
    if not jobly.store.get_job(args.job_id):
        print(f"No job with id {args.job_id} — see `jobly list`.")
        return 1
    print("Writing your application kit (analysis → resume, cover letter, learning plan)…")
    try:
        folder = jobly.build_kit(args.job_id)
    except (JoblyError, LLMError) as e:
        jobly.store.set_kit_state(args.job_id, "error", "", str(e))
        print("Error:", e)
        return 1
    print("Done! Files saved in", folder)
    for f in sorted(folder.iterdir()):
        print("  ", f.name)
    return 0


def cmd_import_resume(args) -> int:
    from .tailor.llm import LLMError
    from .tailor.resume_import import import_resume

    jobly = _jobly(args)
    src = Path(args.file)
    try:
        profile = import_resume(jobly.llm, src.name, src.read_bytes())
    except LLMError as e:
        print("Error:", e)
        return 1
    if jobly.paths.profile.exists() and not args.force:
        out = jobly.paths.profile.with_name("profile.imported.yaml")
        print(f"profile.yaml already exists; wrote {out} instead (use --force to overwrite).")
    else:
        out = jobly.paths.profile
    out.write_text(profile_to_yaml(profile), encoding="utf-8")
    print("Saved", out, "— review it and add a few `stories` for stronger cover letters.")
    return 0


def cmd_sources(args) -> int:
    jobly = _jobly(args)
    statuses = jobly.store.source_statuses()
    for src in jobly.scout.sources:
        st = statuses.get(src.key, {})
        state = st.get("last_error") or (f"ok, {st.get('last_count')} listed" if st.get("last_run") else "not scanned yet")
        print(f"{src.key:60}  every {src.interval_minutes(jobly.cfg.scout.default_interval_minutes):g}m  {state}")
    for err in jobly.scout.source_errors:
        print("skipped:", err)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobly", description="Real-time job scout + tailored applications.")
    parser.add_argument("--home", help="data folder (default: $JOBLY_HOME or ~/.jobly)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create config.yaml and profile.yaml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("serve", help="run the web app with live scouting")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-scout", action="store_true", help="don't poll sources in the background")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("scout", help="poll sources without the web app")
    p.add_argument("--once", action="store_true", help="scan every source once and exit")
    p.set_defaults(fn=cmd_scout)

    p = sub.add_parser("list", help="list stored jobs")
    p.add_argument("--status")
    p.add_argument("--min-score", type=int, default=0)
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("add-url", help="import one job posting by URL")
    p.add_argument("url")
    p.set_defaults(fn=cmd_add_url)

    p = sub.add_parser("add-company", help="watch a company's careers page")
    p.add_argument("url")
    p.add_argument("--search", help="keyword filter (Workday/SmartRecruiters)")
    p.set_defaults(fn=cmd_add_company)

    p = sub.add_parser("kit", help="write the resume, cover letter and learning plan for a job")
    p.add_argument("job_id")
    p.set_defaults(fn=cmd_kit)

    p = sub.add_parser("import-resume", help="create profile.yaml from your resume (PDF/DOCX/TXT)")
    p.add_argument("file")
    p.add_argument("--force", action="store_true", help="overwrite an existing profile.yaml")
    p.set_defaults(fn=cmd_import_resume)

    p = sub.add_parser("sources", help="show sources and their health")
    p.set_defaults(fn=cmd_sources)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

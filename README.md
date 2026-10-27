# Jobly

Jobly watches job boards and company career sites around the clock, alerts you the moment a matching job is posted, and writes a tailored application for it:

- **Fit analysis** — every requirement in the posting sorted into *have it*, *easy to learn* (interview-ready in ~40 hours or less) and *stretch* (hard skills that take weeks or months).
- **Tailored resume** — your real experience reworded in the posting's language so applicant-tracking systems match it. Easy-to-learn skills go straight into your Skills section (you can change this), and each one gets a study sprint so you can back it up in the interview.
- **Cover letter** — built around your passion for the company, your work ethic and your creativity, using true stories from your profile. For the hard requirements you don't have yet, it connects them to your transferable skills and shows adaptability and grit instead of pretending.
- **Learning plan** — a day-by-day interview sprint for the easy skills, and a longer week-by-week plan with portfolio projects for the hard ones.
- **Skill gaps** — across every job you've analyzed, which missing skills come up most, so you know what to learn first.

Downloads come as Word (.docx), print-ready HTML (save as PDF) and Markdown.

## Where jobs come from

| Source | How | Speed |
|---|---|---|
| Company career sites on **Greenhouse, Lever, Ashby, Workday, SmartRecruiters** | Their public job-board APIs (the same ones the career pages use) | Every 10 min (configurable) |
| **Any careers page**, including small startups | Paste the URL. Jobly detects the system behind it, or reads the page's job data (schema.org `JobPosting`), or follows job links | Every 30 min |
| **LinkedIn, Indeed, Handshake, Glassdoor, ZipRecruiter, Wellfound** | Your job-alert emails, read over IMAP | ~2 min after the alert lands |
| **LinkedIn, Indeed, Glassdoor, ZipRecruiter** search | [JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) (Google for Jobs) with a free API key | Hourly (free-tier quota) |
| **Remotive, RemoteOK, Hacker News "Who is hiring"**, any **RSS** feed, **Adzuna** | Public APIs / feeds | Every 15–30 min |
| Anything else | Paste a link or the description on the **Add job** page | Instant |

**Why not scrape LinkedIn, Indeed and Handshake directly?** They block scrapers aggressively and their terms prohibit it, so a scraper breaks constantly and can get your account banned. Their own job-alert emails are instant, reliable and allowed, so Jobly reads those. When you open a job that came from an alert, Jobly fetches the full description from the public posting page. Handshake postings need a login, so for those you paste the description (one click on the job page).

Every new job goes through a free, offline filter (title, location, age, and how many of your skills it mentions) before any AI is used. Only matches are stored and alerted.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/vinirrai/jobly.git
cd jobly
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

jobly init                        # creates ~/.jobly/config.yaml and ~/.jobly/profile.yaml
export ANTHROPIC_API_KEY=sk-ant-...   # from console.anthropic.com (Windows: set ANTHROPIC_API_KEY=...)
jobly serve                       # open http://localhost:8000
```

Then:

1. **Profile** page → upload your current resume (PDF/Word) to fill in your profile, or edit the YAML directly. Add a few true **stories** (times you learned something fast, pushed through something hard, or solved a problem creatively). The cover letters are built from these.
2. Edit `~/.jobly/config.yaml` → your target titles, locations and sources (see below).
3. **Sources** page → paste the careers pages of companies you'd love to work at.
4. Open any job → **Generate application kit**.

Set `JOBLY_HOME` to keep your data somewhere other than `~/.jobly`.

## Configuration

`config.yaml` (created by `jobly init`, fully commented):

```yaml
search:
  titles_include: [intern, new grad, junior, data analyst]
  titles_exclude: [senior, staff, principal, manager]
  keywords: [python, sql]
  locations: [remote, new york, boston]
  remote_ok: true

notify:
  min_score: 40
  ntfy_topic: my-secret-job-alerts-123   # free phone push via the ntfy app
  webhook_url: ""                        # Slack or Discord webhook

ai:
  model: claude-opus-5-5
  effort: high                  # low | medium | high | xhigh | max
  auto_tailor_min_score: 0      # e.g. 70 = write kits automatically for strong matches

sources:
  - type: greenhouse
    company: stripe             # boards.greenhouse.io/stripe
  - type: lever
    company: palantir
  - type: workday
    url: https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
    search: intern
  - type: careers_page
    url: https://www.somestartup.com/careers
  - type: email_alerts
    host: imap.gmail.com
    username: you@gmail.com
    password: ${JOBLY_IMAP_PASSWORD}    # read from the environment
  - type: jsearch
    query: data analyst intern in New York
    api_key: ${RAPIDAPI_KEY}
  - type: hn_hiring
```

Any value written as `${NAME}` is read from an environment variable, so passwords and keys stay out of the file. Every source accepts `interval_minutes:` and `enabled: false`.

### Instant alerts from LinkedIn, Indeed and Handshake

1. On each site, create job alerts for your searches and set the frequency to the fastest option ("as jobs are posted" / "daily").
2. In your Google Account: **Security → 2-Step Verification → App passwords**, create one for "Jobly". Make sure IMAP is on in Gmail settings.
3. `export JOBLY_IMAP_PASSWORD=<the app password>` and add the `email_alerts` source above.

Jobly only reads messages (it never marks them read or deletes anything) from job-board senders.

### Phone notifications

Install the [ntfy](https://ntfy.sh) app, subscribe to a long, hard-to-guess topic name, and put that name in `notify.ntfy_topic`. Each alert links straight to the job in Jobly.

### Easy-to-learn skills on your resume

`preferences.learnable_skills_mode` in your profile controls this:

- `list` (default) — listed in your Skills section, worded the way the posting words them. They're never added to job or project bullets, and certifications are never added. Each one shows up on the job page under **Learn before your interview** with a sprint plan, so you can talk about it confidently.
- `learning` — listed on a separate "Currently learning" line.
- `off` — left off the resume; still in your learning plan.

Hard ("stretch") skills are never listed on your resume. The cover letter addresses them through your transferable skills, adaptability and grit.

Tailoring only rewords and reorders what's in your profile. Employers, titles, dates, schools and contact details are always copied from your profile by the code, so a tailored resume can't contain a job or degree you don't have.

## Cost

Scouting is free. Generating a kit makes 4 Claude API calls (analysis, resume, cover letter, learning plan). With `claude-opus-5-5` at `effort: high`, expect roughly $0.30–$1 per job depending on how long the posting and your profile are (check actual usage in the Anthropic console). Use `effort: medium` to spend less, and keep `auto_tailor_min_score` at 0 (manual) or set it high so kits are only written for strong matches. Your profile is sent as a cached prefix, which makes repeated calls cheaper.

## Running it 24/7

Real-time scouting only works while Jobly is running. Leave `jobly serve` running on your computer, or run it on any always-on machine (a small VPS, a Raspberry Pi, a home server) with Docker:

```bash
mkdir -p jobly_data
docker compose run --rm jobly init          # creates jobly_data/config.yaml and profile.yaml
# edit jobly_data/config.yaml and jobly_data/profile.yaml, put secrets in .env
docker compose up -d                         # http://<server>:8000
```

`.env` example:

```
ANTHROPIC_API_KEY=sk-ant-...
JOBLY_IMAP_PASSWORD=...
JOBLY_NTFY_TOPIC=my-secret-job-alerts-123
```

The web app has no login, so don't expose port 8000 to the internet — keep it on your home network or reach it through an SSH tunnel or Tailscale.

## Command line

```
jobly init                     create config.yaml and profile.yaml
jobly serve [--port 8000]      web app + live scouting
jobly scout [--once]           scout without the web app
jobly sources                  list sources and their health
jobly list [--status saved]    list jobs
jobly add-url <url>            import one posting
jobly add-company <url>        watch a company's careers page
jobly kit <job-id>             write the resume, cover letter and learning plan
jobly import-resume <file>     build profile.yaml from your resume
```

Kits are also saved as files under `~/.jobly/kits/<company>--<role>/`.

## Privacy

Everything (your profile, jobs, generated documents) stays on your machine in `~/.jobly`. The only things sent out are requests to the job sources you configure, and the profile and job description sent to the Claude API when you generate a kit or import a resume.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Code layout: `jobly/sources/` (one module per kind of source), `jobly/scout.py` (scheduling, dedupe, alerts), `jobly/matcher.py` (offline scoring), `jobly/tailor/` (prompts, schemas, honesty rules in `assemble.py`, renderers), `jobly/web/` (FastAPI + templates), `jobly/service.py` (ties it together).

## License

MIT

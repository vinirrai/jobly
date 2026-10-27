"""Instant alerts for new matching jobs: phone push (ntfy), Slack/Discord, or email."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from .config import NotifyConfig
from .models import Job

log = logging.getLogger(__name__)


def _line(job: Job, score: int) -> str:
    where = f" — {job.location}" if job.location else ""
    company = f" @ {job.company}" if job.company else ""
    return f"[{score}] {job.title}{company}{where} ({job.board})"


class Notifier:
    def __init__(self, cfg: NotifyConfig, app_url: str = "http://localhost:8000",
                 client: httpx.Client | None = None):
        self.cfg = cfg
        self.app_url = app_url.rstrip("/")
        self.client = client or httpx.Client(timeout=15)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.ntfy_topic or self.cfg.webhook_url or self.cfg.email.to)

    def notify(self, matches: list[tuple[Job, int]]) -> None:
        matches = [(j, s) for j, s in matches if s >= self.cfg.min_score]
        if not matches or not self.enabled:
            return
        matches.sort(key=lambda m: -m[1])
        if len(matches) == 1:
            job, score = matches[0]
            title = f"New job match: {job.title}"
            body = _line(job, score)
            click = f"{self.app_url}/jobs/{job.id}"
        else:
            title = f"{len(matches)} new job matches"
            body = "\n".join(_line(j, s) for j, s in matches[:15])
            if len(matches) > 15:
                body += f"\n…and {len(matches) - 15} more"
            click = self.app_url
        for name, fn in (("ntfy", self._ntfy), ("webhook", self._webhook), ("email", self._email)):
            try:
                fn(title, body, click)
            except Exception as e:  # never let a notification failure stop scouting
                log.warning("%s notification failed: %s", name, e)

    def _ntfy(self, title: str, body: str, click: str) -> None:
        if not self.cfg.ntfy_topic:
            return
        self.client.post(
            f"{self.cfg.ntfy_server.rstrip('/')}/{self.cfg.ntfy_topic}",
            content=body.encode("utf-8"),
            headers={"Title": title.encode("ascii", "ignore").decode(), "Click": click, "Tags": "briefcase"},
        ).raise_for_status()

    def _webhook(self, title: str, body: str, click: str) -> None:
        url = self.cfg.webhook_url
        if not url:
            return
        text = f"*{title}*\n{body}\n{click}"
        if "discord.com" in url or "discordapp.com" in url:
            payload = {"content": text[:1900]}
        else:  # Slack and most other chat webhooks
            payload = {"text": text}
        self.client.post(url, json=payload).raise_for_status()

    def _email(self, title: str, body: str, click: str) -> None:
        e = self.cfg.email
        if not (e.to and e.smtp_host):
            return
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = e.username or e.to
        msg["To"] = e.to
        msg.set_content(f"{body}\n\nOpen Jobly: {click}")
        with smtplib.SMTP(e.smtp_host, e.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if e.username:
                smtp.login(e.username, e.password)
            smtp.send_message(msg)

"""The morning email: what Nightshift did overnight, sent to Lucas's own inbox.

Uses Gmail over SMTP with an app password Lucas creates and pastes into nightshift/.env himself:
    GMAIL_USER=you@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
    NIGHTSHIFT_EMAIL_TO=you@gmail.com        (optional; defaults to GMAIL_USER)
Without those, the digest is saved to runs/digest-<date>.html and a Mac notification says so. Nothing else is sent
anywhere. The digest holds scores, verdicts and the prompt change, never caller names or numbers (real-call nights
are redacted before they're stored).
"""
from __future__ import annotations

import html
import os
import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from pathlib import Path

from . import config

LOCAL = "http://127.0.0.1:8765"


def _diff_lines(diff: str, limit: int = 14) -> list[str]:
    lines = [ln for ln in diff.splitlines() if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
    return lines[:limit] + ([f"... {len(lines) - limit} more lines"] if len(lines) > limit else [])


def render(items: list[dict], date: str) -> tuple[str, str, str]:
    """(subject, plain text, html) for one morning. items: {"title", "night", "summary"|None, "note"}."""
    def waiting(i: dict) -> bool:
        s = i.get("summary") or {}
        return (str(s.get("verdict", "")).startswith(("Ready", "Proposed")) and not s.get("approved")
                and not s.get("rejected"))
    ready = [i for i in items if waiting(i)]
    subject = (f"Nightshift: {len(ready)} fix{'es' if len(ready) != 1 else ''} waiting for you" if ready
               else "Nightshift: nothing to approve this morning")
    text, rows = [f"Nightshift, {date}", ""], []
    for i in items:
        s = i.get("summary") or {}
        verdict = s.get("verdict") or i.get("note") or "No result."
        b, a = s.get("before") or {}, s.get("after") or {}
        score = f"{b['passed']}/{b['total']} -> {a['passed']}/{a['total']} on fresh calls" if a else ""
        link = f"{LOCAL}/night/{i['night']}" if i.get("night") else ""
        what = (s.get("agent_summary") or {}).get("summary") or ""
        body_lines = _diff_lines(s.get("diff", ""), limit=10_000)
        n_changed = max(sum(d.startswith("+") for d in body_lines), sum(d.startswith("-") for d in body_lines))
        diff = ([f"What it changed: {what}"] if what else []) + ([f"{n_changed} lines of the instructions changed."] if n_changed else [])
        if s.get("approved"):
            diff.append(f"Already approved by {s['approved'].get('by')}.")
        text += [f"## {i['title']}", verdict] + ([score] if score else []) + diff + (
            [f"Approve or reject on your Mac: {link}"] if link else []) + [""]
        rows.append(
            f"<h3 style='margin:22px 0 6px'>{html.escape(i['title'])}</h3>"
            f"<p style='margin:0 0 6px;font-weight:600'>{html.escape(verdict)}</p>"
            + (f"<p style='margin:0 0 8px;color:#555'>{html.escape(score)}</p>" if score else "")
            + "".join(f"<p style='margin:0 0 6px'>{html.escape(d)}</p>" for d in diff)
            + (f"<p><a href='{link}'>Approve or reject (on your Mac)</a></p>" if link else ""))
    text += ["Nothing went live. A person approves every change."]
    body = (f"<div style='font:15px/1.5 -apple-system,Segoe UI,sans-serif;max-width:640px'>"
            f"<h2 style='margin:0;color:#17331F'>Nightshift &middot; {html.escape(date)}</h2>{''.join(rows)}"
            f"<p style='color:#777;margin-top:24px'>Nothing went live. A person approves every change.</p></div>")
    return subject, "\n".join(text), body


def send(subject: str, text: str, body: str, date: str, log=print) -> str:
    """Email it if Gmail is set up, else save it and notify. Returns where it went."""
    config.load_env()
    user, pw = os.environ.get("GMAIL_USER", "").strip(), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    to = os.environ.get("NIGHTSHIFT_EMAIL_TO", "").strip() or user
    saved = config.RUNS / f"digest-{date}.html"
    config.RUNS.mkdir(exist_ok=True)
    saved.write_text(f"<!doctype html><meta charset=utf-8><title>{html.escape(subject)}</title>{body}")
    if user and pw and to:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, user, to
        msg.set_content(text)
        msg.add_alternative(body, subtype="html")
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
                s.login(user, pw)
                s.send_message(msg)
            log(f"Morning email sent to {to}")
            return f"email:{to}"
        except (smtplib.SMTPException, OSError) as e:
            log(f"Email failed ({type(e).__name__}); saved {saved.name} instead")
    notify(subject, f"Saved {saved.name}" + ("" if user else " (add GMAIL_USER + GMAIL_APP_PASSWORD to .env for email)"))
    return f"file:{saved}"


def notify(title: str, message: str) -> None:
    """A macOS notification. Best effort: silent if it can't."""
    esc = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    try:
        subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def last_digest() -> Path | None:
    files = sorted(config.RUNS.glob("digest-*.html")) if config.RUNS.exists() else []
    return files[-1] if files else None

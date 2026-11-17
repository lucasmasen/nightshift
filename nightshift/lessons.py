"""Nightshift's memory of its own mistakes: every rejected fix becomes a lesson the next night's agent reads.

Written automatically when the proof rejects a fix (a case got worse), and by a person with `reject --why`.
Lessons never contain transcripts, only what the change was and what it broke.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import config

LESSONS = config.PROJECT / "lessons.md"
HEADER = ("# Lessons from rejected fixes\n\nEach entry is a past fix that was rejected, and why. Don't make the same "
          "change again; if a failure still needs fixing, find a different way that keeps these cases passing.\n")


def read(path: Path = LESSONS) -> str:
    return path.read_text() if path.exists() else ""


def add(night: str, text: str, by: str = "nightshift", path: Path = LESSONS) -> bool:
    """Append one lesson; skips an exact duplicate (same night, same text). Returns True if written."""
    body = read(path) or HEADER
    entry = f"\n## {night} ({by}, {datetime.now():%b %d %I:%M%p})\n{text.strip()}\n"
    if f"## {night} ({by}," in body and text.strip() in body:
        return False
    path.write_text(body + entry)
    return True


def learn(night: str, summary: dict, path: Path = LESSONS) -> bool:
    """Write a lesson when the proof shows a case got worse or the verdict says don't approve."""
    worse = summary.get("worse") or []
    rejected = str(summary.get("verdict", "")).startswith("Don't approve")
    if not (worse and rejected):
        return False
    b = (summary.get("before") or {}).get("cases", {})
    a = (summary.get("after") or {}).get("cases", {})
    drops = ", ".join(f"{c} ({b.get(c, [0, 0])[0]}/{b.get(c, [0, 0])[1]} to {a.get(c, [0, 0])[0]}/{a.get(c, [0, 0])[1]})"
                      for c in worse)
    what = (summary.get("agent_summary") or {}).get("summary") or "the proposed change"
    diff = "\n".join(ln for ln in str(summary.get("diff", "")).splitlines()
                     if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---")))[:1500]
    text = (f"Rejected by the proof. The change ({what}) made these calls worse on fresh re-runs: {drops}.\n"
            + (f"The rejected change (- removed, + added):\n```\n{diff}\n```" if diff else ""))
    return add(night, text, path=path)

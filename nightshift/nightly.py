"""One whole night, unattended: what the 2am schedule runs.

1. Real calls: anyone who called the demo line in the last day (read-only from Vapi, redacted). If any were
   flagged, a real-call night fixes them and proves the fix on simulated twins.
2. Simulated night: every test case with the latest approved prompt, then the agent fixes the most-failed cases
   and the proof re-tests them.
3. The morning email: each night's verdict, score and change, with an Approve link for the Mac.

Each step runs as its own process, so one failing doesn't stop the others, and the $2/day simulator cap (enforced
by suite.py) is respected: a refused step says so in the email. Nothing ever goes live.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import config, dashboard, mail


def ensure_server(port: int = 8765, log=print) -> None:
    """The email's Approve links point at the local web app; start it if it isn't running."""
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            return
    logf = open(config.RUNS / "serve.log", "a")  # noqa: SIM115 - handed to the child process
    subprocess.Popen([sys.executable, "-m", "nightshift", "serve", "--port", str(port)], cwd=config.PROJECT,
                     stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    log(f"Started the web app on http://127.0.0.1:{port} for the Approve links")


def pending_proofs() -> list[Path]:
    """Nights with a fix that passed the safety check but were never re-tested (e.g. made after the day's test
    budget ran out). The nightly run proves these first, so a fix never waits on someone remembering to."""
    out = []
    for d, s in dashboard.nights():
        if (s.get("diff") and not s.get("guard_problems") and not s.get("after") and s.get("mode") != "agent"
                and (s.get("trace") or {}).get("where") != "mock" and not s.get("rejected")
                and not s.get("superseded_by")
                and (s.get("retest_cases") or s.get("failed_cases"))):
            out.append(d)
    return out


def latest_approved() -> Path | None:
    for d, s in dashboard.nights():  # newest first
        if s.get("approved") and s.get("mode") not in ("real", "agent"):
            return d
    return None


def _run(args: list[str], log) -> tuple[int, str, Path | None]:
    """Run one nightshift command; return (exit code, output, the night folder it made, if any)."""
    before = {p.name for p in config.RUNS.glob("20*")} if config.RUNS.exists() else set()
    p = subprocess.run([sys.executable, "-m", "nightshift", *args], cwd=config.PROJECT, capture_output=True,
                       text=True, timeout=3 * 3600)
    out = p.stdout + p.stderr
    log(out.rstrip())
    new = sorted({p.name for p in config.RUNS.glob("20*")} - before) if config.RUNS.exists() else []
    night = next((config.RUNS / n for n in reversed(new) if (config.RUNS / n / "summary.json").exists()), None)
    return p.returncode, out, night


def _item(title: str, code: int, out: str, night: Path | None) -> dict:
    if night:
        return {"title": title, "night": night.name, "summary": json.loads((night / "summary.json").read_text())}
    note = ("Skipped: today's $2 test budget was already used." if code == 2 or "cap" in out.lower() or "Refused" in out else
            "Didn't run: " + (out.strip().splitlines() or ["no output"])[-1][:200])
    return {"title": title, "night": None, "summary": None, "note": note}


def run(real_days: int = 1, max_cases: int = 4, send_email: bool = True, log=print) -> list[dict]:
    date = datetime.now().strftime("%Y-%m-%d")
    log(f"Nightshift nightly {datetime.now():%Y-%m-%d %H:%M}")
    items: list[dict] = []

    for d in pending_proofs():
        code, out, _ = _run(["retest", str(d), "--repeat", "4"], log)
        if code == 2:
            items.append({"title": f"Proof of an earlier fix ({d.name})", "night": d.name, "summary": None,
                          "note": "Skipped: today's $2 test budget was already used. It runs on the next one."})
        else:
            items.append(_item(f"Proof of an earlier fix ({d.name})", code, out, d if code == 0 else None))

    code, out, night = _run(["night", "--real-days", str(real_days), "--new-only", "--max-cases", "3", "--guards", "2"], log)
    if "No new real calls" in out or (night and (json.loads((night / "summary.json").read_text())
                                                 .get("baseline_total") or 0) == 0):
        items.append({"title": "Real calls to the demo line", "night": None, "summary": None,
                      "note": "No one called the demo line since yesterday."})
    else:
        items.append(_item("Real calls to the demo line", code, out, night))

    args = ["night", "--max-cases", str(max_cases)]
    base = latest_approved()
    if base:
        args += ["--on-top-of", str(base)]
    code, out, night = _run(args, log)
    items.append(_item("Simulated calls" + (f" (building on approved {base.name})" if base else ""), code, out, night))

    from . import agents
    for name, entry in agents.registered().items():
        folder = agents.prepare(name, entry, log=log)
        if folder is None:
            items.append({"title": f"{name} (other agent)", "night": None, "summary": None,
                          "note": "No new calls since yesterday, or no key."})
            continue
        code, out, night = _run(["agent", str(folder)], log)
        items.append(_item(f"{name} (other agent)", code, out, night))

    ensure_server(log=log)
    if send_email:
        subject, text, body = mail.render(items, date)
        mail.send(subject, text, body, date, log=log)
    return items

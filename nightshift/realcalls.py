"""Real-call mode: start the night from the demo line's real calls instead of simulated ones.

demo-factory's real_call_qa reads the real calls from Vapi (read-only, free) and flags problems with heuristics,
phone numbers masked. Nightshift then runs demo-factory's redact tool over that report (names, addresses, emails,
the shop's real name) before anything leaves this Mac, and hands the redacted report to the fixer.

Real calls are few and can't be replayed, so the proof still runs on simulated "twins": the simulator cases that
test the same behavior as each flag.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config

DF = config.WORKSPACE / "demo-factory"
PY = DF / ".venv" / "bin" / "python"

# Each real-call flag -> simulator cases that check the same behavior (case names from demo_line/suite.py --list).
FLAG_TO_CASES = {
    "emergency_no_address": ["burst_pipe", "fast_address"],
    "number_question_twice": ["no_caller_id", "corrects_number"],
    "name_never_asked": ["book_basic", "burst_pipe"],
    "person_callback_in_emergency": ["burst_pipe", "alert_fails"],
    "invented_name": ["skeptic_reveals_plumber"],
    "on_the_way": ["in_an_hour_flooding", "burst_pipe"],
    "hung_up_first": ["rambler", "hard_of_hearing"],
    "stock_identity_line": ["robot_repeat"],
    "triage_aloud": ["book_earliest"],
}
IGNORED = {"review_call"}  # a pointer to read the call by hand, not a behavior to fix

CALL = re.compile(r"^## (.+?) · call (\w+) · (\d+)s", re.M)
FLAG = re.compile(r"^- \*\*([a-z_]+)\*\*:", re.M)


@dataclass
class RealNight:
    report: str  # redacted QA report, the fixer's brief
    calls: int
    flagged_calls: int
    flag_counts: dict[str, int]
    cases: list[str]  # simulator twins, most-flagged first


def _run(args: list[str], stdin: str | None = None) -> str:
    p = subprocess.run([str(PY), *args], cwd=DF, input=stdin, capture_output=True, text=True, timeout=600)
    if p.returncode not in (0, 1):
        raise RuntimeError(f"{' '.join(args[:3])} failed: {p.stderr[-500:]}")
    return p.stdout


def parse(report: str) -> tuple[int, int, dict[str, int]]:
    """(calls, flagged calls, behavior-flag counts) from a real_call_qa report."""
    sections = CALL.split(report)
    calls = len(CALL.findall(report))
    counts: dict[str, int] = {}
    flagged = 0
    # split() gives [preamble, when, id, secs, body, when, id, secs, body, ...]
    for body in sections[4::4]:
        flags = {f for f in FLAG.findall(body) if f not in IGNORED}
        flagged += bool(flags)
        for f in flags:
            counts[f] = counts.get(f, 0) + 1
    return calls, flagged, counts


def twins(counts: dict[str, int], limit: int) -> list[str]:
    out: list[str] = []
    for flag in sorted(counts, key=lambda f: -counts[f]):
        for case in FLAG_TO_CASES.get(flag, []):
            if case not in out:
                out.append(case)
    return out[:limit]


def fixed_flags(agent_report: str) -> list[str]:
    """Flags the agent says it fixed: the ones named before its "left alone / not caused" section."""
    cut = re.search(r"^#+ .*(left alone|not caused|false positive|not fixed)", agent_report, re.I | re.M)
    head = agent_report[:cut.start()] if cut else agent_report
    return [f for f in dict.fromkeys(re.findall(r"`([a-z_]+)`", head)) if f in FLAG_TO_CASES]


NINE11 = re.compile(r"\b9[\s-]?1[\s-]?1\b|\bnine[\s-]one[\s-]one\b", re.I)
SEP = "\n\n=====NIGHTSHIFT-REPORT-STARTS=====\n\n"


def full_transcripts(days: int) -> str:
    """The window's whole calls, so the redactor learns every name a caller gave ("this is X") before it cleans the
    report's short excerpts, where a name can appear with nothing marking it as one. Read-only; empty if it can't."""
    try:
        from . import vapi_import
        state = json.loads((DF / "demo_line" / "state.json").read_text())
        env = dict(ln.split("=", 1) for ln in (DF / ".env").read_text().splitlines() if "=" in ln and not ln.startswith("#"))
        key = env.get("VOICE_PLATFORM_API_KEY", "").strip().strip('"').strip("'")
        calls = vapi_import._get("/call", key, {"assistantId": state["assistant_id"], "limit": 100})
        return "\n\n".join(vapi_import.transcript(c, "Agent") for c in calls)
    except Exception:  # noqa: BLE001 - best effort: the excerpt-only redaction still runs
        return ""


def redact(text: str, context: str = "") -> str:
    """demo-factory's redactor, with 911 protected (it reads "call 911" as an address) and names learned from context."""
    guarded = NINE11.sub("NINEONEONEX", (context + SEP if context else "") + text)
    out = _run(["-m", "demo_line.redact", "--keep-demo"], stdin=guarded)
    out = out.split("=====NIGHTSHIFT-REPORT-STARTS=====", 1)[-1].lstrip("\n") if context else out
    return out.replace("NINEONEONEX", "911")


def load(days: int, limit: int, report_file: Path | None = None) -> RealNight:
    raw = report_file.read_text() if report_file else _run(["-m", "demo_line.real_call_qa", "--days", str(days), "--stdout"])
    redacted = redact(raw, "" if report_file else full_transcripts(days))
    calls, flagged, counts = parse(raw)
    head = ("# Flagged REAL calls (not simulated)\n\nThese are real calls to the demo line, flagged by heuristics on "
            "the model's text, then redacted ([PHONE], [NAME], [ADDRESS]...). The calls may be older than the current "
            "prompt, so a flag can already be fixed: check prompt.md before changing anything.\n\n")
    return RealNight(head + redacted, calls, flagged, counts, twins(counts, limit))

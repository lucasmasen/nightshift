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
    ids: list[str] = None  # the calls reviewed (short ids), so later nights can skip them
    full: str = ""  # the redacted full calls ("## Call <id> ..."), for building their twin tests


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


def drop_calls(report: str, skip: set[str]) -> str:
    """The QA report without the sections for calls an earlier night already reviewed."""
    if not skip:
        return report
    parts = re.split(r"(?=^## .+? · call \w+ · \d+s)", report, flags=re.M)
    keep = [p for p in parts if not ((m := CALL.match(p)) and m.group(2)[:8] in skip)]
    return "".join(keep)


def seen_ids() -> set[str]:
    """Call ids already reviewed by earlier real-call nights."""
    out: set[str] = set()
    for d in config.RUNS.glob("20*/summary.json") if config.RUNS.exists() else []:
        try:
            out |= set(json.loads(d.read_text()).get("call_ids") or [])
        except ValueError:
            continue
    return out


def full_transcripts(days: int, skip: set[str] | None = None) -> str:
    """The window's whole calls, so the redactor learns every name a caller gave ("this is X") before it cleans the
    report's short excerpts, where a name can appear with nothing marking it as one. Read-only; empty if it can't."""
    try:
        from . import vapi_import
        state = json.loads((DF / "demo_line" / "state.json").read_text())
        env = dict(ln.split("=", 1) for ln in (DF / ".env").read_text().splitlines() if "=" in ln and not ln.startswith("#"))
        key = env.get("VOICE_PLATFORM_API_KEY", "").strip().strip('"').strip("'")
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days + 1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        calls = vapi_import._get("/call", key, {"assistantId": state["assistant_id"], "limit": 100,
                                                "createdAtGt": since})
        parts = [f"## Call {c.get('id', '')[:8]} ({c.get('createdAt', '')[:16]} UTC)\n\n"
                 + vapi_import.transcript(c, "Godfrey") for c in calls if c.get("id", "")[:8] not in (skip or set())]
        return "\n\n".join(p for p in parts if p.count("\n") >= 4)
    except Exception:  # noqa: BLE001 - best effort: the excerpt-only redaction still runs
        return ""


def redact_both(text: str, context: str = "") -> tuple[str, str]:
    """demo-factory's redactor over context + text in one pass, so names learned in the full calls ("this is X") are
    also removed from the report's excerpts. 911 is protected (the redactor reads "call 911" as an address).
    Returns (redacted text, redacted context)."""
    guarded = NINE11.sub("NINEONEONEX", (context + SEP if context else "") + text)
    out = _run(["-m", "demo_line.redact", "--keep-demo"], stdin=guarded).replace("NINEONEONEX", "911")
    if not context:
        return out, ""
    ctx, _, rest = out.partition("=====NIGHTSHIFT-REPORT-STARTS=====")
    return rest.lstrip("\n"), ctx.strip()


def redact(text: str, context: str = "") -> str:
    return redact_both(text, context)[0]


def load(days: int, limit: int, report_file: Path | None = None, skip_seen: bool = False) -> RealNight:
    raw = report_file.read_text() if report_file else _run(["-m", "demo_line.real_call_qa", "--days", str(days), "--stdout"])
    skip = seen_ids() if skip_seen else set()
    raw = drop_calls(raw, skip)
    redacted, full = redact_both(raw, "" if report_file else full_transcripts(days, skip))
    calls, flagged, counts = parse(raw)
    head = ("# REAL calls to the demo line (not simulated)\n\nFirst, flags from automatic checks on the agent's text. "
            "They're heuristics: some are wrong, and they miss things. Then the full calls: read each one yourself and "
            "judge what went wrong, including problems no flag caught (for example asking the same question over and "
            "over). Everything is redacted ([PHONE], [NAME], [ADDRESS]...). Calls may be older than the current prompt, "
            "so check prompt.md before changing anything.\n\n")
    tail = ("\n\n# Full calls (redacted)\n\n" + full) if full else ""
    ids = [m.group(2)[:8] for m in CALL.finditer(raw)]
    return RealNight(head + redacted + tail, calls, flagged, counts, twins(counts, limit), ids, full)

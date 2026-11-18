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
# The made-up demo shop. The redactor reads "this is Sunset Plumbing" as a person introducing themself and then
# hides "Sunset" everywhere, which makes the fixer's transcripts say "[NAME] Plumbing". It is not a real shop.
DEMO_SHOP = re.compile(r"\bSunset(?=\s+Plumbing\b)")
SHOP_HOLD = "the_demo_shop"  # starts with "the", which the redactor never reads as a name
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


LABELS = ("Caller", "Godfrey", "Agent", "Customer", "User", "Assistant")
ASKS_NAME = re.compile(r"\b(your name|who am i speaking|who's calling|who is calling|name for the)\b", re.I)
ADDRESSED = re.compile(r"\b(?i:thanks|thank you|sorry|got it|okay|ok|hi|hey|alright|perfect|great),\s+([A-Z][a-z'’-]{1,20})\b")
NOT_A_NAME = {"I", "I'm", "Im", "Yes", "Yeah", "No", "Nope", "Okay", "Ok", "Sure", "Man", "Bro", "Dude", "It", "It's",
              "Its", "The", "My", "Hey", "Hi", "Hello", "Um", "Uh", "Well", "So", "Just", "Sorry", "Thanks", "Mister",
              "Mr", "Mrs", "Ms", "Miss", "Sir", "Ma'am", "Godfrey", "Sunset", "Plumbing", "This", "That", "What", "Why",
              "Can", "Call", "Please", "Wait", "Look", "Like", "Not", "Nah", "Bye", "Goodbye", "Fine", "Good", "Oh",
              "PG", "E", "AI", "Who", "Me", "You", "We", "They", "He", "She", "Name", "Its"}


def learn_names(text: str) -> set[str]:
    """Names a caller gave with nothing marking them as a name: the answer right after the agent asks for a name
    ("Joe. John."), and names the agent then says back ("Thanks, John."). The redactor misses both."""
    names: set[str] = set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        who, _, said = line.partition(":")
        if who.strip() not in LABELS[1:] and who.strip() != "Godfrey":
            continue
        names |= {m for m in ADDRESSED.findall(said) if m not in NOT_A_NAME}
        if ASKS_NAME.search(said) and i + 1 < len(lines):
            nxt_who, _, nxt = lines[i + 1].partition(":")
            if nxt_who.strip() == "Caller":
                names |= {w for w in re.findall(r"\b[A-Z][a-z'’-]{1,20}\b", nxt) if w not in NOT_A_NAME}
    return names


def scrub_names(text: str, names: set[str]) -> str:
    for n in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(n)}\b", "[NAME]", text)
    return text


def _protect_labels(text: str) -> str:
    return re.sub(rf"^(\s*(?:- )?(?:\*\*)?)({'|'.join(LABELS)})(\*\*)?:", lambda m: f"{m.group(1)}LBL{m.group(2).upper()}X{m.group(3) or ''}:",
                  text, flags=re.M)


def _restore_labels(text: str) -> str:
    for lab in LABELS:
        text = text.replace(f"LBL{lab.upper()}X", lab)
    return text


def redact_both(text: str, context: str = "") -> tuple[str, str]:
    """demo-factory's redactor over context + text in one pass, so names learned in the full calls ("this is X") are
    also removed from the report's excerpts. 911 is protected (the redactor reads "call 911" as an address).
    Returns (redacted text, redacted context)."""
    names = learn_names(context) | learn_names(text)
    joined = (context + SEP if context else "") + text
    guarded = _protect_labels(DEMO_SHOP.sub(SHOP_HOLD, NINE11.sub("NINEONEONEX", joined)))
    out = _run(["-m", "demo_line.redact", "--keep-demo"], stdin=guarded)
    # Learned names are scrubbed after the redactor, never before: "my name is [NAME] Whitfield" no longer looks
    # like an intro to it, so scrubbing first let surnames through.
    out = _restore_labels(scrub_names(out, names)).replace("NINEONEONEX", "911").replace(SHOP_HOLD, "Sunset")
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

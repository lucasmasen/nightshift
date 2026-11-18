"""Step 2: the overnight fix. A HarnessRouter agent (Claude Code harness) reads the failed calls and the prompt,
edits prompt.md, and explains each change. --mock does the same shape of work locally, for testing without a key.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


from . import config, cost, lessons
from .hr import Client, final_text

SYSTEM_PROMPT = """You are Nightshift. Each night you review an AI phone receptionist's calls and fix its instructions.

You get three files in your working directory:
- prompt.md: the receptionist's instructions (its system prompt).
- failures.md: simulated calls that failed, each with the grader's reason and the full transcript.
- shop.json: the shop's facts (services, hours, prices). These are true; never contradict or add to them.

Your job:
1. For each failure, find the instruction in prompt.md that caused it, or the missing instruction.
2. Group failures that share a cause. Fix causes, not individual calls.
3. Edit prompt.md in place with the smallest change that fixes them: reword or add a short rule where it belongs.
   Do not rewrite sections, reorder the file, or change its style.
4. Write nightshift-report.md: for each cause, the failed cases, the quoted bad moment, the edit you made, and
   why it should not break calls that already pass.

Hard rules (a change that breaks one is thrown away):
- Never change or remove a line about the AI assistant disclosure or call recording, in any language.
- Keep every $placeholder (like $agent_name) exactly as written.
- Never invent shop facts, prices, hours, promises or phone numbers.
- Change at most 30 lines in total.
- If a failure is the grader's mistake, or not caused by the instructions, say so in the report and leave it.

End your final reply with one line of JSON: {"changed": true|false, "causes": <number>, "summary": "<one sentence>"}"""

TASK = ("Tonight's failed calls are in failures.md. Fix prompt.md following your instructions, then write "
        "nightshift-report.md. Edit prompt.md in place; do not create a copy with another name.")


@dataclass
class Fix:
    prompt: str  # the proposed prompt.md
    report: str  # nightshift-report.md
    summary: dict
    trace: dict  # where it ran: harness id, response id, session id, model, spend


def parse_summary(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        line = line.strip().strip("`")
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {"changed": None, "summary": text.strip()[-300:]}


PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
FICTIONAL = re.compile(r"555[\s.-]?01\d\d")  # 555-0100..0199 is reserved for fiction (the test calls use it)


def outgoing_check(path: Path, log=print) -> Path:
    """Last check before a file leaves this Mac: call transcripts must hold no learnable name and no real-looking
    phone number. Anything found is scrubbed into a copy that is sent instead, and the log says so."""
    if path.name not in ("failures.md", "calls.md"):  # transcripts only; never rewrite the agent's own instructions
        return path
    from .realcalls import learn_names, scrub_names
    text = path.read_text()
    names = learn_names(text)
    phones = [m.group(0) for m in PHONE.finditer(text) if not FICTIONAL.search(m.group(0))]
    if not names and not phones:
        return path
    clean = scrub_names(text, names)
    for ph in phones:
        clean = clean.replace(ph, "[PHONE]")
    safe = path.with_name(path.stem + ".sent" + path.suffix)
    safe.write_text(clean)
    log(f"  privacy check: scrubbed {len(names)} name(s) and {len(phones)} number(s) from {path.name} before upload")
    return safe


def run_harnessrouter(client: Client, prompt: Path, failures: Path, shop: Path, log=print) -> Fix:
    files = [(prompt, "prompt.md"), (failures, "failures.md"), (shop, "shop.json")]
    task = TASK
    if lessons.LESSONS.exists():
        files.append((lessons.LESSONS, "lessons.md"))
        task += (" lessons.md lists earlier fixes that were rejected and what they broke: read it first and don't "
                 "repeat those changes.")
    return run_on_harnessrouter(client, SYSTEM_PROMPT, task, files, log=log)


def run_on_harnessrouter(client: Client, system_prompt: str, task: str, files: list[tuple[Path, str]],
                         log=print, harness_name: str | None = None) -> Fix:
    """Upload the files, run one task on a Nightshift harness, and read back prompt.md + nightshift-report.md.
    Each system prompt gets its own harness (a harness's instructions are fixed when it's created)."""
    log("HarnessRouter: checking the key and the Nightshift harness...")
    me = client.me()
    log(f"  workspace: {me.get('workspace_name') or me.get('workspace')}")
    if harness_name is None and system_prompt != SYSTEM_PROMPT:
        harness_name = config.HARNESS_NAME + " [any agent]"
    harness_id = client.ensure_harness(system_prompt, harness_name)
    log(f"  harness: {harness_id}")
    files = [(outgoing_check(path, log), name) for path, name in files]
    ids = [client.upload(path, name) for path, name in files]
    log("  uploaded " + ", ".join(name for _, name in files))
    started = client.start(harness_id, task, ids)
    rid = started["id"]
    log(f"  task started: {rid} (this takes a few minutes)")
    done = client.wait(rid, on_tick=lambda r, s: log(f"  ...working ({s:.0f}s)") if s and s % 60 == 0 else None)
    if done.get("status") != "completed":
        raise RuntimeError(f"HarnessRouter task ended {done.get('status')}: {done.get('error') or done.get('incomplete_details')}")
    session = (done.get("metadata") or {}).get("session_id", "")
    files = {f.get("path") or f.get("filename"): f for f in client.changed_files(session)} if session else {}
    for o in done.get("output", []):  # annotations on the final message also name the files
        for c in o.get("content", []) if o.get("type") == "message" else []:
            for a in c.get("annotations", []):
                if a.get("type") == "container_file_citation":
                    files.setdefault(a.get("filename"), a)

    def get(name: str) -> str:
        f = files.get(name)
        return client.download(f["download_url"]).decode("utf-8") if f else ""

    new_prompt = get("prompt.md")
    report = get("nightshift-report.md")
    text = final_text(done)
    trace = {"where": "HarnessRouter", "harness_id": harness_id, "response_id": rid, "session_id": session,
             "model": done.get("served_model") or done.get("model"), "usage": done.get("usage")}
    trace["est_cost_usd"] = cost.fix_cost(done.get("usage"), trace.get("model"))
    if session:
        try:
            s = client.request("GET", f"/v1/sessions/{session}")
            trace["spend_usd"] = float(s.get("spend_usd") or 0)
            trace["elapsed_s"] = s.get("elapsed")
        except Exception:  # noqa: BLE001 - spend is nice to show, not required
            pass
    return Fix(prompt=new_prompt, report=report or text, summary=parse_summary(text), trace=trace)


# A stand-in for the HarnessRouter agent so the whole loop can be tested with no key and no spend. It makes one
# small, real edit aimed at the most common failure reason, and says plainly that it is the mock.
MOCK_RULES = [
    (re.compile(r"two questions in one turn", re.I),
     "Ask one question per turn. If you need two things, ask for the first, wait for the answer, then ask the second."),
    (re.compile(r"call you back tonight|let the \w+ know right away", re.I),
     "Never promise a callback or say you've told someone unless a tool just confirmed it. Say what will really happen."),
    (re.compile(r"greet|good morning", re.I),
     "Never greet the caller again after your opening line."),
]


def run_mock(prompt: Path, failures: Path, shop: Path, log=print) -> Fix:
    log("MOCK fixer (no HarnessRouter call, no spend).")
    old = prompt.read_text()
    reasons = failures.read_text()
    rule = next((r for pat, r in MOCK_RULES if pat.search(reasons)), None)
    if not rule:
        return Fix(old, "MOCK: no known pattern in tonight's failures; nothing changed.",
                   {"changed": False, "causes": 0, "summary": "No change."}, {"where": "mock"})
    lines = old.splitlines()
    # Put the rule after the first paragraph of rules (after line 3 in Godfrey's prompt), not at the very end.
    at = min(len(lines), 4)
    new = "\n".join(lines[:at] + [rule] + lines[at:]) + ("\n" if old.endswith("\n") else "")
    report = f"# Nightshift report (MOCK)\n\n## Cause 1\nFailures share one reason. Added one rule:\n\n> {rule}\n"
    return Fix(new, report, {"changed": True, "causes": 1, "summary": "MOCK: added one rule."}, {"where": "mock"})

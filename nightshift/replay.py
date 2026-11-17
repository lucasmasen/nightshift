"""Replay proof for other agents (no simulator): did the new instructions stop the bad moment from coming back?

From the agent's report, take each call it says went wrong and the quoted bad moment. Cut the transcript just before
that moment, then ask the model (playing the agent) for its next line, a few times with the old instructions and a
few with the new, and have a separate grading call say whether each line repeats the mistake.

Honest limit, printed on every result: this is an AI grading AI replies to a single moment, not a full test suite,
so it's weaker evidence than Godfrey's simulated calls. It runs on demo-factory's Anthropic key (~$0.20 per call).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import config, stats

LIMIT = ("AI-graded replay of the moment each call went wrong. Weaker evidence than a full test suite: "
         "it checks the same moment again, not whole new calls.")
WORKER = Path(__file__).with_name("replay_worker.py")
SECTION = re.compile(r"^#{2,3} Call (\S+)\s*$", re.M)
BAD = re.compile(r"Bad moment:\s*[\"“](.+?)[\"”]", re.S)


def cases_from_report(report: str, calls: dict[str, str]) -> list[dict]:
    """Calls the agent said went wrong: {id, context (transcript before the bad line), bad, why}."""
    wrong = report.split("## Calls judged fine")[0]
    parts = SECTION.split(wrong)
    out = []
    for cid, body in zip(parts[1::2], parts[2::2]):
        body = body.split("\n## ")[0]
        m = BAD.search(body)
        transcript = calls.get(cid, "")
        if not m or not transcript:
            continue
        bad = m.group(1).strip()
        lines = transcript.splitlines()
        probe = bad[:40].lower()
        at = next((i for i, ln in enumerate(lines) if probe and probe in ln.lower()), None)
        if at is None or at == 0:
            continue
        why = re.sub(r"\s+", " ", body.replace(m.group(0), "")).strip()[:700]
        out.append({"id": cid, "context": "\n".join(lines[:at]).strip(), "bad": bad, "why": why})
    return out


DEFAULT_AGENT_MODEL = "claude-haiku-5-5"  # what most voice agents run on (Godfrey too); override with model.txt


def run(old: str, new: str, cases: list[dict], samples: int = 3, log=print,
        agent_model: str = DEFAULT_AGENT_MODEL) -> dict:
    if not cases:
        return {"cases": [], "verdict": "No replay: the report quoted no bad moment it could find in a transcript."}
    log(f"   replaying {len(cases)} bad moment(s), {samples}x old and {samples}x new instructions...")
    p = subprocess.run([str(config.PYTHON), str(WORKER)], input=json.dumps(
        {"old": old, "new": new, "samples": samples, "cases": cases,
         "env_file": str(config.TEST_CHECKOUT / ".env"), "agent_model": agent_model}), capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError("replay failed: " + p.stderr.strip().splitlines()[-1][:300] if p.stderr.strip() else "replay failed")
    res = json.loads(p.stdout)
    ob = sum(c["old_bad"] for c in res["cases"])
    nb = sum(c["new_bad"] for c in res["cases"])
    n = sum(c["samples"] for c in res["cases"])
    luck = stats.fisher_improved(n - ob, n, n - nb, n) if n else 1.0
    res.update(old_bad=ob, new_bad=nb, total=n, p_value=round(luck, 3), limit=LIMIT)
    u = res.get("usage") or {}
    res["est_cost_usd"] = round(u.get("input_tokens", 0) * 4e-6 + u.get("output_tokens", 0) * 20e-6, 3)
    if ob == 0:
        res["verdict"] = (f"Replay can't tell: on {res.get('agent_model')}, the mistake didn't come back even with the "
                          f"old instructions (0/{n}), so there's nothing for the fix to beat.")
    elif nb < ob and luck < 0.2:
        res["verdict"] = (f"Replay: the mistake came back {ob}/{n} times with the old instructions, {nb}/{n} with the "
                          f"new (chance that's luck: {luck:.0%}).")
    elif nb < ob:
        res["verdict"] = f"Replay: better ({ob}/{n} to {nb}/{n} repeats), but could be luck ({luck:.0%})."
    else:
        res["verdict"] = f"Replay: not better ({ob}/{n} to {nb}/{n} repeats of the mistake). Don't approve."
    return res

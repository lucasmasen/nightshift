"""Replay worker: runs under demo-factory's venv (it has the official anthropic SDK; nightshift itself is stdlib only).

stdin: {"old": prompt, "new": prompt, "samples": 3, "cases": [{"id", "context", "bad", "why"}]}
stdout: {"cases": [{"id", "old_bad", "new_bad", "samples", "old_replies", "new_replies"}], "usage": {...}}

For each case and each prompt, the agent is asked for its next line given the call so far (up to just before the bad
moment), `samples` times. A separate grading call asks whether that line repeats the mistake. Both use the current
default model at low effort; refusals fall back server-side.
"""
from __future__ import annotations

import json
import sys

import anthropic

GRADER = "claude-opus-5-5"  # grades every reply; refusals fall back server-side
OPUS_EXTRA = {"betas": ["server-side-fallback-2026-07-01"], "extra_body": {"fallbacks": "default",
                                                                           "output_config": {"effort": "low"}}}
usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}


def ask(client: anthropic.Anthropic, system: str, user: str, max_tokens: int = 2000, model: str = GRADER) -> str:
    if model == GRADER:
        r = client.beta.messages.create(model=model, max_tokens=max_tokens, system=system,
                                        messages=[{"role": "user", "content": user}], **OPUS_EXTRA)
    else:  # the agent's own model, called the way a voice agent would be: plain request, low effort
        r = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                   messages=[{"role": "user", "content": user}],
                                   extra_body={"output_config": {"effort": "low"}})
    usage["input_tokens"] += r.usage.input_tokens
    usage["output_tokens"] += r.usage.output_tokens
    usage["calls"] += 1
    if r.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in r.content if b.type == "text").strip()


def next_line(client: anthropic.Anthropic, prompt: str, context: str, model: str) -> str:
    return ask(client, prompt, "This is a live phone call. The transcript so far:\n\n" + context +
               "\n\nYou are the agent. Reply with only your next spoken line (and any tool call you would make, "
               "written as [tool_name: what you'd send]). No commentary.", model=model)


def repeats_mistake(client: anthropic.Anthropic, case: dict, reply: str) -> bool:
    verdict = ask(client, "You grade one line from an AI phone agent. Be strict and literal.",
                  f"In an earlier version of this call the agent said:\n\"{case['bad']}\"\n\nWhy that was wrong:\n"
                  f"{case['why']}\n\nA new version of the agent, at the same moment, said:\n\"{reply}\"\n\nDoes the new "
                  "line make the same mistake (even reworded)? Answer YES or NO on the first line, then one sentence.",
                  max_tokens=1000)
    return verdict.upper().startswith("YES")


def main() -> None:
    job = json.loads(sys.stdin.read())
    # Same key source as the simulator: the test checkout's .env, read the way its config.py reads it.
    from dotenv import dotenv_values
    key = (dotenv_values(job["env_file"]) or {}).get("ANTHROPIC_API_KEY") if job.get("env_file") else None
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    out = []
    for case in job["cases"]:
        row = {"id": case["id"], "samples": job["samples"], "old_bad": 0, "new_bad": 0,
               "old_replies": [], "new_replies": []}
        for which in ("old", "new"):
            for _ in range(job["samples"]):
                reply = next_line(client, job[which], case["context"], job.get("agent_model", "claude-haiku-5-5"))
                row[f"{which}_replies"].append(reply)
                if not reply or repeats_mistake(client, case, reply):
                    row[f"{which}_bad"] += 1
        out.append(row)
    print(json.dumps({"cases": out, "usage": usage, "grader": GRADER, "agent_model": job.get("agent_model", "claude-haiku-5-5")}))


if __name__ == "__main__":
    main()

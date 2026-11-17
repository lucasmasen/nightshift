"""Import a Vapi assistant's instructions and recent calls into a folder `nightshift agent` can run on.

    python3 -m nightshift import-vapi ASSISTANT_ID --days 7 --out tries/acme
    python3 -m nightshift agent tries/acme

Read-only (two GETs). The builder's Vapi private key goes in nightshift/.env as VAPI_IMPORT_KEY (they paste it
themselves; it's never printed or stored anywhere else). Transcripts are redacted with demo-factory's redact tool
(names, phone numbers, addresses, emails) before they're written, so nothing raw lands on disk or reaches the fixer.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

API = "https://api.vapi.ai"


def _get(path: str, key: str, params: dict | None = None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "nightshift-import"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - fixed https host
        return json.loads(r.read().decode())


def transcript(call: dict, agent: str = "Agent") -> str:
    """One readable transcript: who said what, plus tool calls and their results."""
    lines = []
    for m in (call.get("artifact") or {}).get("messages") or call.get("messages") or []:
        role, text = m.get("role"), (m.get("message") or "").strip()
        if role in ("bot", "assistant") and text:
            lines.append(f"{agent}: {text}")
        elif role == "user" and text:
            lines.append(f"Caller: {text}")
        elif role == "tool_calls":
            for tc in m.get("toolCalls") or []:
                fn = tc.get("function") or {}
                lines.append(f"[{fn.get('name')}: {fn.get('arguments')}]")
        elif role in ("tool_call_result", "tool_calls_result"):
            lines.append(f"[tool result: {str(m.get('result') or m.get('content') or '')[:300]}]")
    return "\n".join(lines)


def system_prompt(assistant: dict) -> str:
    msgs = (assistant.get("model") or {}).get("messages") or []
    return "\n\n".join(m.get("content", "") for m in msgs if m.get("role") == "system").strip()


def redact(text: str) -> str:
    from .realcalls import NINE11
    py = config.WORKSPACE / "demo-factory" / ".venv" / "bin" / "python"
    p = subprocess.run([str(py), "-m", "demo_line.redact"], cwd=config.WORKSPACE / "demo-factory",
                       input=NINE11.sub("NINEONEONEX", text), capture_output=True, text=True, timeout=300)
    if p.returncode not in (0, 1) or not p.stdout.strip():
        raise RuntimeError("redaction failed; not writing unredacted calls")
    return p.stdout.replace("NINEONEONEX", "911")


def run(assistant_id: str, out: Path, days: int = 7, limit: int = 50, key: str | None = None, log=print) -> Path:
    config.load_env()
    key = key or os.environ.get("VAPI_IMPORT_KEY", "").strip()
    if not key:
        raise ValueError("No VAPI_IMPORT_KEY in nightshift/.env (the builder's Vapi private key).")
    assistant = _get(f"/assistant/{urllib.parse.quote(assistant_id)}", key)
    days = min(days, 13)  # Vapi's basic plans keep 14 days of call history; asking further back is a 400
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    calls = _get("/call", key, {"assistantId": assistant_id, "createdAtGt": since, "limit": limit})
    prompt = system_prompt(assistant)
    if not prompt:
        raise ValueError("That assistant has no system prompt to fix.")
    name = assistant.get("name") or "agent"
    texts = [(c.get("id", "")[:8], transcript(c, name)) for c in calls]
    texts = [(i, t) for i, t in texts if t.count("\n") >= 2]  # skip empty / instant hang-ups
    if not texts:
        raise ValueError(f"No calls with a conversation in the last {days} days.")
    sep = "\n\n=====CALL=====\n\n"
    cleaned = redact(sep.join(t for _, t in texts)).split("=====CALL=====")
    (out / "calls").mkdir(parents=True, exist_ok=True)
    (out / "prompt.md").write_text(prompt + "\n")
    (out / "name.txt").write_text(f"{name} (Vapi)")
    model = (assistant.get("model") or {}).get("model")
    if model and str(model).startswith("claude-"):
        (out / "model.txt").write_text(str(model))
    for (cid, _), text in zip(texts, cleaned):
        (out / "calls" / f"{cid}.txt").write_text(text.strip() + "\n")
    log(f"Imported {name}: instructions + {len(texts)} calls from the last {days} days -> {out} (redacted)")
    return out

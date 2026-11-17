"""Other people's voice agents, reviewed every night next to Godfrey.

    python3 -m nightshift register-agent acme --vapi <assistant id> [--key-env VAPI_IMPORT_KEY] [--model claude-haiku-5-5]
    python3 -m nightshift register-agent clinic --folder path/to/agent-folder

Each registered agent is a small JSON file in agents/ (git-ignored). Every night, for each one: pull the last day's
calls (Vapi, read-only, redacted) or read its folder, then run `nightshift agent` on them: the HarnessRouter agent
finds the bad calls, fixes the instructions, the safety check runs, and the bad moments are replayed. The builder's
key stays in .env under the name the entry gives; it's never written into the entry.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import config

DIR = config.PROJECT / "agents"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def register(name: str, vapi: str | None = None, folder: str | None = None, key_env: str = "VAPI_IMPORT_KEY",
             model: str | None = None) -> Path:
    if not NAME.match(name):
        raise ValueError("Name: lowercase letters, digits and dashes.")
    if bool(vapi) == bool(folder):
        raise ValueError("Give exactly one of --vapi or --folder.")
    if key_env and not re.match(r"^[A-Z][A-Z0-9_]{2,40}$", key_env):
        raise ValueError("--key-env is the NAME of a variable in .env, like VAPI_IMPORT_KEY, never the key itself.")
    DIR.mkdir(exist_ok=True)
    entry = {"vapi_assistant": vapi, "key_env": key_env} if vapi else {"folder": str(Path(folder).resolve())}
    if model:
        entry["model"] = model
    path = DIR / f"{name}.json"
    path.write_text(json.dumps(entry, indent=2) + "\n")
    return path


def registered() -> dict[str, dict]:
    if not DIR.exists():
        return {}
    out = {}
    for f in sorted(DIR.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text())
        except ValueError:
            continue
    return out


def prepare(name: str, entry: dict, log=print) -> Path | None:
    """The folder `nightshift agent` should run on tonight, or None if there's nothing new."""
    if entry.get("folder"):
        folder = Path(entry["folder"])
        newest = max((f.stat().st_mtime for f in folder.rglob("*") if f.is_file()), default=0)
        stamp = DIR / f".{name}.last"
        if stamp.exists() and newest <= float(stamp.read_text() or 0):
            log(f"{name}: no new calls in its folder since the last night")
            return None
        stamp.write_text(str(newest))
        return folder
    import os
    from . import vapi_import
    config.load_env()
    key = os.environ.get(entry.get("key_env") or "VAPI_IMPORT_KEY", "").strip()
    if not key:
        log(f"{name}: no key in .env under {entry.get('key_env')}; skipped")
        return None
    out = config.PROJECT / "tries" / f"{name}-{datetime.now():%Y-%m-%d}"
    try:
        vapi_import.run(entry["vapi_assistant"], out, days=1, key=key, log=log)
    except ValueError as e:  # no calls with a conversation since yesterday, or no prompt
        log(f"{name}: {e}")
        return None
    if entry.get("model"):
        (out / "model.txt").write_text(entry["model"])
    return out

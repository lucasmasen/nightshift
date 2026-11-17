"""Paths and settings. Nothing secret lives here: the HarnessRouter key is read from nightshift/.env."""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
WORKSPACE = PROJECT.parent  # "AI Infrastructure Claude"
RUNS = PROJECT / "runs"  # git-ignored: one folder per night

# The checkout Nightshift tests in. It's a git worktree of demo-factory on its own branch, so the real prompt.md
# in demo-factory/ is never edited. Its name starts with "demo-factory" on purpose: suite.py counts every
# demo-factory* checkout toward the shared $2/day simulator cap.
TEST_CHECKOUT = Path(os.environ.get("NIGHTSHIFT_CHECKOUT", WORKSPACE / "demo-factory-nightshift"))
PYTHON = Path(os.environ.get("NIGHTSHIFT_PYTHON", WORKSPACE / "demo-factory" / ".venv" / "bin" / "python"))
PROMPT_REL = Path("demo_line/prompt.md")
SHOP_REL = Path("demo_line/sunset.json")

API_BASE = "https://api.harnessrouter.ai"
UHP_VERSION = "2026-09-28"
# Which agent harness does the fixing. claude-code by default; codex if the model key is OpenAI-only
# (set NIGHTSHIFT_BASE=codex in nightshift/.env). Each base gets its own harness, named after it.
HARNESS_BASE = os.environ.get("NIGHTSHIFT_BASE", "claude-code")
HARNESS_NAME = f"Nightshift (voice agent fixer, {HARNESS_BASE})"
PREFERRED_MODELS = ["claude-sonnet-5.5", "claude-opus-5.5", "gpt-5.5", "gpt-5.4"]
TASK_MAX_COST_USD = 1.50  # per overnight fix task, on HarnessRouter's side
TASK_TIMEOUT_SECONDS = 1500
USER_ID = "nightshift-demo"  # HarnessRouter's per-person id; one operator for the demo

MAX_CHANGED_LINES = 30  # a fix bigger than this is rejected by the guard


def load_env(path: Path = PROJECT / ".env") -> None:
    """Minimal .env reader (KEY=value lines). Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_key() -> str:
    load_env()
    return os.environ.get("HR_API_KEY", "").strip()


# Read nightshift/.env once at import so settings like NIGHTSHIFT_BASE apply.
load_env()
HARNESS_BASE = os.environ.get("NIGHTSHIFT_BASE", "claude-code")
HARNESS_NAME = f"Nightshift (voice agent fixer, {HARNESS_BASE})"

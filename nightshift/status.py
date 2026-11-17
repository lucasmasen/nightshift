"""One look at everything: what's waiting on Lucas, today's test budget, new real calls, the next runs.

    python3 -m nightshift status            # add --calls to also check the demo line for new calls (read-only)
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import agents, config, dashboard


def sim_budget() -> tuple[int, float, float] | None:
    """(paid calls today, $ spent today, $ cap) across every demo-factory checkout, read the way suite.py reads it."""
    code = ("import sys; sys.path.insert(0, '.'); from pathlib import Path; from demo_line import suite_cost as c; "
            "n, s = c.spent_today(c.spend_history(Path('demo_line/bench_history.csv'))); print(n, s, c.DAILY_CAP)")
    try:
        p = subprocess.run([str(config.PYTHON), "-c", code], cwd=config.TEST_CHECKOUT, capture_output=True,
                           text=True, timeout=60)
        n, s, cap = p.stdout.split()
        return int(n), float(s), float(cap)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def next_at(hour: int, minute: int = 0, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def waiting(nights) -> list[tuple[Path, dict]]:
    """Nights whose verdict asks for a person and nobody has approved or rejected yet."""
    return [(d, s) for d, s in nights if str(s.get("verdict", "")).startswith(("Ready", "Proposed", "Fix proposed"))
            and not s.get("approved") and not s.get("rejected") and (s.get("trace") or {}).get("where") != "mock"]


def run(check_calls: bool = False, log=print) -> int:
    nights = dashboard.nights()
    w = waiting(nights)
    log("Waiting on you:" if w else "Waiting on you: nothing.")
    for d, s in w:
        log(f"  {d.name}  {str(s.get('verdict'))[:110]}")
        log(f"     open http://127.0.0.1:8765/night/{d.name}")
    b = sim_budget()
    if b:
        n, spent, cap = b
        log(f"Test budget today: ${spent:.2f} of ${cap:.2f} used ({n} simulated calls); resets at midnight PT.")
    if check_calls:
        from . import realcalls
        r = realcalls.load(1, 3, skip_seen=True)
        log(f"New real calls to the demo line since the last review: {r.calls} ({r.flagged_calls} flagged).")
    plist = Path.home() / "Library" / "LaunchAgents" / "ai.nightshift.night.plist"
    log(f"Next nightly run: {next_at(2):%a %-I:%M%p}" + ("" if plist.exists() else " (NOT scheduled)"))
    if (config.PROJECT / "wednesday.sh").exists() and datetime.now() < datetime(2026, 11, 18, 0, 10):
        log("Also queued: wednesday.sh at Wed 12:10AM (Codex half, re-check, real-call proof).")
    others = agents.registered()
    log(f"Other agents watched nightly: {', '.join(others) if others else 'none yet (register-agent)'}")
    approved = [d.name for d, s in nights if s.get("approved")]
    log(f"Approved, not live: {', '.join(approved) or 'none'}. Nothing goes live without golive --really.")
    return 0

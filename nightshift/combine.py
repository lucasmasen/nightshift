"""Combine several waiting fixes into one change, so they can be proven and approved together.

    python3 -m nightshift combine runs/A runs/B runs/C

Every fix that started from the same instructions is merged line by line (git merge-file, a three-way merge). If two
fixes change the same lines differently, it stops and says which: a person picks. The merged change goes through the
same safety check, and becomes a new night whose proof replays every input night's tests together. Nothing goes live.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from . import config, dashboard, guard


class Conflict(RuntimeError):
    pass


def new_night_dir() -> Path:
    """A fresh runs/<date-time> folder; if that second is taken, the next free second (names stay sortable)."""
    from datetime import timedelta
    t = datetime.now()
    while (config.RUNS / t.strftime("%Y-%m-%d-%H%M%S")).exists():
        t += timedelta(seconds=1)
    return config.RUNS / t.strftime("%Y-%m-%d-%H%M%S")


def merge(base: str, fixes: list[str]) -> str:
    merged = fixes[0]
    with tempfile.TemporaryDirectory() as d:
        for i, other in enumerate(fixes[1:], 2):
            a, o, b = Path(d) / "ours", Path(d) / "base", Path(d) / "theirs"
            a.write_text(merged)
            o.write_text(base)
            b.write_text(other)
            p = subprocess.run(["git", "merge-file", "-p", str(a), str(o), str(b)], capture_output=True, text=True)
            if p.returncode != 0:
                raise Conflict(f"fix #{i} changes the same lines as an earlier one in a different way "
                               f"({p.returncode} conflict(s)); combine fewer, or pick one")
            merged = p.stdout
    return merged


def restack(night: Path, onto: Path, client, log=print) -> Path:
    """Redo one night's fix on top of another night's fixed instructions (when the two conflict): the HarnessRouter
    agent gets the same calls and the newer instructions, and makes the fix again from there."""
    from . import fixer
    s = dashboard.load(night)
    base_dir = onto
    base = (base_dir / "prompt.proposed.md").read_text()
    out = new_night_dir()
    out.mkdir(parents=True)
    (out / "prompt.before.md").write_text(base)
    (out / "failures.md").write_text((night / "failures.md").read_text())
    shop = config.TEST_CHECKOUT / config.SHOP_REL
    fix = fixer.run_harnessrouter(client, out / "prompt.before.md", out / "failures.md", shop, log=log)
    (out / "prompt.proposed.md").write_text(fix.prompt)
    (out / "nightshift-report.md").write_text(fix.report)
    diff = guard.diff_text(base, fix.prompt)
    (out / "change.diff").write_text(diff)
    problems = guard.check(base, fix.prompt)
    summary = {k: s.get(k) for k in ("agent", "mode", "baseline_total", "baseline_passed", "baseline_file", "call_ids",
                                     "twin_tests", "failed_cases", "retest_cases", "flag_counts")}
    summary.update(date=out.name, builds_on=onto.name, restacked_from=night.name, diff=diff, guard_problems=problems,
                   agent_report=fix.report, agent_summary=fix.summary, trace=fix.trace,
                   verdict=("Fix rejected by the safety check." if problems else
                            f"Redone on top of {onto.name}. Not re-tested yet: the proof runs on the next test budget."))
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    if not problems:
        s["superseded_by"] = out.name
        dashboard.save(night, s)
    log(f"Restacked {night.name} onto {onto.name} -> {out.name}: " + ("REJECTED" if problems else "safety check passed"))
    return out


def run(nights: list[Path], log=print) -> Path:
    if len(nights) < 2:
        raise ValueError("Give two or more nights to combine.")
    summaries = [dashboard.load(n) for n in nights]
    bases = {(n / "prompt.before.md").read_text() for n in nights}
    if len(bases) != 1:
        raise ValueError("These fixes started from different instructions; combine only fixes from the same version.")
    base = bases.pop()
    for n, s in zip(nights, summaries):
        if s.get("guard_problems") or not s.get("diff"):
            raise ValueError(f"{n.name} has no fix that passed the safety check.")
    fixes = [(n / "prompt.proposed.md").read_text() for n in nights]
    merged = merge(base, fixes)
    problems = guard.check(base, merged)
    out = new_night_dir()
    out.mkdir(parents=True)
    (out / "prompt.before.md").write_text(base)
    (out / "prompt.proposed.md").write_text(merged)
    diff = guard.diff_text(base, merged)
    (out / "change.diff").write_text(diff)
    cases: list[str] = []
    for s in summaries:
        for c in s.get("retest_cases") or s.get("failed_cases") or []:
            if c not in cases:
                cases.append(c)
    twins = [c for c in cases if c.startswith("real_")]
    cases = twins + [c for c in cases if not c.startswith("real_")][:6]  # every real-call test, a few guards
    summary = {
        "date": out.name, "agent": "Godfrey (Sunset Plumbing demo line), combined fixes", "mode": "real",
        "combined_from": [n.name for n in nights], "baseline_total": sum(s.get("baseline_total", 0) for s in summaries),
        "baseline_passed": sum(s.get("baseline_passed", 0) for s in summaries), "baseline_file": "real calls",
        "call_ids": sorted({i for s in summaries for i in (s.get("call_ids") or [])}),
        "twin_tests": twins, "failed_cases": twins, "retest_cases": cases, "diff": diff, "guard_problems": problems,
        "agent_summary": {"changed": True, "causes": sum((s.get("agent_summary") or {}).get("causes", 0) for s in summaries),
                          "summary": " ".join((s.get("agent_summary") or {}).get("summary", "") for s in summaries)},
        "agent_report": "\n\n---\n\n".join(f"# From {n.name}\n\n{s.get('agent_report', '')}" for n, s in zip(nights, summaries)),
        "trace": {"where": "combined", "from": [n.name for n in nights]},
        "verdict": ("Combined fix rejected by the safety check." if problems else
                    f"Combined {len(nights)} fixes ({guard.changed_lines(base, merged)} lines). Not re-tested yet: the "
                    "proof runs on the next test budget."),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    if not problems:  # the parts are covered by the whole: don't prove (or pay for) them separately
        for n, sm in zip(nights, summaries):
            sm["superseded_by"] = out.name
            dashboard.save(n, sm)
    log(f"Combined {', '.join(n.name for n in nights)} -> {out.name}: "
        + ("REJECTED: " + " | ".join(problems) if problems else f"{len(cases)} cases queued for the proof"))
    return out

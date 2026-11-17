"""Approval and the all-nights dashboard.

approve: the human gate. It never edits the live agent. It saves the approved prompt to approved/<night>.prompt.md,
marks the night approved, and prints the two commands that would put it live (which a person runs).
dashboard: runs/index.html, one row per night plus a score trend.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from . import config, report

APPROVED = config.PROJECT / "approved"


def load(night_dir: Path) -> dict:
    return json.loads((night_dir / "summary.json").read_text())


def save(night_dir: Path, summary: dict) -> None:
    (night_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (night_dir / "report.html").write_text(report.render(summary))


def approve(night_dir: Path, by: str, force: bool = False) -> tuple[bool, str]:
    s = load(night_dir)
    if s.get("guard_problems"):
        return False, "This fix failed the safety check; it can't be approved."
    if not (night_dir / "prompt.proposed.md").exists():
        return False, "No proposed prompt in this night."
    real = config.WORKSPACE / "demo-factory" / config.PROMPT_REL
    root = night_dir  # a stacked night's chain must start from the current real prompt
    for _ in range(20):
        parent = load(root).get("builds_on")
        if not parent:
            break
        root = night_dir.parent / parent
    before = root / "prompt.before.md"
    if real.exists() and before.exists() and real.read_text() != before.read_text():
        return False, ("The real prompt changed since this night ran, so this fix was made against an old version. "
                       "Run a new night instead of approving this one.")
    ready = str(s.get("verdict", "")).startswith("Ready for approval")
    if not ready and not force:
        return False, "Not marked ready (the re-test didn't show a clean improvement). Use --force to approve anyway."
    APPROVED.mkdir(exist_ok=True)
    out = APPROVED / f"{night_dir.name}.prompt.md"
    out.write_text((night_dir / "prompt.proposed.md").read_text())
    s["approved"] = {"by": by, "at": datetime.now().isoformat(timespec="seconds"), "file": str(out), "forced": force and not ready}
    save(night_dir, s)
    steps = (f"Approved and saved to {out}.\nTo put it live (a person does this, after reading the diff):\n"
             f"  cp '{out}' '{config.WORKSPACE / 'demo-factory' / config.PROMPT_REL}'\n"
             f"  cd '{config.WORKSPACE / 'demo-factory'}' && ./.venv/bin/python demo_line/setup.py")
    return True, steps


def reject(night_dir: Path, by: str, why: str) -> tuple[bool, str]:
    """The human 'no'. Marks the night rejected and writes the reason into lessons.md for the next night's agent."""
    from . import lessons
    s = load(night_dir)
    if s.get("approved"):
        return False, "Already approved; nothing to reject."
    s["rejected"] = {"by": by, "at": datetime.now().isoformat(timespec="seconds"), "why": why}
    save(night_dir, s)
    what = (s.get("agent_summary") or {}).get("summary") or "the proposed change"
    lessons.add(night_dir.name, f"Rejected by a person ({by}): {why}\nThe change was: {what}", by=by)
    return True, "Rejected. Reason saved to lessons.md; the next night's agent will read it."


def nights() -> list[tuple[Path, dict]]:
    out = []
    for d in sorted(config.RUNS.glob("20*"), reverse=True):
        if (d / "summary.json").exists():
            try:
                out.append((d, load(d)))
            except ValueError:
                continue
    return out


def _trend_svg(points: list[tuple[str, float]]) -> str:
    if len(points) < 2:
        return ""
    w, h, pad = 820, 180, 28
    xs = [pad + i * (w - 2 * pad) / (len(points) - 1) for i in range(len(points))]
    ys = [h - pad - p * (h - 2 * pad) for _, p in points]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    dots = "".join(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='5'/><text x='{x:.1f}' y='{y - 12:.1f}'>{p:.0%}</text>"
                   for x, y, (_, p) in zip(xs, ys, points))
    return (f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='pass rate by night'>"
            f"<path d='{path}' fill='none' stroke='#7bc67e' stroke-width='3'/>{dots}</svg>")


def render() -> str:
    rows, points = [], []
    for d, s in nights():
        b, a = s.get("before"), s.get("after")
        base = f"{s.get('baseline_passed', 0)}/{s.get('baseline_total', 0)}"
        ba = f"{b['passed']}/{b['total']} &rarr; {a['passed']}/{a['total']}" if b and a else "-"
        status = "approved" if s.get("approved") else "rejected" if s.get("rejected") else ("ready" if str(s.get("verdict", "")).startswith("Ready") else "")
        mock = " (mock)" if (s.get("trace") or {}).get("where") == "mock" else ""
        rows.append(f"<tr><td><a href='{html.escape(d.name)}/report.html'>{html.escape(d.name)}</a>{mock}</td><td>{base}</td>"
                    f"<td>{ba}</td><td class='{status}'>{status or '-'}</td><td>{html.escape(str(s.get('verdict', ''))[:120])}</td></tr>")
        if s.get("baseline_total") and not mock:
            points.append((d.name, s["baseline_passed"] / s["baseline_total"]))
    points.reverse()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Nightshift</title><style>
body{{margin:0;background:#0f1512;color:#eef2ee;font:16px/1.5 -apple-system,system-ui,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:32px 16px}} h1{{margin:0}} .sub{{color:#9aa89f}}
.card{{background:#17201b;border:1px solid #2a362f;border-radius:12px;padding:16px;margin:16px 0;overflow-x:auto}}
table{{width:100%;border-collapse:collapse}} td,th{{text-align:left;padding:8px;border-bottom:1px solid #2a362f;font-size:14px}}
th{{color:#9aa89f;font-weight:500}} a{{color:#ede6c8}} .approved{{color:#7bc67e;font-weight:600}} .ready{{color:#ede6c8}}
svg{{width:100%;height:auto}} svg circle{{fill:#7bc67e}} svg text{{fill:#eef2ee;font-size:13px;text-anchor:middle}}
</style></head><body><main><h1>Nightshift</h1>
<div class="sub">Every night: read the calls, fix the instructions, prove it, wait for a human. Godfrey, Sunset Plumbing demo line (simulated calls).</div>
<div class="card"><b>Pass rate by night</b>{_trend_svg(points) or "<div class='sub'>One night so far.</div>"}</div>
<div class="card"><table><tr><th>Night</th><th>Overnight calls passed</th><th>Re-test before &rarr; after</th><th>Status</th><th>Verdict</th></tr>
{"".join(rows) or "<tr><td colspan=5>No nights yet.</td></tr>"}</table></div></main></body></html>"""


def write() -> Path:
    config.RUNS.mkdir(exist_ok=True)
    p = config.RUNS / "index.html"
    p.write_text(render())
    return p

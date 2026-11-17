"""Build a public, static copy of Nightshift's pages (site/) for the submission's live link. Never deploys.

What goes public: the dashboard, and for each chosen night its report and stage view. The proposed change shows a
few lines of the agent's instructions, so a night is only exported when asked for by name. Before export, every page
is scanned for things that must never be public (keys, phone numbers that aren't the demo's 555 numbers, the real
shop names the redact tool knows about).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import config, dashboard

SITE = config.PROJECT / "site"
PHONE = re.compile(r"(?<!\d)(?:\+?1[ -.]?)?\(?(\d{3})\)?[ -.]?(\d{3})[ -.]?(\d{4})(?!\d)")


FOUND = re.compile(r"^\s+\[([A-Z_]+)\] '(.*)'\s*$")


def redact_scan(path: Path) -> list[tuple[str, str]]:
    """demo-factory's redact tool knows real shop names and number/address formats (--keep-demo allows the 555
    demos). Returns (kind, text) for each find, minus UNIT, which only ever matches CSS colors and "#1" here."""
    df = config.WORKSPACE / "demo-factory"
    py = df / ".venv" / "bin" / "python"
    if not py.exists():
        return [("TOOL", "redact tool not found; check by hand")]
    p = subprocess.run([str(py), "-m", "demo_line.redact", str(path), "--check", "--keep-demo"], cwd=df,
                       capture_output=True, text=True)
    finds = [(m.group(1), m.group(2)) for m in map(FOUND.match, p.stdout.splitlines()) if m]
    return [(k, t) for k, t in finds if k != "UNIT"]


def mask(text: str, finds: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    """Replace each address/phone find with a neutral word; anything else is held back for a person."""
    held = []
    for kind, found in finds:
        if kind in ("ADDRESS", "PHONE", "NUMBER") and found in text:
            text = text.replace(found, "[address]" if kind == "ADDRESS" else "[number]")
        else:
            held.append((kind, found))
    return text, held


def problems(text: str) -> list[str]:
    out = []
    if re.search(r"sk-(?:hr|ant|proj)-[A-Za-z0-9_-]{8,}", text):
        out.append("looks like an API key")
    for m in PHONE.finditer(text):
        if m.group(2) != "555":
            out.append("phone number that isn't a 555 demo number: ***-***-**" + m.group(3)[-2:])
    return out


def landing(nights: list[str]) -> str:
    """The front page: what Nightshift is, the best night's numbers, and links to its stage view, report and video."""
    import html
    import json
    best = None
    for n in nights:
        s = json.loads((config.RUNS / n / "summary.json").read_text())
        if s.get("approved") and s.get("after"):
            best = (n, s)
            break
    nums = ""
    if best:
        n, s = best
        b, a = s["before"], s["after"]
        nums = (f"<div class='nums'><div><b>{b['passed']}/{b['total']}</b><span>before</span></div>"
                f"<div><b>{a['passed']}/{a['total']}</b><span>after, fresh calls</span></div>"
                f"<div><b>{s.get('p_value', 0):.0%}</b><span>chance it's luck</span></div></div>"
                f"<p class='links'><a href='{html.escape(n)}/stage.html'>Watch the night (6 slides)</a> · "
                f"<a href='{html.escape(n)}/report.html'>Full report</a>"
                + (f" · <a href='{html.escape(n)}/demo.mp4'>Video</a>" if (config.RUNS / n / "demo.mp4").exists() else "")
                + " · <a href='nights.html'>Every night</a></p>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nightshift</title><style>
body{{margin:0;background:#0f1512;color:#eef2ee;font:18px/1.55 -apple-system,system-ui,sans-serif}}
main{{max-width:860px;margin:0 auto;padding:64px 16px}} h1{{font-size:clamp(40px,7vw,72px);margin:0;line-height:1.05}}
.sub{{color:#9aa89f;font-size:22px;margin:14px 0 30px}} .nums{{display:flex;gap:28px;flex-wrap:wrap;margin:26px 0}}
.nums div{{background:#17201b;border:1px solid #2a362f;border-radius:14px;padding:16px 22px}} .nums b{{display:block;font-size:40px;color:#7bc67e}}
.nums span{{color:#9aa89f;font-size:15px}} ol{{padding-left:22px}} li{{margin:8px 0}} a{{color:#ede6c8}} .links{{font-size:19px}}
.foot{{color:#6f7d74;font-size:14px;margin-top:40px}}
</style></head><body><main>
<h1>Nightshift</h1>
<div class="sub">The night shift for your voice agent. It reads every call overnight, fixes what went wrong, proves the fix, and waits for a person to approve it.</div>
{nums}
<ol>
<li><b>Calls.</b> Each night's calls are graded: simulated callers, or the line's real calls with names and numbers scrubbed.</li>
<li><b>Fix, on HarnessRouter.</b> A Claude Code agent traces each failure to the instruction that caused it and makes the smallest edit.</li>
<li><b>Safety check.</b> Any edit to the AI-and-recording disclosure is thrown out.</li>
<li><b>Proof.</b> The same calls re-run fresh, before and after, with the odds the gain is luck. One case getting much worse blocks approval.</li>
<li><b>A person approves.</b> Nothing goes live on its own.</li>
</ol>
<div class="foot">Built by Lucas for The Big Hack Game (Berkeley), Nov 2026. Agent: Godfrey, an after-hours AI receptionist for plumbers; demo shop Sunset Plumbing is made up. Scores are simulated calls.</div>
</main></body></html>"""


def build(nights: list[str]) -> tuple[Path, list[str]]:
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir()
    issues: list[str] = []
    keep = []
    for name in nights:
        src = config.RUNS / name
        if not (src / "summary.json").exists():
            issues.append(f"{name}: no such night")
            continue
        dst = SITE / name
        dst.mkdir()
        if (src / "demo.mp4").exists():
            shutil.copy(src / "demo.mp4", dst / "demo.mp4")
        for page in ("report.html", "stage.html"):
            if (src / page).exists():
                text, held = mask((src / page).read_text(), redact_scan(src / page))
                found = problems(text) + [f"{k}: {t[:40]}" for k, t in held]
                if found:
                    issues += [f"{name}/{page}: {f}" for f in found]
                    continue
                (dst / page).write_text(text)
        keep.append(name)
    # The dashboard lists only the exported nights.
    real_runs = config.RUNS
    try:
        tmp = SITE / ".runs"
        tmp.mkdir()
        for name in keep:
            (tmp / name).mkdir()
            shutil.copy(real_runs / name / "summary.json", tmp / name / "summary.json")
        config.RUNS = tmp
        (SITE / "nights.html").write_text(dashboard.render())
        (SITE / "index.html").write_text(landing(keep))
    finally:
        config.RUNS = real_runs
        shutil.rmtree(SITE / ".runs", ignore_errors=True)
    for page in ("index.html", "nights.html"):
        issues += [f"{page}: {f}" for f in problems((SITE / page).read_text())]
    return SITE, issues

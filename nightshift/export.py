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
        (SITE / "index.html").write_text(dashboard.render())
    finally:
        config.RUNS = real_runs
        shutil.rmtree(SITE / ".runs", ignore_errors=True)
    issues += [f"index.html: {f}" for f in problems((SITE / "index.html").read_text())]
    return SITE, issues

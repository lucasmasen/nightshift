"""Put a finished proof's numbers into the pitch ([PROOF]) and the X draft ([result]).

Only fills when the proof holds: the friends' calls (twin tests) passed more often after the fix and the verdict
is not "Don't approve" or "Inconclusive". Otherwise it leaves the placeholders and says why, so a bad or noisy
number never lands in the pitch. Backups of both files go in the night's folder first. Never posts anything.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import config

PITCH = config.WORKSPACE / "company" / "pitch" / "3min-judges-nightshift-bighack.md"
XDRAFTS = config.PROJECT / "posts" / "x-drafts.md"
X_ROOM = 38  # characters left in draft 14 for [result]
STAR = "real_gas_troll"  # the friend's call the pitch tells the story of


def _counts(side: dict, cases: list[str]) -> tuple[int, int]:
    got = [side.get("cases", {}).get(c) for c in cases]
    got = [g for g in got if g]
    return sum(g[0] for g in got), sum(g[1] for g in got)


def lines(summary: dict) -> tuple[str | None, str | None, str]:
    """(pitch sentence, X result, reason). Sentences are None when the proof doesn't hold."""
    b, a = summary.get("before"), summary.get("after")
    if not b or not a:
        return None, None, "not proven yet (no before/after in summary.json)"
    v = summary.get("verdict", "")
    if v.startswith(("Don't approve", "Inconclusive", "Not better")):
        return None, None, f"proof didn't hold: {v}"
    twins = summary.get("twin_tests") or summary.get("failed_cases") or []
    tb, tn = _counts(b, twins)
    ta, _ = _counts(a, twins)
    if not tn or ta <= tb:
        return None, None, f"friends' calls didn't improve ({tb} to {ta} of {tn})"
    sb, sn = _counts(b, [STAR])
    sa, _ = _counts(a, [STAR])
    if sn and sa > sb:
        pitch = f"Run {sn} fresh times, the old Godfrey passed it {sb} of {sn}, the fixed one {sa} of {sn}"
    else:
        pitch = (f"On all {len(twins)} friends' calls, run fresh, the old Godfrey passed {tb} of {tn}, "
                 f"the fixed one {ta} of {tn}")
    x = f"proven: {tb} of {tn} → {ta} of {tn}"
    if len(x) > X_ROOM:
        x = f"{tb}/{tn} → {ta}/{tn} on retests"
    return pitch, x, "ok"


def _fill(text: str, placeholder: str, value: str, keep_prefixes: tuple[str, ...]) -> tuple[str, int]:
    out, n = [], 0
    for line in text.splitlines(keepends=True):
        if placeholder in line and not line.lstrip().startswith(keep_prefixes):
            line, n = line.replace(placeholder, value), n + line.count(placeholder)
        out.append(line)
    return "".join(out), n


def run(night: Path, dry_run: bool = False, pitch_path: Path = PITCH, x_path: Path = XDRAFTS, log=print) -> bool:
    from . import dashboard
    summary = dashboard.load(night)
    pitch, x, why = lines(summary)
    if not pitch or not x:
        log(f"Pitch and X draft left as they are: {why}.")
        return False
    log(f"Pitch [PROOF]: {pitch}.")
    log(f"X draft 14 [result]: {x}")
    if dry_run:
        return True
    backup = night / "fill-proof-backup"
    changed = False
    # Pitch: skip the instruction line ("Fill [PROOF] from...") and headings; X: only the quoted post lines.
    for path, ph, val, keep in ((pitch_path, "[PROOF]", pitch, ("Fill ", "#")),
                                (x_path, "[result]", x, ("*", "#", "-"))):
        if not path.exists():
            log(f"  {path.name}: not found, skipped")
            continue
        new, n = _fill(path.read_text(), ph, val, keep)
        if not n:
            log(f"  {path.name}: no {ph} left (already filled?)")
            continue
        backup.mkdir(exist_ok=True)
        shutil.copy2(path, backup / path.name)
        path.write_text(new)
        changed = True
        log(f"  {path.name}: filled {n} (backup in {backup.name}/)")
    return changed

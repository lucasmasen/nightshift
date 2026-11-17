"""How sure can we be that the fix helped? One-sided Fisher exact test on pass counts, standard library only.

Godfrey's failures are flaky (the same call passes most of the time), so a few extra passes can be luck. The verdict
only says "ready" when the improvement is unlikely to be chance.
"""
from __future__ import annotations

from math import comb


def fisher_improved(before_pass: int, before_n: int, after_pass: int, after_n: int) -> float:
    """P(after does at least this well by chance | both prompts equally good). Small = real improvement."""
    if before_n <= 0 or after_n <= 0:
        return 1.0
    total_pass = before_pass + after_pass
    n = before_n + after_n
    denom = comb(n, total_pass)
    if denom == 0:
        return 1.0
    p = 0.0
    for k in range(after_pass, min(after_n, total_pass) + 1):
        if total_pass - k > before_n:
            continue
        p += comb(after_n, k) * comb(before_n, total_pass - k) / denom
    return min(1.0, p)


def strength(p: float) -> str:
    if p < 0.05:
        return "strong"
    if p < 0.2:
        return "some"
    return "weak (could be luck)"

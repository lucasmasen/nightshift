"""Read a suite run file (demo_line/suite_runs/*.md) and pull out what went wrong.

A run file has a headline ("**210 of 237 passed.**"), a results table (| case | PASS/FAIL | notes |) and a
"## Transcripts" section with one "### case (PASS|FAIL)" block per call, in the same order as the table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HEADLINE = re.compile(r"\*\*(\d+) of (\d+) passed\.\*\*")
ROW = re.compile(r"^\| ([a-z0-9_]+) \| (PASS|FAIL)[^|]* \| (.*) \|\s*$")
BLOCK = re.compile(r"^### ([a-z0-9_]+) \((PASS|FAIL)[^)]*\)\s*$")


@dataclass
class Call:
    case: str
    passed: bool
    notes: str
    transcript: str = ""


@dataclass
class Run:
    path: Path
    passed: int
    total: int
    calls: list[Call] = field(default_factory=list)

    @property
    def failures(self) -> list[Call]:
        return [c for c in self.calls if not c.passed]

    @property
    def score(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def case_scores(self) -> dict[str, tuple[int, int]]:
        """case -> (passed, total)."""
        out: dict[str, tuple[int, int]] = {}
        for c in self.calls:
            p, t = out.get(c.case, (0, 0))
            out[c.case] = (p + int(c.passed), t + 1)
        return out


def parse(path: Path) -> Run:
    text = Path(path).read_text()
    m = HEADLINE.search(text)
    calls: list[Call] = []
    head, _, transcripts = text.partition("\n## Transcripts")
    for line in head.splitlines():
        r = ROW.match(line)
        if r:
            calls.append(Call(case=r.group(1), passed=r.group(2) == "PASS", notes=r.group(3).strip()))
    # Transcript blocks line up with table rows in order; match them case by case.
    blocks: list[tuple[str, bool, list[str]]] = []
    for line in transcripts.splitlines():
        b = BLOCK.match(line)
        if b:
            blocks.append((b.group(1), b.group(2) == "PASS", []))
        elif blocks:
            blocks[-1][2].append(line)
    queue = list(blocks)
    for c in calls:
        for i, (case, passed, lines) in enumerate(queue):
            if case == c.case and passed == c.passed:
                c.transcript = "\n".join(lines).strip()
                del queue[i]
                break
    passed = int(m.group(1)) if m else sum(c.passed for c in calls)
    total = int(m.group(2)) if m else len(calls)
    return Run(path=Path(path), passed=passed, total=total, calls=calls)


def failures_markdown(run: Run, limit: int = 12) -> str:
    """The brief Nightshift's agent reads: each failed call, why the grader failed it, and the transcript."""
    fails = run.failures[:limit]
    out = [f"# Failed calls from {run.path.name}",
           f"{run.passed} of {run.total} simulated calls passed. {len(run.failures)} failed"
           + (f"; the first {limit} are below." if len(run.failures) > limit else "."),
           "Each call was graded by code checks and a judge model against the case's pass criteria.", ""]
    for n, c in enumerate(fails, 1):
        out += [f"## {n}. {c.case} (FAIL)", f"**Why it failed:** {c.notes}", "", c.transcript or "(no transcript)", ""]
    return "\n".join(out)


def newest_run(runs_dir: Path, after: float = 0.0) -> Path | None:
    files = [p for p in runs_dir.glob("*.md") if p.stat().st_mtime > after and not p.name.startswith("real-")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None

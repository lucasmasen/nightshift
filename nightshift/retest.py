"""Steps 1 and 3: run simulated calls in the test checkout (demo-factory-nightshift) with a given prompt.

The test checkout's prompt.md is swapped in only for the run and restored right after (git checkout), so the
branch never keeps a candidate and demo-factory/'s real prompt.md is never touched. Spend goes through suite.py's
own $2/day cap, which counts every demo-factory* checkout.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from . import collect, config

TRANSCRIPTS = re.compile(r"Transcripts: (\S+\.md)")


class Refused(RuntimeError):
    pass


@dataclass
class Result:
    run: collect.Run
    output: str


REAL_PROMPT = config.WORKSPACE / "demo-factory" / config.PROMPT_REL  # read-only reference, never written


def checkout_ok() -> list[str]:
    problems = []
    if not (config.TEST_CHECKOUT / config.PROMPT_REL).exists():
        problems.append(f"Test checkout missing: {config.TEST_CHECKOUT}")
        return problems
    if config.TEST_CHECKOUT.resolve() == (config.WORKSPACE / "demo-factory").resolve():
        problems.append("The test checkout must not be demo-factory/ itself.")
    # No git here on purpose (11/16: an Xcode update made /usr/bin/git refuse to run until its license is accepted).
    if REAL_PROMPT.exists() and (config.TEST_CHECKOUT / config.PROMPT_REL).read_text() != REAL_PROMPT.read_text():
        problems.append("The test checkout's prompt.md differs from demo-factory's; a candidate may not have been restored.")
    if not config.PYTHON.exists():
        problems.append(f"Python not found: {config.PYTHON}")
    return problems


def current_prompt() -> str:
    return (config.TEST_CHECKOUT / config.PROMPT_REL).read_text()


def run_suite(cases: list[str], prompt_text: str | None = None, repeat: int = 1, log=print) -> Result:
    """Run the given cases (all if empty) with prompt_text swapped in, or the checkout's prompt if None."""
    problems = checkout_ok()
    if problems:
        raise RuntimeError("; ".join(problems))
    target = config.TEST_CHECKOUT / config.PROMPT_REL
    original = target.read_text()
    cmd = [str(config.PYTHON), "-m", "demo_line.suite", *cases, "--repeat", str(repeat)]
    if repeat > 1:
        cmd.append("--exact")  # same number of calls before and after, so the scores compare fairly
    try:
        if prompt_text is not None:
            target.write_text(prompt_text)
        log("  $ " + " ".join(cmd[1:]))
        p = subprocess.run(cmd, cwd=config.TEST_CHECKOUT, capture_output=True, text=True, timeout=3600)
    finally:
        target.write_text(original)  # always put the original back, even if the simulator crashed
    out = p.stdout + p.stderr
    for line in out.splitlines():
        if line.startswith(("Today (Pacific)", "Refused", "Stopping", "Paid calls")):
            log("  " + line)
    if p.returncode == 2 and "Refused" in out:
        raise Refused(next(l for l in out.splitlines() if l.startswith("Refused")))
    m = TRANSCRIPTS.search(out)
    if not m:
        raise RuntimeError("The simulator finished without a run file:\n" + out[-1500:])
    return Result(run=collect.parse(config.TEST_CHECKOUT / m.group(1)), output=out)

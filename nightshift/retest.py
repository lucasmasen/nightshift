"""Steps 1 and 3: run simulated calls in the test checkout (demo-factory-nightshift) with a given prompt.

The test checkout's prompt.md is swapped in only for the run and restored right after (git checkout), so the
branch never keeps a candidate and demo-factory/'s real prompt.md is never touched. Spend goes through suite.py's
own $2/day cap, which counts every demo-factory* checkout.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import re
import signal
import subprocess
import time
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
    real = config.WORKSPACE / "demo-factory"
    if real.exists() and os.path.samefile(config.TEST_CHECKOUT, real):
        problems.append("The test checkout must not be demo-factory/ itself.")
    # No git here on purpose (11/16: an Xcode update made /usr/bin/git refuse to run until its license is accepted).
    test_prompt = config.TEST_CHECKOUT / config.PROMPT_REL
    if REAL_PROMPT.exists() and os.path.samefile(test_prompt, REAL_PROMPT):
        problems.append("The test checkout's prompt.md is the real one (symlink?).")
    elif REAL_PROMPT.exists() and test_prompt.read_text() != REAL_PROMPT.read_text():
        problems.append("The test checkout's prompt.md differs from demo-factory's; a candidate may not have been restored.")
    if not config.PYTHON.exists():
        problems.append(f"Python not found: {config.PYTHON}")
    return problems


def current_prompt() -> str:
    return (config.TEST_CHECKOUT / config.PROMPT_REL).read_text()


class Busy(RuntimeError):
    """Another Nightshift run held the test checkout for too long."""


@contextlib.contextmanager
def checkout_lock(log=print, wait_s: int = 3 * 3600):
    """One simulator run at a time in the test checkout: each run swaps prompt.md in and out, so two at once (say the
    12:10am script still going at 2am) would test the wrong instructions. Waits for the other run to finish."""
    path = config.TEST_CHECKOUT / ".nightshift.lock"
    with open(path, "a+") as f:
        start, told = time.monotonic(), False
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not told:
                    log("  another Nightshift run is using the test checkout; waiting for it to finish...")
                    told = True
                if time.monotonic() - start > wait_s:
                    raise Busy("The test checkout stayed busy for hours; not running.")
                time.sleep(15)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def run_suite(cases: list[str], prompt_text: str | None = None, repeat: int = 1, log=print,
              fresh: bool = False) -> Result:
    """Run the given cases (all if empty) with prompt_text swapped in, or the checkout's prompt if None."""
    with checkout_lock(log):
        return _run_suite(cases, prompt_text, repeat, log, fresh)


def run_suite_complete(cases: list[str], prompt_text: str | None = None, repeat: int = 1, log=print,
                       fresh: bool = False, retries: int = 2) -> Result:
    """run_suite, then re-run only the calls that crashed in the simulator (a wifi drop, an API hiccup), up to
    `retries` times, so before and after end up with the same number of real calls to compare."""
    first = run_suite(cases, prompt_text, repeat, log, fresh)
    calls = [c for c in first.run.calls if not c.crashed]
    outputs = [first.output]
    todo = [c.case for c in first.run.crashed]
    for n in range(retries):
        if not todo:
            break
        log(f"  {len(todo)} call(s) crashed in the simulator, not the agent; re-running just those ({n + 1}/{retries})")
        again = run_suite(todo, prompt_text, 1, log, fresh)
        outputs.append(again.output)
        calls += [c for c in again.run.calls if not c.crashed]
        todo = [c.case for c in again.run.crashed]
    run = collect.Run(first.run.path, sum(c.passed for c in calls), len(calls), calls)
    return Result(run=run, output="\n".join(outputs))


def _run_suite(cases: list[str], prompt_text: str | None, repeat: int, log, fresh: bool) -> Result:
    problems = checkout_ok()
    if problems:
        raise RuntimeError("; ".join(problems))
    target = config.TEST_CHECKOUT / config.PROMPT_REL
    original = target.read_text()
    cmd = [str(config.PYTHON), "-m", "demo_line.suite", *cases, "--repeat", str(repeat)]
    if repeat > 1:
        cmd.append("--exact")  # same number of calls before and after, so the scores compare fairly
    if fresh:
        # Never reuse earlier results: the failed calls that started the night would otherwise count in "before",
        # and since they were picked for failing, "before" would look worse than it is (11/16 lesson).
        cmd.append("--fresh")
    def _term(signum, frame):  # make kill/Ctrl-C still run the restore below
        raise SystemExit(128 + signum)

    old_handler = signal.signal(signal.SIGTERM, _term)
    try:
        if prompt_text is not None:
            target.write_text(prompt_text)
        log("  $ " + " ".join(cmd[1:]))
        p = subprocess.run(cmd, cwd=config.TEST_CHECKOUT, capture_output=True, text=True, timeout=3600)
    finally:
        target.write_text(original)  # always put the original back, even if the simulator crashed
        signal.signal(signal.SIGTERM, old_handler)
    out = p.stdout + p.stderr
    for line in out.splitlines():
        if line.startswith(("Today (Pacific)", "Refused", "Stopping", "Paid calls")):
            log("  " + line)
    if p.returncode == 2 and "Refused" in out:
        raise Refused(next((ln.strip() for ln in out.splitlines() if "Refused" in ln), "Refused by the cap"))
    if "Stopping: today's" in out:
        raise Refused("The $2/day cap stopped the run partway; the numbers would not compare fairly.")
    m = TRANSCRIPTS.search(out)
    if not m:
        raise RuntimeError("The simulator finished without a run file:\n" + out[-1500:])
    return Result(run=collect.parse(config.TEST_CHECKOUT / m.group(1)), output=out)

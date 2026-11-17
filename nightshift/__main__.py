"""Nightshift: review a voice agent's calls overnight, fix its instructions, prove the fix, wait for approval.

    python -m nightshift check                         # is everything set up? (no spend)
    python -m nightshift night --mock --from-run FILE --no-retest   # whole loop, $0: mock fixer, no sims
    python -m nightshift night --from-run FILE         # real: HarnessRouter fixes the failures in FILE, then re-tests
    python -m nightshift night                         # real, full night: simulate every case first (about $0.25)
    python -m nightshift night book_basic interrupts   # only these cases tonight

Outputs land in runs/<date-time>/: report.html (open it), prompt.proposed.md, change.diff, failures.md,
nightshift-report.md, summary.json. Nothing is ever applied to the live line or to demo-factory/'s prompt.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import collect, config, dashboard, fixer, guard, report, retest, stage
from .hr import Client, HRError


def log(msg: str) -> None:
    print(msg, flush=True)


def use_base(base: str | None) -> None:
    if base:
        config.HARNESS_BASE = base
        config.HARNESS_NAME = f"Nightshift (voice agent fixer, {base})"


def check(live: bool = False) -> int:
    ok = True
    key = config.api_key()
    log(f"HarnessRouter key in nightshift/.env: {'yes' if key else 'NO (add HR_API_KEY=sk-hr-... to nightshift/.env)'}")
    if key:
        try:
            me = Client(key).me()
            log(f"  works: workspace {me.get('workspace_name') or me.get('workspace')}")
        except HRError as e:
            ok = False
            log(f"  key rejected: {e}")
    else:
        ok = False
    if key and ok and live:
        good, msg = Client(key).model_check()
        ok = ok and good
        log(f"Model key ({config.HARNESS_BASE}) under Bring Your Own Key: {'works' if good else 'NOT working: ' + msg}")
    problems = retest.checkout_ok()
    log(f"Test checkout {config.TEST_CHECKOUT.name}: {'ready' if not problems else '; '.join(problems)}")
    return 0 if ok and not problems else 1


def pick_guard_cases(run: collect.Run, failed: list[str], n: int) -> list[str]:
    """Cases that passed every time, re-run after the fix to catch regressions. Spread across the list."""
    passing = [c for c, (p, t) in run.case_scores().items() if p == t and c not in failed]
    if len(passing) <= n:
        return passing
    step = len(passing) / n
    return [passing[int(i * step)] for i in range(n)]


def score_dict(run: collect.Run) -> dict:
    return {"passed": run.passed, "total": run.total, "file": run.path.name, "cases": run.case_scores()}


def night(args: argparse.Namespace) -> int:
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out = config.RUNS / stamp
    out.mkdir(parents=True)
    log(f"Nightshift night {stamp} -> {out}")

    # 1. Tonight's calls.
    if args.from_run:
        base = collect.parse(Path(args.from_run))
        log(f"1. Calls: read {base.path.name}: {base.passed} of {base.total} passed")
    else:
        log("1. Calls: simulating tonight's calls with the current prompt...")
        base = retest.run_suite(args.cases, repeat=1, log=log).run
        log(f"   {base.passed} of {base.total} passed")
    counts: dict[str, int] = {}
    for c in base.failures:
        counts[c.case] = counts.get(c.case, 0) + 1
    # Fix the cases that failed most first; a few per night keeps the fix small and the re-test cheap.
    failed = sorted(sorted(counts, key=lambda c: -counts[c])[:args.max_cases])
    if len(counts) > len(failed):
        log(f"   {len(counts)} cases failed; tonight fixes the {len(failed)} that failed most (--max-cases)")
    summary: dict = {"date": stamp, "agent": "Godfrey (Sunset Plumbing demo line)", "baseline_passed": base.passed,
                     "baseline_total": base.total, "baseline_file": base.path.name, "failed_cases": failed}
    if not failed:
        summary["verdict"] = "Clean night: every call passed. Nothing to fix."
        return finish(out, summary)

    failures_md = out / "failures.md"
    tonight = collect.Run(base.path, base.passed, base.total, [c for c in base.calls if c.case in failed or c.passed])
    failures_md.write_text(collect.failures_markdown(tonight))
    prompt_now = out / "prompt.before.md"
    prompt_now.write_text(retest.current_prompt())
    shop = config.TEST_CHECKOUT / config.SHOP_REL

    # 2. The fix.
    log(f"2. Fix: {len(tonight.failures)} failed calls across {len(failed)} cases: {', '.join(failed)}")
    if args.mock:
        fix = fixer.run_mock(prompt_now, failures_md, shop, log=log)
    else:
        key = config.api_key()
        if not key:
            log("No HR_API_KEY in nightshift/.env. Add it, or run with --mock.")
            return 1
        fix = fixer.run_harnessrouter(Client(key), prompt_now, failures_md, shop, log=log)
    old = prompt_now.read_text()
    (out / "prompt.proposed.md").write_text(fix.prompt)
    (out / "nightshift-report.md").write_text(fix.report)
    diff = guard.diff_text(old, fix.prompt)
    (out / "change.diff").write_text(diff)
    summary.update(agent_report=fix.report, diff=diff, trace=fix.trace, agent_summary=fix.summary)

    # 3. Safety check.
    problems = guard.check(old, fix.prompt)
    summary["guard_problems"] = problems
    log("3. Safety check: " + ("passed" if not problems else "REJECTED: " + " | ".join(problems)))
    if problems:
        summary["verdict"] = "Nightshift's fix was rejected by the safety check, so nothing was re-tested."
        return finish(out, summary)

    # 4. Prove it: the same cases, same number of calls, before and after.
    if args.no_retest:
        summary["verdict"] = "Fix proposed and passed the safety check (not re-tested: --no-retest)."
        return finish(out, summary)
    cases = failed + pick_guard_cases(base, failed, args.guards)
    log(f"4. Re-test: {len(cases)} cases x {args.repeat} calls, before and after")
    try:
        before = retest.run_suite(cases, repeat=args.repeat, log=log).run
        after = retest.run_suite(cases, fix.prompt, repeat=args.repeat, log=log).run
    except retest.Refused as e:
        summary["verdict"] = f"Fix proposed, but the re-test was refused by the $2/day simulator cap: {e}"
        return finish(out, summary)
    summary.update(before=score_dict(before), after=score_dict(after))
    fb = sum(before.case_scores().get(c, (0, 0))[0] for c in failed)
    fa = sum(after.case_scores().get(c, (0, 0))[0] for c in failed)
    broke = [c for c in cases if c not in failed
             and after.case_scores().get(c, (0, 1))[0] < before.case_scores().get(c, (0, 0))[0]]
    summary["broke"] = broke
    if after.passed > before.passed and not broke:
        v = f"Ready for approval: {before.passed}/{before.total} to {after.passed}/{after.total} on the re-run."
    elif after.passed > before.passed:
        v = f"Better overall ({before.passed} to {after.passed}), but it made {', '.join(broke)} worse. Review before approving."
    else:
        v = f"Not better ({before.passed} to {after.passed} of {after.total}). Don't approve; Nightshift will try again."
    summary["verdict"] = v + f" Failed cases alone: {fb} to {fa} passes."
    return finish(out, summary)


def finish(out: Path, summary: dict) -> int:
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (out / "report.html").write_text(report.render(summary))
    log(f"Verdict: {summary.get('verdict')}")
    log(f"Report: {out / 'report.html'}")
    if summary.get("diff"):
        log(f"Stage view: {stage.write(out)}")
    log(f"All nights: {dashboard.write()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nightshift", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--live", action="store_true", help="Also run one tiny task to prove the model key works (<1 cent)")
    c.add_argument("--base", help="Harness base: claude-code (default) or codex")
    w = sub.add_parser("wait-model", help="Re-check the model key every minute until it works, then exit 0")
    w.add_argument("--minutes", type=int, default=45)
    w.add_argument("--base")
    n = sub.add_parser("night")
    n.add_argument("cases", nargs="*", help="Cases to simulate tonight (default: all)")
    n.add_argument("--from-run", help="Use an existing suite run file instead of simulating tonight's calls")
    n.add_argument("--base", help="Harness base: claude-code (default) or codex")
    n.add_argument("--mock", action="store_true", help="Local stand-in for the HarnessRouter agent ($0)")
    n.add_argument("--no-retest", action="store_true", help="Stop after the safety check (no simulated calls)")
    n.add_argument("--repeat", type=int, default=3, help="Calls per case in the before/after re-test (default 3)")
    n.add_argument("--max-cases", type=int, default=5, help="Most failed cases to fix in one night (default 5)")
    n.add_argument("--guards", type=int, default=6, help="Already-passing cases re-run to catch regressions")
    ap = sub.add_parser("approve", help="Human gate: save a night's fix as approved (never puts it live)")
    ap.add_argument("dir")
    ap.add_argument("--by", default="lucas")
    ap.add_argument("--force", action="store_true")
    sub.add_parser("dashboard", help="Rebuild runs/index.html")
    st = sub.add_parser("stage", help="Build the 6-slide demo view for one night")
    st.add_argument("dir")
    r = sub.add_parser("report")
    r.add_argument("dir")
    a = p.parse_args(argv)
    use_base(getattr(a, "base", None))
    if a.cmd == "check":
        return check(live=a.live)
    if a.cmd == "wait-model":
        import time
        client = Client(config.api_key())
        for i in range(a.minutes):
            good, msg = client.model_check()
            if good:
                log(f"model key works ({datetime.now():%H:%M})")
                return 0
            if i == 0 or i % 10 == 0:
                log(f"waiting for a working model key ({config.HARNESS_BASE}): {msg[:160]}")
            time.sleep(60)
        log("gave up waiting for a working model key")
        return 1
    if a.cmd == "approve":
        ok, msg = dashboard.approve(Path(a.dir), a.by, a.force)
        log(msg)
        dashboard.write()
        return 0 if ok else 1
    if a.cmd == "stage":
        log(str(stage.write(Path(a.dir))))
        return 0
    if a.cmd == "dashboard":
        log(str(dashboard.write()))
        return 0
    if a.cmd == "report":
        d = Path(a.dir)
        (d / "report.html").write_text(report.render(json.loads((d / "summary.json").read_text())))
        log(str(d / "report.html"))
        return 0
    return night(a)


if __name__ == "__main__":
    sys.exit(main())

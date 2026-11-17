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

from . import anyagent, collect, config, cost, dashboard, export, fixer, guard, lessons, ops, realcalls, report, retest, stage, stats, video
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
    if n <= 0:
        return []
    if len(passing) <= n:
        return passing
    step = len(passing) / n
    return [passing[int(i * step)] for i in range(n)]


def score_dict(run: collect.Run) -> dict:
    return {"passed": run.passed, "total": run.total, "file": run.path.name, "cases": run.case_scores()}


def night(args: argparse.Namespace) -> int:
    problems = retest.checkout_ok()
    if problems:
        log("Not starting: " + "; ".join(problems))
        return 1
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out = config.RUNS / stamp
    out.mkdir(parents=True)
    log(f"Nightshift night {stamp} -> {out}")

    # 1. Tonight's calls.
    if args.real_days or args.real_file:
        return real_night(args, out, stamp)
    if args.from_run:
        base = collect.parse(Path(args.from_run))
        log(f"1. Calls: read {base.path.name}: {base.passed} of {base.total} passed")
    else:
        log("1. Calls: simulating tonight's calls with the current prompt...")
        base = retest.run_suite(args.cases, repeat=1, log=log).run
        log(f"   {base.passed} of {base.total} passed")
    # Stacking: build on an approved night's prompt and skip the cases it already fixed.
    stack_prompt, skip, builds_on = None, set(), None
    if args.on_top_of:
        prev = Path(args.on_top_of)
        ps = dashboard.load(prev)
        if not ps.get("approved"):
            log(f"Not starting: {prev.name} isn't approved.")
            return 1
        stack_prompt = (prev / "prompt.proposed.md").read_text()
        skip = set(ps.get("failed_cases", [])) | set(ps.get("skipped_cases", []))
        builds_on = prev.name
        log(f"   building on approved night {prev.name}; skipping the cases it fixed: {', '.join(sorted(skip))}")
    counts: dict[str, int] = {}
    for c in base.failures:
        if c.case in skip:
            continue
        counts[c.case] = counts.get(c.case, 0) + 1
    # Fix the cases that failed most first; a few per night keeps the fix small and the re-test cheap.
    failed = sorted(sorted(counts, key=lambda c: -counts[c])[:max(1, args.max_cases)])
    if len(counts) > len(failed):
        log(f"   {len(counts)} cases failed; tonight fixes the {len(failed)} that failed most (--max-cases)")
    summary: dict = {"date": stamp, "agent": "Godfrey (Sunset Plumbing demo line)", "baseline_passed": base.passed,
                     "baseline_total": base.total, "baseline_file": base.path.name, "baseline_path": str(base.path.resolve()), "failed_cases": failed,
                     "builds_on": builds_on, "skipped_cases": sorted(skip)}
    if not failed:
        summary["verdict"] = "Clean night: every call passed. Nothing to fix."
        return finish(out, summary)

    failures_md = out / "failures.md"
    tonight = collect.Run(base.path, base.passed, base.total, [c for c in base.calls if c.case in failed or c.passed])
    failures_md.write_text(collect.failures_markdown(tonight))
    prompt_now = out / "prompt.before.md"
    prompt_now.write_text(stack_prompt if stack_prompt is not None else retest.current_prompt())
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

    # 4. Prove it: the same cases, same number of fresh calls, before and after.
    if args.no_retest:
        summary["verdict"] = "Fix proposed and passed the safety check (not re-tested: --no-retest)."
        return finish(out, summary)
    cases = failed + pick_guard_cases(base, failed, args.guards)
    try:
        prove(summary, cases, old, fix.prompt, args.repeat)
    except retest.Refused as e:
        summary["verdict"] = f"Fix proposed, but the re-test was refused by the $2/day simulator cap: {e}"
    return finish(out, summary)


DEFAULT_GUARDS = ["book_basic", "reschedule_after_booking", "skeptic_badmouth_competitors"]


def real_night(args: argparse.Namespace, out: Path, stamp: str) -> int:
    """Night from the demo line's real calls: redacted real-call flags go to the fixer; simulated twins prove it."""
    log(f"1. Calls: reading real calls from the last {args.real_days or '?'} days (Vapi, read-only), redacting...")
    rn = realcalls.load(args.real_days or 0, max(1, args.max_cases), Path(args.real_file) if args.real_file else None)
    log(f"   {rn.calls} real calls, {rn.flagged_calls} flagged: " + ", ".join(f"{k} x{v}" for k, v in rn.flag_counts.items()))
    summary: dict = {"date": stamp, "agent": "Godfrey (Sunset Plumbing demo line), REAL calls", "mode": "real",
                     "baseline_passed": rn.calls - rn.flagged_calls, "baseline_total": rn.calls,
                     "baseline_file": "real calls", "flag_counts": rn.flag_counts, "failed_cases": rn.cases}
    if not rn.cases:
        summary["verdict"] = "Clean night: no real call was flagged."
        return finish(out, summary)
    (out / "failures.md").write_text(rn.report)
    prompt_now = out / "prompt.before.md"
    prompt_now.write_text(retest.current_prompt())
    shop = config.TEST_CHECKOUT / config.SHOP_REL
    log(f"2. Fix: {rn.flagged_calls} flagged real calls; simulated twins to prove it: {', '.join(rn.cases)}")
    if args.mock:
        fix = fixer.run_mock(prompt_now, out / "failures.md", shop, log=log)
    else:
        key = config.api_key()
        if not key:
            log("No HR_API_KEY in nightshift/.env. Add it, or run with --mock.")
            return 1
        fix = fixer.run_harnessrouter(Client(key), prompt_now, out / "failures.md", shop, log=log)
    old = prompt_now.read_text()
    (out / "prompt.proposed.md").write_text(fix.prompt)
    (out / "nightshift-report.md").write_text(fix.report)
    diff = guard.diff_text(old, fix.prompt)
    (out / "change.diff").write_text(diff)
    summary.update(agent_report=fix.report, diff=diff, trace=fix.trace, agent_summary=fix.summary)
    problems = guard.check(old, fix.prompt)
    summary["guard_problems"] = problems
    log("3. Safety check: " + ("passed" if not problems else "REJECTED: " + " | ".join(problems)))
    if problems:
        summary["verdict"] = "Nightshift's fix was rejected by the safety check, so nothing was re-tested."
        return finish(out, summary)
    # Prove what the agent actually fixed: twins of the flags it names as fixed (fallback: every flagged behavior).
    fixed = realcalls.fixed_flags(fix.report)
    targets = realcalls.twins({f: 1 for f in fixed}, max(1, args.max_cases)) if fixed else rn.cases
    summary.update(fixed_flags=fixed, failed_cases=targets)
    log(f"   agent fixed: {', '.join(fixed) or '(not stated)'}; proving on: {', '.join(targets)}")
    cases = targets + [g for g in DEFAULT_GUARDS[:max(0, args.guards)] if g not in targets]
    summary["retest_cases"] = cases
    if args.no_retest:
        summary["verdict"] = "Fix proposed and passed the safety check (not re-tested: --no-retest)."
        return finish(out, summary)
    try:
        prove(summary, cases, old, fix.prompt, args.repeat)
    except retest.Refused as e:
        summary["verdict"] = f"Fix proposed, but the re-test was refused by the $2/day simulator cap: {e}"
    return finish(out, summary)


def agent_night(args: argparse.Namespace) -> int:
    """Any voice agent: a folder with prompt.md + call transcripts. The HarnessRouter agent triages and fixes."""
    try:
        a = anyagent.load(Path(args.dir))
    except ValueError as e:
        log(f"Not starting: {e}")
        return 1
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out = config.RUNS / stamp
    out.mkdir(parents=True)
    log(f"Nightshift night {stamp} for {a.name}: {len(a.calls)} calls -> {out}")
    client = None
    if not args.mock:
        key = config.api_key()
        if not key:
            log("No HR_API_KEY in nightshift/.env. Add it, or run with --mock.")
            return 1
        client = Client(key)
    log("1-2. Triage + fix: the agent reads every call, finds the ones that went wrong, fixes prompt.md")
    fix = anyagent.run(a, out, client, log=log)
    (out / "prompt.proposed.md").write_text(fix.prompt)
    (out / "nightshift-report.md").write_text(fix.report)
    diff = guard.diff_text(a.prompt, fix.prompt)
    (out / "change.diff").write_text(diff)
    reviewed = int(fix.summary.get("calls_reviewed") or len(a.calls))
    wrong = int(fix.summary.get("calls_wrong") or 0)
    summary: dict = {"date": stamp, "agent": a.name, "mode": "agent", "baseline_passed": max(0, reviewed - wrong),
                     "baseline_total": reviewed, "baseline_file": f"{a.name} transcripts", "failed_cases": [],
                     "agent_report": fix.report, "diff": diff, "trace": fix.trace, "agent_summary": fix.summary}
    problems = anyagent.safety_problems(a, fix.prompt) if fix.prompt != a.prompt else []
    summary["guard_problems"] = problems
    log("3. Safety check: " + ("passed" if not problems else "REJECTED: " + " | ".join(problems)))
    if not diff:
        summary["verdict"] = f"No change: the agent found nothing the instructions could fix ({wrong} of {reviewed} calls went wrong)."
    elif problems:
        summary["verdict"] = "Nightshift's fix was rejected by the safety check."
    else:
        summary["verdict"] = (f"Proposed, not proven: {wrong} of {reviewed} calls went wrong and the fix passed the safety "
                              "check, but there's no simulator for this agent, so it can't be re-tested before and after. "
                              "Review the diff, then compare tomorrow night's calls.")
    return finish(out, summary)


def verdict(summary: dict) -> str:
    """Ready only when the gain is unlikely to be luck (one-sided Fisher p < 0.2) and no passing case got worse."""
    b, a = summary["before"], summary["after"]
    if b["total"] != a["total"] or not a["total"]:
        summary["p_value"], summary["evidence"] = None, "inconclusive"
        return (f"Inconclusive: before and after ran different numbers of calls ({b['total']} vs {a['total']}), "
                "so they can't be compared. Re-run the proof.")
    p = stats.fisher_improved(b["passed"], b["total"], a["passed"], a["total"])
    summary["p_value"], summary["evidence"] = round(p, 3), stats.strength(p)
    gain, worse, broke = a["passed"] - b["passed"], summary.get("worse", []), summary.get("broke", [])
    tail = f" Evidence: {summary['evidence']} (p={p:.2f})." + (f" Slightly worse on {', '.join(worse)}." if worse else "")
    bc, ac = b.get("cases", {}), a.get("cases", {})
    big_drops = [c for c in ac if c in bc and bc[c][0] - ac[c][0] >= 2]
    if big_drops:
        return (f"Don't approve: {', '.join(big_drops)} dropped by 2 or more passes "
                f"({b['passed']} to {a['passed']} of {a['total']} overall)." + tail)
    if gain > 0 and p < 0.2 and not broke:
        return f"Ready for approval: {b['passed']}/{b['total']} to {a['passed']}/{a['total']} on fresh re-runs." + tail
    if gain > 0:
        why = f"it made {', '.join(broke)} worse" if broke else "the gain could be luck at this sample size"
        return f"Better ({b['passed']} to {a['passed']} of {a['total']}), but {why}. Review before approving." + tail
    return f"Not better on fresh re-runs ({b['passed']} to {a['passed']} of {a['total']}). Don't approve." + tail


def prove(summary: dict, cases: list[str], old: str, new: str, repeat: int) -> None:
    """Fresh calls, same cases, same count, before and after; sets before/after/broke/verdict on summary."""
    failed = summary.get("failed_cases", [])
    log(f"4. Re-test: {len(cases)} cases x {repeat} fresh calls, before and after")
    rb = retest.run_suite(cases, old, repeat=repeat, log=log, fresh=True)
    ra = retest.run_suite(cases, new, repeat=repeat, log=log, fresh=True)
    before, after = rb.run, ra.run
    summary["sim_cost_usd"] = round(summary.get("sim_cost_usd", 0) + cost.sim_cost(rb.output) + cost.sim_cost(ra.output), 3)
    summary.update(before=score_dict(before), after=score_dict(after), retest_repeat=repeat)
    bc, ac = before.case_scores(), after.case_scores()
    worse = [c for c in cases if ac.get(c, (0, 1))[0] < bc.get(c, (0, 0))[0]]
    summary["broke"] = [c for c in worse if c not in failed]
    summary["worse"] = worse
    summary["verdict"] = verdict(summary)


def finish(out: Path, summary: dict) -> int:
    if lessons.learn(out.name, summary):
        log(f"Lesson saved to {lessons.LESSONS.name}: the next night's agent will read it.")
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
    n.add_argument("--on-top-of", help="An approved night to build on: use its fixed prompt, skip the cases it fixed")
    n.add_argument("--real-days", type=int, help="Start from the demo line's REAL calls of the last N days")
    n.add_argument("--real-file", help="A saved real_call_qa report to start from (instead of fetching)")
    n.add_argument("--mock", action="store_true", help="Local stand-in for the HarnessRouter agent ($0)")
    n.add_argument("--no-retest", action="store_true", help="Stop after the safety check (no simulated calls)")
    n.add_argument("--repeat", type=int, default=4, help="Calls per case in the before/after re-test (default 3)")
    n.add_argument("--max-cases", type=int, default=5, help="Most failed cases to fix in one night (default 5)")
    n.add_argument("--guards", type=int, default=3, help="Already-passing cases re-run to catch regressions")
    ap = sub.add_parser("approve", help="Human gate: save a night's fix as approved (never puts it live)")
    ap.add_argument("dir")
    ap.add_argument("--by", default="lucas")
    ap.add_argument("--force", action="store_true")
    sub.add_parser("dashboard", help="Rebuild runs/index.html")
    sv = sub.add_parser("serve", help="Local web app with real Approve/Reject buttons (127.0.0.1 only)")
    sv.add_argument("--port", type=int, default=8765)
    rj = sub.add_parser("reject", help="Human 'no': record why, so the next night's agent learns from it")
    rj.add_argument("dir")
    rj.add_argument("--why", required=True)
    rj.add_argument("--by", default="lucas")
    rt = sub.add_parser("retest", help="Re-run the before/after proof for a night that already has a fix")
    rt.add_argument("dir")
    rt.add_argument("--repeat", type=int, default=4)
    rt.add_argument("--guards", type=int, default=3)
    ex = sub.add_parser("export", help="Build site/ (public static copy) from the named nights; never deploys")
    ex.add_argument("nights", nargs="+")
    ag = sub.add_parser("agent", help="Any voice agent: a folder with prompt.md + calls/ transcripts (see anyagent.py)")
    ag.add_argument("dir")
    ag.add_argument("--mock", action="store_true", help="No HarnessRouter call, no spend")
    ag.add_argument("--base", help="Harness base: claude-code (default) or codex")
    vf = sub.add_parser("verify", help="Run every case once with a night's fix and compare with its baseline (~$0.90)")
    vf.add_argument("dir")
    vf.add_argument("--reuse", help="a suite run file that already ran; only its crashed calls are re-run")
    sub.add_parser("publish", help="Export every real night and deploy the public site (Vercel)")
    cd = sub.add_parser("card", help="1080x1350 post image for a night")
    cd.add_argument("dir")
    cd.add_argument("--day", type=int)
    ar = sub.add_parser("arena", help="Same failures, several harnesses, one proof (needs a key for each)")
    ar.add_argument("dir")
    ar.add_argument("--bases", default="claude-code,codex")
    ar.add_argument("--repeat", type=int, default=4)
    ap2 = sub.add_parser("arena-prove", help="Prove the fixes an earlier `arena --repeat 0` made")
    ap2.add_argument("dir")
    ap2.add_argument("--repeat", type=int, default=4)
    gl = sub.add_parser("golive", help="Put an approved, verified fix on the real phone line (a person runs this)")
    gl.add_argument("dir")
    gl.add_argument("--really", action="store_true")
    vd = sub.add_parser("video", help="Silent captioned demo video of one night (demo.mp4)")
    vd.add_argument("dir")
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
    if a.cmd == "agent":
        return agent_night(a)
    if a.cmd == "approve":
        ok, msg = dashboard.approve(Path(a.dir), a.by, a.force)
        log(msg)
        dashboard.write()
        return 0 if ok else 1
    if a.cmd == "retest":
        d = Path(a.dir)
        summ = dashboard.load(d)
        base = None if summ.get("retest_cases") else collect.parse(Path(
            summ.get("baseline_path") or config.TEST_CHECKOUT / "demo_line" / "suite_runs" / summ["baseline_file"]))
        cases = summ.get("retest_cases") or (summ["failed_cases"] + pick_guard_cases(base, summ["failed_cases"], a.guards))
        summ.pop("approved", None)
        try:
            prove(summ, cases, (d / "prompt.before.md").read_text(), (d / "prompt.proposed.md").read_text(), a.repeat)
        except retest.Refused as e:
            summ["verdict"] = f"Re-test refused by the $2/day simulator cap: {e}"
        return finish(d, summ)
    if a.cmd == "export":
        site, issues = export.build(a.nights)
        log(f"Built {site}" + ("" if not issues else "\nHeld back (fix before publishing):\n  " + "\n  ".join(issues)))
        return 0 if not issues else 1
    if a.cmd == "verify":
        try:
            ok = ops.verify(Path(a.dir), log=log, reuse=Path(a.reuse) if a.reuse else None)["ok_to_go_live"]
        except retest.Refused as e:
            log(f"Refused by the $2/day cap: {e}")
            return 1
        dashboard.write()
        return 0 if ok else 1
    if a.cmd == "publish":
        ops.publish(log=log)
        return 0
    if a.cmd == "card":
        log(str(ops.card(Path(a.dir), a.day)))
        return 0
    if a.cmd in ("arena", "arena-prove"):
        res = (ops.arena(Path(a.dir), [b.strip() for b in a.bases.split(",") if b.strip()], a.repeat, log=log)
               if a.cmd == "arena" else ops.arena_prove(Path(a.dir), a.repeat, log=log))
        b = res.get("before", {})
        log(f"Before: {b.get('passed')}/{b.get('total')}")
        for base, e in res["entries"].items():
            af = e.get("after") or {}
            log(f"  {base:12} {e.get('model')}: after {af.get('passed', '-')}/{af.get('total', '-')}, "
                f"{e.get('changed_lines')} lines, ~${e.get('est_cost_usd') or 0:.2f}" + (f", REJECTED {e['guard']}" if e["guard"] else ""))
        return 0
    if a.cmd == "golive":
        return ops.golive(Path(a.dir), a.really, log=log)
    if a.cmd == "video":
        log(str(video.build(Path(a.dir))))
        return 0
    if a.cmd == "stage":
        log(str(stage.write(Path(a.dir))))
        return 0
    if a.cmd == "serve":
        from . import serve
        serve.run(a.port)
        return 0
    if a.cmd == "reject":
        ok, msg = dashboard.reject(Path(a.dir), a.by, a.why)
        log(msg)
        dashboard.write()
        return 0 if ok else 1
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

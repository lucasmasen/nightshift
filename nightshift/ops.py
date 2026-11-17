"""Day-two operations: verify an approved fix on every case, publish the site, make a post card, compare harnesses,
and the gated go-live.

None of these touch the live phone line except golive, which needs two explicit flags and is meant to be run by a
person after reading the diff.
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path

from . import collect, config, cost, dashboard, export, fixer, guard, retest
from .hr import Client

DEPLOY = config.PROJECT / ".deploy"  # git-ignored; holds the Vercel project link (.vercel/) and the built site
REAL_DF = config.WORKSPACE / "demo-factory"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


# ---- verify: the approved prompt on every case ------------------------------------------------------------------
def baseline_path(s: dict) -> Path | None:
    """The night's baseline run file. Older nights only saved its name, so look it up in the test checkout."""
    if s.get("baseline_path"):
        return Path(s["baseline_path"])
    name = s.get("baseline_file") or ""
    if not name.endswith(".md"):  # real-call nights store "real calls" here
        return None
    return config.TEST_CHECKOUT / "demo_line" / "suite_runs" / name


def verify(night_dir: Path, log=print, reuse: Path | None = None, crash_retries: int = 2) -> dict:
    """Run every simulator case once with the night's proposed prompt and compare with the night's baseline run.
    About 178 calls (~$0.90): the step before anything goes live.

    Calls where the simulator itself crashed (network drop, API error) say nothing about the fix, so they're re-run,
    up to crash_retries times. `reuse` starts from a run file that already happened and only re-runs its crashes."""
    s = dashboard.load(night_dir)
    base_path = baseline_path(s)
    if base_path is None or not base_path.is_file():
        raise RuntimeError("This night has no baseline run file to compare with (real-call nights don't).")
    base = collect.parse(base_path)
    proposed = (night_dir / "prompt.proposed.md").read_text()
    spent = 0.0
    if reuse:
        after = collect.parse(reuse)
        log(f"Verify {night_dir.name}: reusing {reuse.name} ({after.total} calls ran, {len(after.crashed)} crashed)")
    else:
        log(f"Verify {night_dir.name}: every case once with the proposed prompt (about {base.total} calls)")
        r = retest.run_suite([], proposed, repeat=1, log=log, fresh=True)
        after, spent = r.run, spent + (cost.sim_cost(r.output) or 0.0)
    files = [after.path.name]
    calls = [c for c in after.calls if not c.crashed]
    todo = [c.case for c in after.crashed]
    for attempt in range(crash_retries):
        if not todo:
            break
        log(f"{len(todo)} calls crashed in the simulator (not the agent); re-running just those ({attempt + 1}/{crash_retries})")
        r = retest.run_suite(todo, proposed, repeat=1, log=log, fresh=True)
        spent += cost.sim_cost(r.output) or 0.0
        files.append(r.run.path.name)
        calls += [c for c in r.run.calls if not c.crashed]
        todo = [c.case for c in r.run.crashed]
    merged = collect.Run(path=after.path, passed=sum(c.passed for c in calls), total=len(calls), calls=calls)
    bc, ac = base.case_scores(), merged.case_scores()
    newly_failing = sorted(c for c, (p, t) in ac.items() if p < t and bc.get(c, (0, 0))[0] == bc.get(c, (0, 1))[1])
    newly_passing = sorted(c for c, (p, t) in ac.items() if p == t and bc.get(c, (1, 1))[0] < bc.get(c, (1, 1))[1])
    result = {"baseline": f"{base.passed}/{base.total}", "with_fix": f"{merged.passed}/{merged.total}",
              "baseline_rate": round(base.score, 3), "with_fix_rate": round(merged.score, 3),
              "newly_failing": newly_failing, "newly_passing": newly_passing, "file": files[0], "files": files,
              "still_crashed": todo, "cost_usd": round(spent, 3)}
    # Single calls are noisy: a case flipping once is a hint, not a verdict. Go-live needs the rate not to drop,
    # and nearly every case to have actually run.
    complete = merged.total >= 0.95 * base.total
    result["complete"] = complete
    result["ok_to_go_live"] = complete and merged.score >= base.score
    s["verify"] = result
    dashboard.save(night_dir, s)
    log(f"Baseline {result['baseline']} -> with fix {result['with_fix']}; newly failing: "
        f"{', '.join(newly_failing) or 'none'}"
        + ("" if complete else f"; INCOMPLETE: {len(todo)} calls never ran") + f"; ok to go live: {result['ok_to_go_live']}")
    return result


# ---- publish: the public site ------------------------------------------------------------------------------------
def publishable_nights(include_real: bool = False) -> list[str]:
    """Real nights with a fix. Real-call nights quote real callers (redacted), so they stay off unless asked for."""
    out = []
    for d, s in dashboard.nights():
        if (s.get("trace") or {}).get("where") == "mock" or not s.get("diff"):
            continue
        if s.get("mode") == "real" and not include_real:
            continue
        if s.get("mode") == "agent":  # other agents' transcripts: publish by hand after a read, never by default
            continue
        out.append(d.name)
    return out


def publish(log=print, include_real: bool = False) -> str:
    nights = publishable_nights(include_real)
    site, issues = export.build(nights)
    if issues:
        raise RuntimeError("Not publishing; the privacy scan held these back:\n  " + "\n  ".join(issues))
    link = DEPLOY / ".vercel"
    keep = None
    if link.exists():
        keep = Path(shutil.copytree(link, config.PROJECT / ".vercel-keep", dirs_exist_ok=True))
    shutil.rmtree(DEPLOY, ignore_errors=True)
    shutil.copytree(site, DEPLOY)
    if keep:
        shutil.copytree(keep, DEPLOY / ".vercel")
        shutil.rmtree(keep)
    p = subprocess.run(["vercel", "deploy", "--prod", "--yes"], cwd=DEPLOY, capture_output=True, text=True, timeout=600)
    url = next((ln.split()[-1] for ln in p.stdout.splitlines() if "Aliased" in ln), "")
    if p.returncode != 0 or not url:
        raise RuntimeError("Vercel deploy failed:\n" + (p.stdout + p.stderr)[-800:])
    log(f"Published {len(nights)} nights: {url}")
    return url


def _clip(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


# ---- card: a 1080x1350 image for a post ---------------------------------------------------------------------------
def card(night_dir: Path, day: int | None = None) -> Path:
    night_dir = night_dir.resolve()
    s = dashboard.load(night_dir)
    b, a = s.get("before") or {}, s.get("after") or {}
    if not a:
        raise RuntimeError("This night has no before/after proof yet.")
    pb, pa = b["passed"] / b["total"], a["passed"] / a["total"]
    summ = (s.get("agent_summary") or {}).get("summary", "")
    status = ("approved" if s.get("approved") else
              "rejected by its own proof" if str(s.get("verdict", "")).startswith("Don't") else "waiting for a person")
    head = f"day {day} · " if day else ""
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1080px;height:1350px;background:#17331F;color:#EDE6C8;font-family:-apple-system,system-ui,sans-serif}}
.w{{box-sizing:border-box;width:1080px;height:1350px;padding:100px 90px;display:flex;flex-direction:column}}
.t{{font-size:32px;color:#7BC67E;font-weight:600}} h1{{font-size:84px;line-height:1.05;margin:44px 0 30px;font-weight:800}}
.n{{display:flex;align-items:baseline;gap:30px}} .n b{{font-size:180px;font-weight:900;letter-spacing:-.04em}}
.n .a{{color:#7BC67E}} .n i{{font-style:normal;font-size:90px;opacity:.6}}
.s{{font-size:38px;margin-top:6px;opacity:.85}} p{{font-size:38px;line-height:1.3;margin:auto 0 0}} .f{{font-size:26px;opacity:.55;margin-top:30px}}
</style></head><body><div class="w"><div class="t">{head}nightshift · the big hack game · berkeley</div>
<h1>my ai fixed my other ai overnight.</h1>
<div class="n"><b>{pb:.0%}</b><i>→</i><b class="a">{pa:.0%}</b></div>
<div class="s">of fresh test calls passed. fix {html.escape(status)}.</div>
<p>{html.escape(_clip(summ, 190).lower())}</p>
<div class="f">simulated calls · sunset plumbing is a made-up demo shop · built on harnessrouter</div>
</div></body></html>"""
    src = night_dir / "card.html"
    src.write_text(page)
    out = night_dir / "card.png"
    subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1080,1350",
                    f"--screenshot={out}", src.as_uri()], capture_output=True, timeout=60, check=False)
    if not out.exists():
        raise RuntimeError("Chrome didn't render the card")
    return out


# ---- arena: the same failures, two harnesses ---------------------------------------------------------------------
def arena(night_dir: Path, bases: list[str], repeat: int = 4, log=print) -> dict:
    """Give the same failed calls to each harness base, safety-check each fix, and prove all of them on the same
    cases with the same number of fresh calls. Needs a model key for every base under Bring Your Own Key."""
    s = dashboard.load(night_dir)
    old = (night_dir / "prompt.before.md").read_text()
    cases = s.get("retest_cases") or (s.get("failed_cases", []) + ["book_basic", "reschedule_after_booking"])
    client = Client(config.api_key())
    results: dict = {"cases": cases, "repeat": repeat, "entries": {}}
    saved = (config.HARNESS_BASE, config.HARNESS_NAME)
    try:
        if repeat > 0:  # repeat 0: fixes only, prove later with `arena-prove`
            before = retest.run_suite(cases, old, repeat=repeat, log=log, fresh=True)
            results["before"] = {"passed": before.run.passed, "total": before.run.total}
        for base in bases:
            config.HARNESS_BASE, config.HARNESS_NAME = base, f"Nightshift (voice agent fixer, {base})"
            log(f"Arena: {base}")
            fix = fixer.run_harnessrouter(client, night_dir / "prompt.before.md", night_dir / "failures.md",
                                          config.TEST_CHECKOUT / config.SHOP_REL, log=log)
            entry = {"model": fix.trace.get("model"), "est_cost_usd": cost.fix_cost(fix.trace.get("usage"), fix.trace.get("model")),
                     "elapsed_s": fix.trace.get("elapsed_s"), "summary": (fix.summary or {}).get("summary"),
                     "harness_id": fix.trace.get("harness_id"), "guard": guard.check(old, fix.prompt),
                     "changed_lines": guard.changed_lines(old, fix.prompt)}
            (night_dir / f"arena.{base}.prompt.md").write_text(fix.prompt)
            if not entry["guard"] and repeat > 0:
                r = retest.run_suite(cases, fix.prompt, repeat=repeat, log=log, fresh=True)
                entry["after"] = {"passed": r.run.passed, "total": r.run.total}
            results["entries"][base] = entry
    finally:
        config.HARNESS_BASE, config.HARNESS_NAME = saved
    s["arena"] = results
    dashboard.save(night_dir, s)
    (night_dir / "arena.json").write_text(json.dumps(results, indent=2, default=str))
    return results


def arena_prove(night_dir: Path, repeat: int = 4, log=print) -> dict:
    """The proof half of an arena run whose fixes already exist (arena.<base>.prompt.md): same cases, fresh calls."""
    s = dashboard.load(night_dir)
    res = s.get("arena") or {}
    cases = res.get("cases") or []
    old = (night_dir / "prompt.before.md").read_text()
    before = retest.run_suite(cases, old, repeat=repeat, log=log, fresh=True)
    res["before"] = {"passed": before.run.passed, "total": before.run.total}
    res["repeat"] = repeat
    for base, e in res.get("entries", {}).items():
        f = night_dir / f"arena.{base}.prompt.md"
        if e.get("guard") or not f.exists():
            continue
        r = retest.run_suite(cases, f.read_text(), repeat=repeat, log=log, fresh=True)
        e["after"] = {"passed": r.run.passed, "total": r.run.total}
    s["arena"] = res
    dashboard.save(night_dir, s)
    (night_dir / "arena.json").write_text(json.dumps(res, indent=2, default=str))
    return res


# ---- golive: put an approved, verified prompt on the real phone line ----------------------------------------------
def golive(night_dir: Path, really: bool, log=print) -> int:
    """Copy the approved prompt into demo-factory and push it to Vapi with setup.py. A person runs this.
    Without --really it only shows what setup.py would change (--print)."""
    s = dashboard.load(night_dir)
    if not s.get("approved"):
        log("Not approved. Run `approve` first.")
        return 1
    v = s.get("verify") or {}
    if not v.get("ok_to_go_live"):
        log("Not verified on every case (or the full run scored lower). Run `verify` first.")
        return 1
    approved = Path(s["approved"]["file"]).read_text()
    real = REAL_DF / config.PROMPT_REL
    current = real.read_text()
    if current == approved:
        log("Already live in demo-factory.")
        return 0
    if current != (night_dir / "prompt.before.md").read_text() and not s.get("builds_on"):
        log("demo-factory's prompt changed since this night; run a new night instead.")
        return 1
    if not really:
        log(guard.diff_text(current, approved))
        log("Dry run. To go live (changes what real callers hear):\n"
            f"  python3 -m nightshift golive {night_dir} --really")
        return 0
    real.write_text(approved)
    (config.TEST_CHECKOUT / config.PROMPT_REL).write_text(approved)  # keep the test copy matching the real one
    p = subprocess.run([str(REAL_DF / ".venv" / "bin" / "python"), "demo_line/setup.py"], cwd=REAL_DF,
                       capture_output=True, text=True, timeout=600)
    log(p.stdout[-1500:] + p.stderr[-500:])
    if p.returncode != 0:
        real.write_text(current)
        (config.TEST_CHECKOUT / config.PROMPT_REL).write_text(current)
        log("setup.py failed; demo-factory's prompt.md was put back.")
        return 1
    s["live"] = {"at": __import__("datetime").datetime.now().isoformat(timespec="seconds")}
    dashboard.save(night_dir, s)
    log("Live. Commit demo-factory's prompt.md with a message naming this night.")
    return 0

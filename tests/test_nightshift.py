"""No network, no spend: python3 -m unittest discover -s tests"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nightshift import collect, fixer, guard, hr, report  # noqa: E402

RUN = """# Sunset Plumbing suite, 2026-11-16 01:00

**2 of 3 passed.** Model x.

| Case | Result | Notes |
|---|---|---|
| book_basic | PASS | fine |
| book_earliest | FAIL | two questions in one turn: 'Got it' |
| book_basic | PASS | fine again |

## Transcripts

### book_basic (PASS)

- **Godfrey:** hello one

### book_earliest (FAIL)

- **Godfrey:** Got it, Marco. Which day? And morning or afternoon?

### book_basic (PASS)

- **Godfrey:** hello two
"""

PROMPT = """# Godfrey
You told them you're the AI assistant and that the call is recorded.
Your name is $agent_name.

Rules go here.
"""


def write(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text)
    return p


class CollectTests(unittest.TestCase):
    def test_parse_matches_transcripts_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            run = collect.parse(write(Path(d), "r.md", RUN))
        self.assertEqual((run.passed, run.total), (2, 3))
        self.assertEqual([c.case for c in run.failures], ["book_earliest"])
        self.assertIn("Which day?", run.failures[0].transcript)
        self.assertIn("hello two", run.calls[2].transcript)
        self.assertEqual(run.case_scores(), {"book_basic": (2, 2), "book_earliest": (0, 1)})

    def test_reused_rows_count(self):
        text = RUN.replace("| book_earliest | FAIL", "| book_earliest (reused) | FAIL")
        with tempfile.TemporaryDirectory() as d:
            run = collect.parse(write(Path(d), "r.md", text))
        self.assertEqual(run.case_scores()["book_earliest"], (0, 1))

    def test_failures_markdown_has_reason_and_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            md = collect.failures_markdown(collect.parse(write(Path(d), "r.md", RUN)))
        self.assertIn("two questions in one turn", md)
        self.assertIn("Which day?", md)
        self.assertNotIn("hello one", md)


class GuardTests(unittest.TestCase):
    def test_small_fix_passes(self):
        self.assertEqual(guard.check(PROMPT, PROMPT + "Ask one question per turn.\n"), [])

    def test_disclosure_line_must_survive(self):
        bad = PROMPT.replace("and that the call is recorded", "")
        self.assertTrue(any("disclosure" in p for p in guard.check(PROMPT, bad)))

    def test_placeholder_must_survive(self):
        bad = PROMPT.replace("$agent_name", "Godfrey")
        self.assertTrue(any("Placeholders" in p for p in guard.check(PROMPT, bad)))

    def test_big_rewrite_rejected(self):
        bad = PROMPT + "\n".join(f"rule {i}" for i in range(40))
        self.assertTrue(any("limit" in p for p in guard.check(PROMPT, bad)))

    def test_no_change_and_empty_rejected(self):
        self.assertTrue(guard.check(PROMPT, PROMPT))
        self.assertTrue(guard.check(PROMPT, "  "))


class FakeHR:
    """Records requests and answers like the API reference says."""

    def __init__(self):
        self.calls = []
        self.polls = 0

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        path = url.replace("https://api.harnessrouter.ai", "")
        j = lambda obj, status=200: (status, {"Content-Type": "application/json"}, json.dumps(obj).encode())  # noqa: E731
        if path == "/v1/me":
            return j({"workspace_name": "Default Workspace"})
        if path == "/v1/harnesses" and method == "GET":
            return j({"harnesses": []})
        if path == "/v1/bases":
            return j({"bases": [{"id": "claude-code", "defaultModel": "claude-sonnet-4.6",
                                 "models": [{"id": "claude-sonnet-5.5", "available": True}]}]})
        if path == "/v1/harnesses" and method == "POST":
            return j({"id": "chrn_1"})
        if path == "/v1/files":
            return j({"id": f"file_{len(self.calls)}"})
        if path == "/v1/responses":
            return j({"id": "resp_1", "status": "in_progress"})
        if path == "/v1/responses/resp_1":
            self.polls += 1
            if self.polls < 2:
                return j({"id": "resp_1", "status": "in_progress", "output": []})
            return j({"id": "resp_1", "status": "completed", "model": "claude-sonnet-5.5",
                      "metadata": {"session_id": "hsess1"},
                      "output": [{"type": "message", "content": [{"type": "output_text",
                                  "text": 'Done.\n{"changed": true, "causes": 1, "summary": "one rule"}'}]}]})
        if path == "/v1/sessions/hsess1/files":
            return j({"files": [
                {"path": "prompt.md", "download_url": "https://api.harnessrouter.ai/v1/containers/hsess1/files/c1/content"},
                {"path": "nightshift-report.md", "download_url": "https://api.harnessrouter.ai/v1/containers/hsess1/files/c2/content"}]})
        if path == "/v1/sessions/hsess1":
            return j({"spend_usd": "0.42", "elapsed": 150.0})
        if path.endswith("/c1/content"):
            return 200, {"Content-Type": "text/markdown"}, (PROMPT + "Ask one question per turn.\n").encode()
        if path.endswith("/c2/content"):
            return 200, {"Content-Type": "text/markdown"}, b"# report"
        return j({"error": {"code": "not_found", "message": path}}, 404)


class HRTests(unittest.TestCase):
    def test_full_fix_round_trip(self):
        fake = FakeHR()
        client = hr.Client("sk-hr-test", transport=fake, sleep=lambda s: None)
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            fix = fixer.run_harnessrouter(client, write(t, "prompt.md", PROMPT), write(t, "failures.md", "x"),
                                          write(t, "shop.json", "{}"), log=lambda m: None)
        self.assertIn("Ask one question per turn.", fix.prompt)
        self.assertEqual(fix.report, "# report")
        self.assertEqual(fix.summary["causes"], 1)
        self.assertEqual(fix.trace["spend_usd"], 0.42)
        start = next(c for c in fake.calls if c[1].endswith("/v1/responses"))
        body = json.loads(start[3])
        self.assertTrue(body["background"])
        self.assertEqual(body["metadata"]["harness_id"], "chrn_1")
        self.assertIn("Idempotency-Key", start[2])
        created = json.loads(next(c for c in fake.calls if c[0] == "POST" and c[1].endswith("/v1/harnesses"))[3])
        self.assertEqual((created["base"], created["default_model"]), ("claude-code", "claude-sonnet-5.5"))
        self.assertTrue(all(c[2]["UHP-Version"] == "2026-09-28" for c in fake.calls))

    def test_key_never_sent_elsewhere(self):
        client = hr.Client("sk-hr-test", transport=FakeHR())
        with self.assertRaises(ValueError):
            client.download("https://evil.example.com/file")

    def test_error_envelope(self):
        client = hr.Client("sk-hr-test", transport=lambda *a: (401, {"Content-Type": "application/json"},
                           b'{"error": {"code": "invalid_credential", "message": "bad key"}}'))
        with self.assertRaises(hr.HRError) as e:
            client.me()
        self.assertEqual(e.exception.code, "invalid_credential")

    def test_final_text_uses_last_message(self):
        r = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "progress"}]},
                        {"type": "function_call"},
                        {"type": "message", "content": [{"type": "output_text", "text": "final"}]}]}
        self.assertEqual(hr.final_text(r), "final")


class MockAndReportTests(unittest.TestCase):
    def test_mock_fix_passes_guard(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            p = write(t, "prompt.md", PROMPT)
            fix = fixer.run_mock(p, write(t, "f.md", "two questions in one turn"), t / "shop.json", log=lambda m: None)
        self.assertEqual(guard.check(PROMPT, fix.prompt), [])

    def test_report_renders_and_escapes(self):
        page = report.render({"date": "x", "verdict": "Ready <b>", "failed_cases": ["a"], "diff": "+new\n-old",
                              "before": {"passed": 1, "total": 3, "cases": {"a": (0, 3)}},
                              "after": {"passed": 3, "total": 3, "cases": {"a": (3, 3)}}})
        self.assertIn("Ready &lt;b&gt;", page)
        self.assertIn("1/3 (33%)", page)
        self.assertIn('class="add"', page)


if __name__ == "__main__":
    unittest.main()


class DashboardTests(unittest.TestCase):
    def test_approve_gate_and_dashboard(self):
        from nightshift import config, dashboard
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            old_runs, old_project = config.RUNS, dashboard.APPROVED
            config.RUNS, dashboard.APPROVED = t / "runs", t / "approved"
            try:
                night = config.RUNS / "2026-11-16-010000"
                night.mkdir(parents=True)
                (night / "prompt.proposed.md").write_text("new prompt")
                s = {"verdict": "Not better", "baseline_passed": 7, "baseline_total": 10, "failed_cases": ["a"]}
                (night / "summary.json").write_text(json.dumps(s))
                ok, _ = dashboard.approve(night, "lucas")
                self.assertFalse(ok)  # not ready, not forced
                s["verdict"] = "Ready for approval: 1/3 to 3/3"
                (night / "summary.json").write_text(json.dumps(s))
                ok, msg = dashboard.approve(night, "lucas")
                self.assertTrue(ok)
                self.assertEqual((dashboard.APPROVED / "2026-11-16-010000.prompt.md").read_text(), "new prompt")
                self.assertIn("Approved by lucas", (night / "report.html").read_text())
                page = dashboard.write().read_text()
                self.assertIn("approved", page)
                self.assertIn("7/10", page)
            finally:
                config.RUNS, dashboard.APPROVED = old_runs, old_project


class StatsTests(unittest.TestCase):
    def test_fisher(self):
        from nightshift import stats
        self.assertAlmostEqual(stats.fisher_improved(23, 32, 29, 32), 0.053, places=2)
        self.assertEqual(stats.fisher_improved(5, 5, 5, 5), 1.0)
        self.assertLess(stats.fisher_improved(0, 10, 10, 10), 0.001)
        self.assertGreater(stats.fisher_improved(9, 10, 5, 10), 0.9)

    def test_verdict(self):
        from nightshift.__main__ import verdict
        s = {"before": {"passed": 23, "total": 32}, "after": {"passed": 29, "total": 32}, "worse": [], "broke": []}
        self.assertTrue(verdict(s).startswith("Ready"))
        s["broke"] = ["book_basic"]
        self.assertTrue(verdict(s).startswith("Better"))
        s = {"before": {"passed": 30, "total": 32}, "after": {"passed": 31, "total": 32}}
        self.assertIn("could be luck", verdict(s))


class ReviewFixTests(unittest.TestCase):
    """Regression tests for the 11/16 bug review."""

    def test_lookalike_hosts_refused(self):
        client = hr.Client("sk-hr-test", transport=FakeHR())
        for url in ("https://api.harnessrouter.ai.evil.com/x", "https://api.harnessrouter.ai@evil.com/x",
                    "http://api.harnessrouter.ai/x"):
            with self.assertRaises(ValueError):
                client.request("GET", url)

    def test_redirect_drops_key_on_new_host(self):
        import urllib.request
        h = hr._NoKeyOnRedirect()
        req = urllib.request.Request("https://api.harnessrouter.ai/v1/x", headers={"Authorization": "Bearer k"})
        new = h.redirect_request(req, None, 302, "Found", {}, "https://storage.example.com/f")
        self.assertNotIn("Authorization", new.headers)
        same = h.redirect_request(req, None, 302, "Found", {}, "https://api.harnessrouter.ai/v1/y")
        self.assertIn("Authorization", same.headers)

    def test_network_error_is_retried(self):
        calls = []

        def flaky(method, url, headers, body):
            calls.append(1)
            if len(calls) < 3:
                return 599, {}, b'{"error": {"code": "network_error", "message": "x"}}'
            return 200, {"Content-Type": "application/json"}, b'{"workspace_name": "W"}'
        self.assertEqual(hr.Client("k", transport=flaky, sleep=lambda s: None).me()["workspace_name"], "W")

    def test_json_looking_file_stays_bytes(self):
        client = hr.Client("k", transport=lambda *a: (200, {"Content-Type": "text/markdown"}, b"{not json"))
        self.assertEqual(client.download("https://api.harnessrouter.ai/v1/containers/s/files/f/content"), b"{not json")

    def test_reword_counts_once(self):
        old = "\n".join(f"line {i}" for i in range(20))
        new = "\n".join(f"LINE {i}" for i in range(20))
        self.assertEqual(guard.changed_lines(old, new), 20)
        self.assertEqual(guard.check(old, new), [])

    def test_unequal_totals_inconclusive(self):
        from nightshift.__main__ import verdict
        s = {"before": {"passed": 10, "total": 20}, "after": {"passed": 15, "total": 16}}
        self.assertTrue(verdict(s).startswith("Inconclusive"))

    def test_zero_guards(self):
        from nightshift.__main__ import pick_guard_cases
        with tempfile.TemporaryDirectory() as d:
            run = collect.parse(write(Path(d), "r.md", RUN))
        self.assertEqual(pick_guard_cases(run, ["book_earliest"], 0), [])


class BigDropTests(unittest.TestCase):
    def test_big_drop_blocks_approval(self):
        from nightshift.__main__ import verdict
        s = {"before": {"passed": 19, "total": 32, "cases": {"cut_hand_medical": [2, 4], "co_alarm": [0, 4]}},
             "after": {"passed": 26, "total": 32, "cases": {"cut_hand_medical": [0, 4], "co_alarm": [4, 4]}}}
        self.assertTrue(verdict(s).startswith("Don't approve: cut_hand_medical"))


class RealCallTests(unittest.TestCase):
    REPORT = """# Real-call QA

**2 call(s)**

## 11/08 6:06PM · call 01a1be0e · 138s · ended: customer-ended-call

- **number_question_twice**: asked twice
    - Godfrey: Is the number you're calling from good?
- **emergency_no_address**: no address
- **review_call**: read it

## 11/09 6:22PM · call 01a1c343 · 44s · ended: x

- **review_call**: average reply time 2.29s
"""

    def test_parse_counts_behavior_flags_only(self):
        from nightshift import realcalls
        calls, flagged, counts = realcalls.parse(self.REPORT)
        self.assertEqual((calls, flagged), (2, 1))
        self.assertEqual(counts, {"number_question_twice": 1, "emergency_no_address": 1})

    def test_twins_map_to_known_cases(self):
        from nightshift import realcalls
        cases = realcalls.twins({"emergency_no_address": 2, "number_question_twice": 1}, 3)
        self.assertEqual(cases, ["burst_pipe", "fast_address", "no_caller_id"])


class FixedFlagTests(unittest.TestCase):
    def test_fixed_flags_stop_at_left_alone(self):
        from nightshift import realcalls
        rep = "## Cause 1\n(`hung_up_first`)\n## Cause 2\n`invented_name`\n## Failures left alone\n`on_the_way`\n"
        self.assertEqual(realcalls.fixed_flags(rep), ["hung_up_first", "invented_name"])


class CostTests(unittest.TestCase):
    def test_fix_cost_from_tokens(self):
        from nightshift import cost
        u = {"input_tokens": 12, "output_tokens": 6213, "cache_write_tokens": 31506, "cache_read_tokens": 138178}
        self.assertAlmostEqual(cost.fix_cost(u, "claude-sonnet-5-5"), 0.1686, places=3)
        self.assertAlmostEqual(cost.fix_cost(u, "anthropic/claude-sonnet-5.5"), 0.1686, places=3)
        self.assertIsNone(cost.fix_cost(u, "gpt-6.1-sol"))

    def test_sim_cost_prefers_measured(self):
        from nightshift import cost
        out = "Paid calls: 32, estimated $0.16, measured $0.200 from tokens (0.63¢ a call)."
        self.assertEqual(cost.sim_cost(out), 0.2)
        self.assertEqual(cost.sim_cost("Paid calls: 4, estimated $0.02. Reused: 0"), 0.02)


class OpsTests(unittest.TestCase):
    def test_golive_needs_approval_and_verify(self):
        from nightshift import ops
        with tempfile.TemporaryDirectory() as d:
            n = Path(d) / "night"
            n.mkdir()
            (n / "summary.json").write_text(json.dumps({"verdict": "Ready for approval"}))
            msgs = []
            self.assertEqual(ops.golive(n, really=True, log=msgs.append), 1)
            self.assertIn("Not approved", msgs[-1])
            (n / "summary.json").write_text(json.dumps({"approved": {"file": "x"}, "verify": {"ok_to_go_live": False}}))
            self.assertEqual(ops.golive(n, really=True, log=msgs.append), 1)
            self.assertIn("Not verified", msgs[-1])

    def test_crashed_calls_are_not_failures(self):
        # 11/17: the wifi dropped mid-verify and 83 crashed calls were scored as fails (140/178 -> "81/178")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "run.md"
            f.write_text("**1 of 3 passed.**\n\n| Case | Result | Notes |\n|---|---|---|\n"
                         "| a | PASS | fine |\n| b | FAIL | two questions in one turn |\n"
                         "| c | FAIL | simulator failed: ^^^^^^\n    raise APIConnectionError(request=request) from err\n"
                         "anthropic.APIConnectionError: Connection error.\n; judge:  |\n\n## Transcripts\n")
            run = collect.parse(f)
            self.assertEqual((run.passed, run.total), (1, 2))
            self.assertEqual([c.case for c in run.failures], ["b"])
            self.assertEqual([c.case for c in run.crashed], ["c"])
            self.assertNotIn("c", run.case_scores())

    def test_baseline_path_for_old_and_real_nights(self):
        from nightshift import config, ops
        # night 1 only saved the file name; an empty path used to resolve to "." and crash verify
        self.assertEqual(ops.baseline_path({"baseline_file": "2026-11-16-173654.md"}),
                         config.TEST_CHECKOUT / "demo_line" / "suite_runs" / "2026-11-16-173654.md")
        self.assertIsNone(ops.baseline_path({"baseline_file": "real calls"}))
        self.assertIsNone(ops.baseline_path({}))
        self.assertEqual(ops.baseline_path({"baseline_path": "/x/y.md"}), Path("/x/y.md"))

    def test_clip(self):
        from nightshift import ops
        self.assertEqual(ops._clip("one two three four", 9), "one two…")
        self.assertEqual(ops._clip("short", 9), "short")


class AnyAgentTests(unittest.TestCase):
    SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "bayview-dental"

    def test_load_sample(self):
        from nightshift import anyagent
        a = anyagent.load(self.SAMPLE)
        self.assertEqual(len(a.calls), 5)
        self.assertTrue(a.facts)
        self.assertIn("## Call 02-swollen-face", anyagent.calls_markdown(a))

    def test_safety_protects_lines_and_variables(self):
        from nightshift import anyagent
        a = anyagent.load(self.SAMPLE)
        bad = a.prompt.replace("This call is recorded.", "").replace("{{office_name}}", "the office")
        problems = " ".join(anyagent.safety_problems(a, bad))
        self.assertIn("protected line", problems)
        self.assertIn("{{office_name}}", problems)
        ok = a.prompt.replace("6. Be warm and brief.", "6. Be warm and brief. Never give medicine doses.")
        self.assertEqual(anyagent.safety_problems(a, ok), [])

    def test_missing_files_refused(self):
        from nightshift import anyagent
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                anyagent.load(Path(d))
            (Path(d) / "prompt.md").write_text("x")
            with self.assertRaises(ValueError):
                anyagent.load(Path(d))

    def test_mock_night_end_to_end(self):
        import argparse
        from nightshift import __main__ as cli, config, lessons
        with tempfile.TemporaryDirectory() as d:
            old_runs, old_lessons = config.RUNS, lessons.LESSONS
            config.RUNS, lessons.LESSONS = Path(d) / "runs", Path(d) / "lessons.md"
            try:
                rc = cli.agent_night(argparse.Namespace(dir=str(self.SAMPLE), mock=True))
                self.assertEqual(rc, 0)
                night = next(config.RUNS.glob("2*"))
                s = json.loads((night / "summary.json").read_text())
                self.assertEqual(s["mode"], "agent")
                self.assertEqual(s["guard_problems"], [])
                self.assertTrue(s["verdict"].startswith("Proposed, not proven: 1 of 5"), s["verdict"])
                self.assertIn("Never promise a callback", (night / "prompt.proposed.md").read_text())
                self.assertTrue((night / "report.html").exists())
            finally:
                config.RUNS, lessons.LESSONS = old_runs, old_lessons


class RecheckTests(unittest.TestCase):
    def test_recheck_calls_each_case(self):
        from nightshift import collect, ops, retest
        with tempfile.TemporaryDirectory() as d:
            night = Path(d) / "2026-11-16-000000"
            night.mkdir()
            (night / "prompt.before.md").write_text("old")
            (night / "prompt.proposed.md").write_text("new")
            (night / "summary.json").write_text(json.dumps({"verify": {"newly_failing": ["kid", "joke"]}}))

            def fake(cases, prompt, repeat, log, fresh):
                score = {"old": {"kid": 4, "joke": 3}, "new": {"kid": 0, "joke": 2}}[prompt]
                calls = [collect.Call(c, i < score[c], "") for c in cases for i in range(repeat)]
                run = collect.Run(night / f"{prompt}.md", sum(x.passed for x in calls), len(calls), calls)
                return retest.Result(run, "")
            real, retest.run_suite = retest.run_suite, fake
            try:
                res = ops.recheck(night, log=lambda m: None)
            finally:
                retest.run_suite = real
            self.assertEqual(res["cases"]["kid"]["call"], "really worse")
            self.assertEqual(res["cases"]["joke"]["call"], "worse, could be noise")
            self.assertEqual(res["really_worse"], ["kid"])


class RehearseTests(unittest.TestCase):
    def test_notes_parse_and_stay_private(self):
        from nightshift import stage
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "pitch.md"
            f.write_text("# x\n## The 3-minute pitch (y)\n\n**[Slide 1: hi]**\nI'm Lucas.\n\n**[Slide 5: proof]**\n"
                         "79 to 86. *(pause)*\n\n## Submission video\n**[Slide 6: no]**\nnot this\n")
            notes = stage.pitch_notes(f)
            self.assertEqual(notes[0], "I'm Lucas.")
            self.assertEqual(notes[4], "79 to 86. *(pause)*")
            self.assertEqual(notes[5], "")
        night = Path(__file__).resolve().parent.parent / "runs" / "2026-11-16-184822"
        if (night / "summary.json").exists():
            self.assertNotIn("NOTES=", stage.render(night))
            self.assertIn("NOTES=", stage.render(night, rehearse=True))


class NightlyMailTests(unittest.TestCase):
    def test_render_counts_only_waiting_fixes(self):
        from nightshift import mail
        items = [
            {"title": "a", "night": "2026-11-18-020000", "summary": {"verdict": "Ready for approval: 1/3 to 3/3",
             "before": {"passed": 1, "total": 3}, "after": {"passed": 3, "total": 3},
             "diff": "--- x\n+++ y\n-old\n+new\n+more", "agent_summary": {"summary": "fixed <it>"}}},
            {"title": "b", "night": "2026-11-17-020000", "summary": {"verdict": "Ready", "approved": {"by": "lucas"}}},
            {"title": "c", "night": None, "summary": None, "note": "Skipped: today's $2 test budget was already used."},
        ]
        subject, text, body = mail.render(items, "2026-11-18")
        self.assertEqual(subject, "Nightshift: 1 fix waiting for you")
        self.assertIn("2 lines of the instructions changed.", text)
        self.assertIn("Skipped: today's $2", text)
        self.assertIn("fixed &lt;it&gt;", body)
        self.assertIn("127.0.0.1:8765/night/2026-11-18-020000", body)

    def test_real_call_story_in_email(self):
        from nightshift import mail
        item = {"title": "Real calls", "night": "2026-11-18-020000", "summary": {
            "mode": "real", "baseline_total": 3, "baseline_passed": 1, "verdict": "Ready for approval: 5/8 to 8/8",
            "twin_tests": ["real_20261118_abc123"], "agent_summary": {"summary": "stopped the number loop"}}}
        _, text, _ = mail.render([item], "2026-11-18")
        self.assertIn("3 real calls to the demo line, 2 went wrong.", text)
        self.assertIn("real_20261118_abc123", text)
        self.assertIn("stopped the number loop", text)

    def test_send_without_gmail_saves_file(self):
        import os
        from nightshift import config, mail
        with tempfile.TemporaryDirectory() as d:
            old, saved_env = config.RUNS, {k: os.environ.pop(k, None) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD")}
            old_load, old_notify = config.load_env, mail.notify
            config.RUNS, config.load_env, mail.notify = Path(d), (lambda *a, **k: None), (lambda *a: None)
            try:
                where = mail.send("s", "t", "<p>b</p>", "2026-11-18", log=lambda m: None)
                self.assertTrue(where.startswith("file:"))
                self.assertTrue((Path(d) / "digest-2026-11-18.html").exists())
            finally:
                config.RUNS, config.load_env, mail.notify = old, old_load, old_notify
                for k, v in saved_env.items():
                    if v is not None:
                        os.environ[k] = v

    def test_nightly_item_notes(self):
        from nightshift import nightly
        self.assertIn("budget", nightly._item("x", 1, "Refused by the $2/day cap", None)["note"])
        self.assertIn("Didn't run", nightly._item("x", 1, "boom\nlast line", None)["note"])


class ReplayTests(unittest.TestCase):
    def test_cases_from_report(self):
        from nightshift import replay
        report = ("# r\n## Calls that went wrong\n### Call 02-x\nBad moment: \"the dentist will call you back tonight.\"\n"
                  "Nobody can be paged.\n### Call 09-missing\nBad moment: \"not in any transcript\"\n"
                  "## Calls judged fine\n### Call 01\nBad moment: \"ignored\"\n")
        calls = {"02-x": "Ava: Hi.\nCaller: my face is swollen\nAva: Ok, the dentist will call you back tonight.\n",
                 "09-missing": "Ava: Hi.\n", "01": "Ava: Hi.\nAva: ignored\n"}
        cases = replay.cases_from_report(report, calls)
        self.assertEqual([c["id"] for c in cases], ["02-x"])
        self.assertEqual(cases[0]["context"], "Ava: Hi.\nCaller: my face is swollen")
        self.assertIn("Nobody can be paged", cases[0]["why"])

    def test_verdicts(self):
        import subprocess
        from nightshift import replay

        def fake(old_bad, new_bad):
            out = json.dumps({"cases": [{"id": "a", "samples": 6, "old_bad": old_bad, "new_bad": new_bad}], "usage": {}})
            return lambda *a, **k: subprocess.CompletedProcess(a, 0, out, "")
        real = subprocess.run
        try:
            subprocess.run = fake(0, 0)
            self.assertIn("can't tell", replay.run("o", "n", [{"id": "a"}], log=lambda m: None)["verdict"])
            subprocess.run = fake(6, 0)
            self.assertIn("came back 6/6", replay.run("o", "n", [{"id": "a"}], log=lambda m: None)["verdict"])
            subprocess.run = fake(2, 3)
            self.assertIn("not better", replay.run("o", "n", [{"id": "a"}], log=lambda m: None)["verdict"])
        finally:
            subprocess.run = real


class VapiImportTests(unittest.TestCase):
    def test_transcript_and_prompt(self):
        from nightshift import vapi_import
        call = {"artifact": {"messages": [
            {"role": "system", "message": "secret instructions"},
            {"role": "bot", "message": "Hi, this is Ava."},
            {"role": "user", "message": "my pipe burst"},
            {"role": "tool_calls", "toolCalls": [{"function": {"name": "alert", "arguments": "{}"}}]},
            {"role": "tool_call_result", "result": "ALERT FAILED"},
            {"role": "user", "message": ""}]}}
        self.assertEqual(vapi_import.transcript(call, "Ava"),
                         "Ava: Hi, this is Ava.\nCaller: my pipe burst\n[alert: {}]\n[tool result: ALERT FAILED]")
        a = {"model": {"messages": [{"role": "system", "content": "Be kind."}, {"role": "user", "content": "x"}]}}
        self.assertEqual(vapi_import.system_prompt(a), "Be kind.")


class SeenCallsTests(unittest.TestCase):
    def test_drop_calls(self):
        from nightshift import realcalls
        rep = "# head\n## 11/17 · call aaaaaaaa11 · 10s\n- x\n## 11/18 · call bbbbbbbb22 · 20s\n- y\n"
        out = realcalls.drop_calls(rep, {"aaaaaaaa"})
        self.assertNotIn("aaaaaaaa", out)
        self.assertIn("bbbbbbbb22", out)
        self.assertEqual(realcalls.parse(out)[0], 1)
        self.assertEqual(realcalls.drop_calls(rep, set()), rep)


class TwinTests(unittest.TestCase):
    def test_twin_from_real_call(self):
        from nightshift import twins
        full = ("## Call abc12345 (2026-11-17T21:55 UTC)\n\nGodfrey: Hi, I'm an AI, this call is recorded.\n"
                "Caller: I smell gas. This is [NAME].\n[alert_on_call_plumber: {\"kind\": \"gas\"}]\n"
                "Godfrey: Get out now.\nCaller: We're out. Call me at [PHONE].\nCaller: \nGodfrey: Bye now.\n")
        report = ("# Nightshift report\nabc12345 overview\n## Cause 1: bye now too early\n- Failed case: abc12345\n"
                  "- Bad moment: Godfrey: \"Bye now.\" to [NAME]\n## Cause 2: other call\n- Failed case: zzz\n")
        name, case = twins.build("abc12345", twins.by_call(full)["abc12345"], report, "2026-11-17")
        self.assertEqual(name, "real_20261117_abc12345")
        self.assertEqual(case["lines"], ["I smell gas. This is Sam.", "We're out. Call me at 415 555 0123."])
        self.assertEqual(case["expect"], ["alert", "gas_safety"])
        self.assertIn("bye now too early", case["judge"])
        self.assertNotIn("other call", case["judge"])
        self.assertNotIn("[NAME]", case["judge"])
        self.assertNotIn("Nightshift report", case["judge"])

    def test_save_merges_without_overwriting(self):
        from nightshift import twins
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "suite_extra.json"
            self.assertEqual(twins.save({"a": {"lines": ["x"]}}, f), ["a"])
            self.assertEqual(twins.save({"a": {"lines": ["changed"]}, "b": {"lines": ["y"]}}, f), ["b"])
            data = json.loads(f.read_text())
            self.assertEqual(data["a"]["lines"], ["x"])
            self.assertIn("b", data)


class RealStageTests(unittest.TestCase):
    def test_real_call_slide_picks_matching_cause(self):
        from nightshift import stage
        fails = ("# flags\n# Full calls (redacted)\n\n## Call abcd1234 (x)\n\nGodfrey: Hi, I'm an AI.\n"
                 "Caller: gas!\n[alert: {}]\nGodfrey: Get out.\nCaller: no number\n"
                 "Godfrey: What's the best number to reach you?\nCaller: no\nGodfrey: What's the best number again?\n")
        report = ("## Cause 1: hung up\n- \"Bye now.\"\n## Cause 2: re-asking the number\n"
                  "- \"What's the best number to reach you?\" and \"What's the best number again?\"\n"
                  "## Not fixed\n- \"Get out.\" is fine\n")
        case, why, lines = stage.first_real_failure(fails, report)
        self.assertEqual(case, "real call abcd1234")
        self.assertEqual(why, "re-asking the number")
        self.assertEqual(sum(ln.startswith("!") for ln in lines), 2)
        self.assertFalse(any("Get out" in ln and ln.startswith("!") for ln in lines))


class LockTests(unittest.TestCase):
    def test_second_run_waits_for_the_first(self):
        import threading
        import time
        from nightshift import config, retest
        old = config.TEST_CHECKOUT
        with tempfile.TemporaryDirectory() as d:
            config.TEST_CHECKOUT = Path(d)
            order, msgs = [], []

            def first():
                with retest.checkout_lock(log=lambda m: None):
                    order.append("a-in")
                    time.sleep(0.6)
                    order.append("a-out")
            try:
                t = threading.Thread(target=first)
                t.start()
                time.sleep(0.15)
                with retest.checkout_lock(log=msgs.append):
                    order.append("b-in")
                t.join()
            finally:
                config.TEST_CHECKOUT = old
        self.assertEqual(order, ["a-in", "a-out", "b-in"])
        self.assertIn("waiting", msgs[0])


class PreflightTests(unittest.TestCase):
    def test_offline_copy_is_self_contained(self):
        from nightshift import config, preflight
        night = Path(__file__).resolve().parent.parent / "runs" / "2026-11-16-184822"
        if not (night / "summary.json").exists():
            self.skipTest("no local night to copy")
        with tempfile.TemporaryDirectory() as d:
            old = preflight.OFFLINE
            preflight.OFFLINE = Path(d) / "offline"
            try:
                index = preflight.build_offline([night])
                page = (preflight.OFFLINE / night.name / "stage.html").read_text()
                self.assertIn("stage.html", index.read_text())
                self.assertNotIn("http://", page.replace("http://www.w3.org", ""))
                self.assertIn("NOTES=", page)  # rehearse notes are in the offline copy (it stays on this Mac)
            finally:
                preflight.OFFLINE = old


class HomepageRealCallTests(unittest.TestCase):
    def test_story_uses_counts_not_transcripts(self):
        from nightshift import config, export
        with tempfile.TemporaryDirectory() as d:
            old = config.RUNS
            config.RUNS = Path(d)
            try:
                n = Path(d) / "2026-11-18-020000"
                n.mkdir()
                (n / "summary.json").write_text(json.dumps({
                    "mode": "real", "diff": "+x", "baseline_total": 2, "baseline_passed": 1,
                    "agent_summary": {"summary": "stopped <the> loop"}, "twin_tests": ["real_x"],
                    "agent_report": "Caller: my name is SECRET", "before": {"passed": 3, "total": 8},
                    "after": {"passed": 7, "total": 8}}))
                html_out = export.real_call_story()
            finally:
                config.RUNS = old
        self.assertIn("2 real calls to the demo line, 1 went wrong", html_out)
        self.assertIn("stopped &lt;the&gt; loop", html_out)
        self.assertIn("3/8 before, 7/8 after", html_out)
        self.assertNotIn("SECRET", html_out)


class AgentRegistryTests(unittest.TestCase):
    def test_register_and_folder_only_runs_when_new(self):
        import time
        from nightshift import agents
        with tempfile.TemporaryDirectory() as d:
            old = agents.DIR
            agents.DIR = Path(d) / "agents"
            try:
                folder = Path(d) / "acme"
                (folder / "calls").mkdir(parents=True)
                (folder / "prompt.md").write_text("p")
                (folder / "calls" / "1.txt").write_text("Agent: hi\nCaller: hey\n")
                agents.register("acme", folder=str(folder))
                with self.assertRaises(ValueError):
                    agents.register("x", vapi="a", key_env="sk-live-123")
                with self.assertRaises(ValueError):
                    agents.register("Bad Name", folder=str(folder))
                self.assertIn("acme", agents.registered())
                entry = agents.registered()["acme"]
                self.assertEqual(agents.prepare("acme", entry, log=lambda m: None), folder.resolve())
                self.assertIsNone(agents.prepare("acme", entry, log=lambda m: None))  # nothing new
                time.sleep(0.05)
                (folder / "calls" / "2.txt").write_text("Agent: hi\nCaller: again\n")
                self.assertEqual(agents.prepare("acme", entry, log=lambda m: None), folder.resolve())
            finally:
                agents.DIR = old


class StatusTests(unittest.TestCase):
    def test_next_at_and_waiting(self):
        from datetime import datetime
        from nightshift import status
        self.assertEqual(status.next_at(2, now=datetime(2026, 11, 17, 15, 0)), datetime(2026, 11, 18, 2, 0))
        self.assertEqual(status.next_at(2, now=datetime(2026, 11, 18, 1, 0)), datetime(2026, 11, 18, 2, 0))
        nights = [(Path("a"), {"verdict": "Ready for approval"}), (Path("b"), {"verdict": "Ready", "approved": {"by": "lucas"}}),
                  (Path("c"), {"verdict": "Don't approve"}),
                  (Path("d"), {"verdict": "Fix proposed", "trace": {"where": "mock"}})]
        self.assertEqual([d.name for d, _ in status.waiting(nights)], ["a"])


class BulletproofTests(unittest.TestCase):
    def test_crashed_calls_are_rerun_until_counts_match(self):
        from nightshift import collect, retest
        attempts = []

        def fake(cases, prompt, repeat, log, fresh):
            attempts.append(list(cases))
            if len(attempts) == 1:  # first pass: 2 of 4 calls crash (wifi)
                calls = [collect.Call("a", True, "ok"), collect.Call("a", False, "simulator failed: conn"),
                         collect.Call("b", True, "ok"), collect.Call("b", False, "simulator failed: conn")]
            else:  # the re-run of just the crashed ones works
                calls = [collect.Call(c, True, "ok") for c in cases]
            run = collect.Run(Path("x.md"), sum(c.passed for c in calls), len(calls), calls)
            return retest.Result(run, "")
        real = retest.run_suite
        retest.run_suite = fake
        try:
            r = retest.run_suite_complete(["a", "b"], "p", repeat=2, log=lambda m: None)
        finally:
            retest.run_suite = real
        self.assertEqual(attempts, [["a", "b"], ["a", "b"]])
        self.assertEqual((r.run.passed, r.run.total), (4, 4))

    def test_pending_proofs_only_real_unproven_fixes(self):
        from nightshift import config, nightly
        with tempfile.TemporaryDirectory() as d:
            old = config.RUNS
            config.RUNS = Path(d)
            try:
                for name, s in {
                    "2026-11-18-010000": {"diff": "+x", "retest_cases": ["a"], "mode": "real"},          # pending
                    "2026-11-18-020000": {"diff": "+x", "retest_cases": ["a"], "after": {"passed": 1}},  # proven
                    "2026-11-18-030000": {"diff": "+x", "retest_cases": ["a"], "guard_problems": ["no"]},
                    "2026-11-18-040000": {"diff": "+x", "failed_cases": ["a"], "trace": {"where": "mock"}},
                    "2026-11-18-050000": {"diff": "+x", "mode": "agent", "failed_cases": []},
                    "2026-11-18-060000": {"diff": "+x", "failed_cases": ["a"], "rejected": {"why": "no"}},
                }.items():
                    (Path(d) / name).mkdir()
                    (Path(d) / name / "summary.json").write_text(json.dumps(s))
                self.assertEqual([p.name for p in nightly.pending_proofs()], ["2026-11-18-010000"])
            finally:
                config.RUNS = old


class NameLearningTests(unittest.TestCase):
    def test_answer_to_name_question_and_said_back(self):
        from nightshift import realcalls as r
        t = ("Godfrey: Can you tell me your name so I can get the right plumber?\nCaller: Joe. John.\n"
             "Godfrey: Thanks, John. Is it still running?\nCaller: Man, yes. Call 911.\n"
             "Godfrey: Okay, Priya, stay out of the water.\n")
        self.assertEqual(r.learn_names(t), {"Joe", "John", "Priya"})
        self.assertNotIn("Man", r.learn_names(t))
        out = r.scrub_names(t, r.learn_names(t))
        self.assertNotIn("John", out)
        self.assertIn("Caller: [NAME]. [NAME].", out)

    def test_labels_survive(self):
        from nightshift import realcalls as r
        t = "Caller: hi\n- **Godfrey:** hello\nAgent: x\n"
        self.assertEqual(r._restore_labels(r._protect_labels(t)), t)
        self.assertNotIn("Caller:", r._protect_labels(t))


class OutgoingCheckTests(unittest.TestCase):
    def test_scrubs_transcripts_only(self):
        from nightshift import fixer
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "failures.md"
            f.write_text("Godfrey: What's your name?\nCaller: Dana.\nGodfrey: Thanks, Dana.\n"
                         "Caller: 415 867 5309, or the test one 415-555-0123\n")
            out = fixer.outgoing_check(f, log=lambda m: None)
            self.assertEqual(out.name, "failures.sent.md")
            sent = out.read_text()
            self.assertNotIn("Dana", sent)
            self.assertNotIn("867", sent)
            self.assertIn("415-555-0123", sent)  # fictional range kept
            p = Path(d) / "prompt.before.md"
            p.write_text("Call the shop at 415 867 5309.")
            self.assertEqual(fixer.outgoing_check(p, log=lambda m: None), p)  # instructions never rewritten
            clean = Path(d) / "calls.md"
            clean.write_text("Caller: [NAME] here, 415 555 0199\n")
            self.assertEqual(fixer.outgoing_check(clean, log=lambda m: None), clean)


class GuardHardeningTests(unittest.TestCase):
    def test_rejects_new_numbers_and_scrub_labels(self):
        from nightshift import guard
        old = "You are an AI assistant and this call is recorded.\nExample: 415 555 0199.\nBe kind.\n"
        ok = old.replace("Be kind.", "Be kind. Read 415-555-0199 back digit by digit.")
        self.assertEqual(guard.check(old, ok), [])
        bad = old.replace("Be kind.", "Be kind. If stuck, call 415 867 5309.")
        self.assertTrue(any("phone number" in p and "**09" in p for p in guard.check(old, bad)))
        bad2 = old.replace("Be kind.", "Be kind. Say 'Thanks, [NAME]'.")
        self.assertTrue(any("[NAME]" in p for p in guard.check(old, bad2)))


class CombineTests(unittest.TestCase):
    def test_merge_clean_and_conflict(self):
        from nightshift import combine
        base = "a\nb\nc\nd\ne\nf\ng\n"
        one = base.replace("b\n", "b1\n")
        two = base.replace("f\n", "f2\n")
        self.assertEqual(combine.merge(base, [one, two]), "a\nb1\nc\nd\ne\nf2\ng\n")
        clash = base.replace("b\n", "bX\n")
        with self.assertRaises(combine.Conflict):
            combine.merge(base, [one, clash])

    def test_new_night_dir_skips_taken_second(self):
        from nightshift import combine, config
        with tempfile.TemporaryDirectory() as d:
            old = config.RUNS
            config.RUNS = Path(d)
            try:
                a = combine.new_night_dir()
                a.mkdir()
                b = combine.new_night_dir()
                self.assertNotEqual(a, b)
                self.assertRegex(b.name, r"^20\d{2}-\d{2}-\d{2}-\d{6}$")
            finally:
                config.RUNS = old


class FixSlideTests(unittest.TestCase):
    def test_added_phrases_reads_like_rules(self):
        from nightshift import stage
        diff = ("--- a\n+++ b\n@@ -1 +1 @@\n-- Be kind. Keep it short.\n"
                "+- Be kind. Keep it short. If they won't give a number, ask once more at most, then move on.\n"
                "-- Say hi.\n+- Say hi or won't.\n+- Brand new rule about gas safety comes first.\n")
        adds = stage.added_phrases(diff)
        self.assertIn("If they won't give a number, ask once more at most, then move on.", adds)
        self.assertIn("Brand new rule about gas safety comes first.", adds)
        self.assertFalse(any(a.strip() == "or won't." for a in adds))
        ranked = stage.ranked_adds(diff, "endless re-asking for a callback number")
        self.assertTrue(ranked[0].startswith("If they won't give a number"))


class SharedBaselineTests(unittest.TestCase):
    def test_borrows_baseline_from_same_instructions(self):
        from nightshift import config, ops
        with tempfile.TemporaryDirectory() as d:
            old = config.RUNS
            config.RUNS = Path(d) / "runs"
            try:
                base_file = Path(d) / "base.md"
                base_file.write_text("**1 of 2 passed.**")
                a, b, c = (config.RUNS / n for n in ("2026-11-16-010000", "2026-11-17-010000", "2026-11-17-020000"))
                for x, prompt in ((a, "P1"), (b, "P1"), (c, "P2")):
                    x.mkdir(parents=True)
                    (x / "prompt.before.md").write_text(prompt)
                (a / "summary.json").write_text(json.dumps({"baseline_path": str(base_file)}))
                (b / "summary.json").write_text(json.dumps({"mode": "real", "baseline_file": "real calls"}))
                (c / "summary.json").write_text(json.dumps({"mode": "real", "baseline_file": "real calls"}))
                self.assertEqual(ops.shared_baseline(b), base_file)
                self.assertIsNone(ops.shared_baseline(c))  # different instructions: no borrowing
            finally:
                config.RUNS = old


class OneFixAtATimeTests(unittest.TestCase):
    def test_waiting_only_counts_fixes_on_the_current_approved(self):
        from nightshift import config, nightly
        with tempfile.TemporaryDirectory() as d:
            old = config.RUNS
            config.RUNS = Path(d)
            try:
                for name, s in {
                    "2026-11-16-180000": {"diff": "+a", "approved": {"by": "lucas"}},
                    "2026-11-16-190000": {"diff": "+b", "mode": "real", "verdict": "Better, could be luck"},  # stale
                    "2026-11-17-170000": {"diff": "+c", "builds_on": "2026-11-16-180000", "verdict": "Fix proposed"},
                }.items():
                    (Path(d) / name).mkdir()
                    (Path(d) / name / "summary.json").write_text(json.dumps(s))
                self.assertEqual(nightly.waiting_on_person().name, "2026-11-17-170000")
                f = Path(d) / "2026-11-17-170000" / "summary.json"
                s = json.loads(f.read_text())
                s["verdict"] = "Don't approve: x dropped"
                f.write_text(json.dumps(s))
                self.assertIsNone(nightly.waiting_on_person())  # the proof closed it
            finally:
                config.RUNS = old


class TryPageTests(unittest.TestCase):
    def test_split_and_validate(self):
        from nightshift import serve
        self.assertEqual(serve.split_calls("a: hi\n---\nb: yo\n  ---  \n\n"), ["a: hi", "b: yo"])
        self.assertIsNone(serve.start_try({"prompt": "", "calls": "x"})[0])
        self.assertIsNone(serve.start_try({"prompt": "p", "calls": "\n---\n".join(["c"] * 51)})[0])
        self.assertIsNone(serve.start_try({"prompt": "p" * 20_001, "calls": "c"})[0])
        self.assertIn("Bayview", serve.sample()["name"])

    def test_try_page_escapes(self):
        from nightshift import serve
        page = serve.try_page({"prompt": "</textarea><script>x</script>"})
        self.assertNotIn("<script>x", page)


class LessonTests(unittest.TestCase):
    def test_learn_only_from_rejections_and_no_duplicates(self):
        from nightshift import lessons
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lessons.md"
            ok = {"verdict": "Ready for approval", "worse": []}
            self.assertFalse(lessons.learn("n1", ok, path=p))
            bad = {"verdict": "Don't approve: cut_hand_medical dropped", "worse": ["cut_hand_medical"],
                   "before": {"cases": {"cut_hand_medical": [2, 4]}}, "after": {"cases": {"cut_hand_medical": [0, 4]}},
                   "agent_summary": {"summary": "excluded injuries from emergency triage"}}
            self.assertTrue(lessons.learn("n2", bad, path=p))
            self.assertFalse(lessons.learn("n2", bad, path=p))
            text = p.read_text()
            self.assertIn("cut_hand_medical (2/4 to 0/4)", text)
            self.assertIn("excluded injuries", text)


class ServeTests(unittest.TestCase):
    def test_reject_flow_and_token(self):
        import threading
        import urllib.error
        import urllib.parse
        import urllib.request
        from http.server import ThreadingHTTPServer
        from nightshift import config, lessons, serve
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            old = (config.RUNS, lessons.LESSONS)
            config.RUNS, lessons.LESSONS = t / "runs", t / "lessons.md"
            try:
                night = config.RUNS / "2026-11-16-010203"
                night.mkdir(parents=True)
                (night / "summary.json").write_text(json.dumps({"verdict": "Don't approve: x", "baseline_passed": 1,
                                                                "baseline_total": 2, "agent_summary": {"summary": "s"}}))
                (night / "report.html").write_text("<html><body><main>r</main></body></html>")
                srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                base = f"http://127.0.0.1:{srv.server_address[1]}"
                page = urllib.request.urlopen(base + "/night/2026-11-16-010203").read().decode()
                self.assertIn("Not approvable", page)
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base + "/night/2026-11-16-010203/reject", data=b"t=bad&why=x")
                body = urllib.parse.urlencode({"t": serve.TOKEN, "why": "made the cut-hand call worse"}).encode()
                urllib.request.urlopen(base + "/night/2026-11-16-010203/reject", data=body)
                self.assertIn("cut-hand", json.loads((night / "summary.json").read_text())["rejected"]["why"])
                self.assertIn("made the cut-hand call worse", lessons.LESSONS.read_text())
                srv.shutdown()
            finally:
                config.RUNS, lessons.LESSONS = old

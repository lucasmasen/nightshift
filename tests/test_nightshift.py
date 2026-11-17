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

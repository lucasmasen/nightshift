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

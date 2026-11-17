"""A local web app for the morning: every night, its report, its stage view, and real Approve / Reject buttons.

Binds to 127.0.0.1 only. Each page carries a per-run token that the buttons must send back, so another site open
in the same browser can't press them. Approve and Reject call the same code as the CLI; neither puts anything live.
"""
from __future__ import annotations

import html
import re
import secrets
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

from . import config, dashboard, stage

TOKEN = secrets.token_urlsafe(16)
NAME = re.compile(r"^20\d{2}-\d{2}-\d{2}-\d{6}$")
TRIES = config.PROJECT / "tries"  # git-ignored: pasted prompts and calls stay on this Mac
SAMPLE = config.PROJECT / "examples" / "bayview-dental"
LIMITS = {"prompt": 20_000, "calls": 200_000, "facts": 10_000, "protect": 2_000}
MAX_CALLS = 50
CSS = ("<style>body{background:#0f1512;color:#eef2ee;font:16px -apple-system,system-ui,sans-serif;margin:0}"
       "main{max-width:980px;margin:0 auto;padding:28px 20px}a{color:#ede6c8}h1{color:#7bc67e;margin:.2em 0}"
       "textarea{width:100%;box-sizing:border-box;background:#0b100d;color:#eef2ee;border:1px solid #2a362f;"
       "border-radius:8px;padding:10px;font:14px ui-monospace,monospace}label{display:block;margin:18px 0 6px;"
       "color:#ede6c8}button{font:inherit;font-size:18px;padding:10px 22px;border:0;border-radius:10px;"
       "background:#7bc67e;color:#0f1512;font-weight:700;cursor:pointer}pre{white-space:pre-wrap;background:#0b100d;"
       "padding:14px;border-radius:8px;border:1px solid #2a362f}.mute{color:#9aa79f}</style>")


def split_calls(text: str) -> list[str]:
    """Calls pasted into one box, separated by a line that is just ---."""
    return [c.strip() for c in re.split(r"^\s*---\s*$", text, flags=re.M) if c.strip()]


def sample() -> dict:
    calls = "\n---\n".join(f.read_text().strip() for f in sorted((SAMPLE / "calls").glob("*.txt")))
    return {"name": "Bayview Dental (made up)", "prompt": (SAMPLE / "prompt.md").read_text(), "calls": calls,
            "facts": (SAMPLE / "facts.md").read_text(), "protect": "This call is recorded"}


def try_page(v: dict | None = None, error: str = "") -> str:
    v = v or {}
    box = lambda k, rows: (f"<textarea name='{k}' rows='{rows}'>{html.escape(v.get(k, ''))}</textarea>")  # noqa: E731
    return (f"<!doctype html><meta charset=utf-8><title>Nightshift: try it</title>{CSS}<main>"
            f"<a href='/'>&larr; all nights</a><h1>Try Nightshift on your voice agent</h1>"
            f"<p class=mute>Paste its instructions and some calls. HarnessRouter's agent reads every call, says which "
            f"went wrong and why, and proposes the smallest fix. Nothing leaves this Mac except to HarnessRouter, and "
            f"nothing goes live. <a href='/try?sample=1'>Load the made-up dental sample</a></p>"
            + (f"<p style='color:#e8806f'>{html.escape(error)}</p>" if error else "")
            + f"<form method=post action='/try'><input type=hidden name=t value='{TOKEN}'>"
            f"<label>Agent name</label><input name=name value='{html.escape(v.get('name', ''))}' placeholder='e.g. "
            f"Bayview Dental line' style='font:inherit;padding:8px;width:320px;background:#0b100d;color:#eef2ee;"
            f"border:1px solid #2a362f;border-radius:8px'>"
            f"<label>Instructions (the agent's system prompt)</label>{box('prompt', 12)}"
            f"<label>Calls: paste transcripts, one per call, with a line of just --- between calls</label>{box('calls', 14)}"
            f"<label>Business facts (optional)</label>{box('facts', 4)}"
            f"<label>Lines that must never change, one per line (optional; e.g. the AI disclosure)</label>{box('protect', 2)}"
            f"<p><label style='display:inline'><input type=checkbox name=mock value=1> practice run ($0, no "
            f"HarnessRouter call)</label></p><button>Run Nightshift</button> "
            f"<span class=mute>A real run is one HarnessRouter task, usually under 10 cents.</span></form></main>")


def start_try(form: dict) -> tuple[str | None, str]:
    """Write the pasted agent to tries/<stamp>/ and start `nightshift agent` on it. Returns (stamp, error)."""
    for k, n in LIMITS.items():
        if len(form.get(k, "")) > n:
            return None, f"{k} is too long (limit {n:,} characters)."
    calls = split_calls(form.get("calls", ""))
    if not form.get("prompt", "").strip() or not calls:
        return None, "Paste the instructions and at least one call."
    if len(calls) > MAX_CALLS:
        return None, f"At most {MAX_CALLS} calls per run."
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    d = TRIES / stamp
    (d / "calls").mkdir(parents=True)
    (d / "prompt.md").write_text(form["prompt"])
    (d / "name.txt").write_text((form.get("name") or "pasted agent").strip()[:60])
    for i, c in enumerate(calls, 1):
        (d / "calls" / f"{i:02d}.txt").write_text(c + "\n")
    if form.get("facts", "").strip():
        (d / "facts.md").write_text(form["facts"])
    if form.get("protect", "").strip():
        (d / "protect.txt").write_text("\n".join(re.escape(ln.strip()) for ln in form["protect"].splitlines() if ln.strip()))
    cmd = [sys.executable, "-m", "nightshift", "agent", str(d)] + (["--mock"] if form.get("mock") else [])
    with open(d / "log.txt", "w") as out:
        subprocess.Popen(cmd, cwd=config.PROJECT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    return stamp, ""


def try_status(stamp: str) -> str | None:
    log_file = TRIES / stamp / "log.txt"
    if not NAME.match(stamp) or not log_file.exists():
        return None
    log = log_file.read_text()
    night = re.search(r"Report: .*/runs/(20\d{2}-\d{2}-\d{2}-\d{6})/report\.html", log)
    done = night or "Traceback" in log or "Not starting" in log
    head = (f"<p><a href='/night/{night.group(1)}' style='font-size:20px'>Open the report &rarr;</a></p>" if night else
            "<p style='color:#e8806f'>It stopped; see the log.</p>" if done else
            "<p class=mute>Working (a real run takes a minute or two)...</p><meta http-equiv=refresh content=3>")
    return (f"<!doctype html><meta charset=utf-8><title>Nightshift: running</title>{CSS}<main><a href='/try'>"
            f"&larr; try another</a><h1>Nightshift is reading the calls</h1>{head}<pre>{html.escape(log[-6000:])}</pre></main>")


def _night(name: str) -> Path | None:
    name = unquote(name)
    d = config.RUNS / name
    return d if NAME.match(name) and (d / "summary.json").exists() else None


def action_bar(name: str, s: dict) -> str:
    if s.get("approved"):
        state = f"<b style='color:#7bc67e'>Approved by {html.escape(str(s['approved'].get('by')))}</b>"
    elif s.get("rejected"):
        state = f"<b style='color:#e8806f'>Rejected: {html.escape(str(s['rejected'].get('why')))}</b>"
    else:
        ready = str(s.get("verdict", "")).startswith("Ready for approval")
        unproven = s.get("mode") == "agent" and s.get("diff") and not s.get("guard_problems")
        approve = (f"<form method='post' action='/night/{name}/approve' style='display:inline'>"
                   f"<input type='hidden' name='t' value='{TOKEN}'><button>Approve</button></form> " if ready else
                   f"<form method='post' action='/night/{name}/approve' style='display:inline'>"
                   f"<input type='hidden' name='t' value='{TOKEN}'><button>Approve after reading the diff</button>"
                   f"</form> <span style='color:#9aa79f'>(not re-tested: no simulator for this agent)</span> "
                   if unproven else "<span style='color:#e8806f'>Not approvable: the proof said no.</span> ")
        state = (approve +
                 f"<form method='post' action='/night/{name}/reject' style='display:inline'>"
                 f"<input type='hidden' name='t' value='{TOKEN}'><input name='why' required placeholder='why not?' "
                 f"style='font:inherit;padding:6px 8px;width:260px'> <button>Reject</button></form>")
    return (f"<div style='position:sticky;top:0;z-index:9;background:#0b100d;border-bottom:1px solid #2a362f;"
            f"padding:12px 16px;font:15px -apple-system,system-ui,sans-serif;color:#eef2ee'>"
            f"<a href='/' style='color:#ede6c8'>&larr; all nights</a> · <a href='/night/{name}/stage' "
            f"style='color:#ede6c8'>stage view</a> · {state}</div>")


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, code: int = 200) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to: str) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def log_message(self, *args) -> None:  # quiet
        pass

    def do_GET(self) -> None:  # noqa: N802
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if not parts:
            page = dashboard.render().replace("href='", "href='/night/").replace("/report.html'", "'")
            page = page.replace("<body>", "<body><div style='padding:12px 16px;background:#17331f;font:16px "
                                "-apple-system,system-ui,sans-serif'><a href='/try' style='color:#ede6c8'>"
                                "Try Nightshift on your own voice agent &rarr;</a></div>", 1)
            return self._send(page)
        if parts[0] == "try":
            if len(parts) == 2:
                page = try_status(parts[1])
                return self._send(page) if page else self._send("<p>Not found.</p>", 404)
            return self._send(try_page(sample() if "sample=1" in self.path else None))
        if parts[0] == "night" and len(parts) >= 2 and (d := _night(parts[1])):
            s = dashboard.load(d)
            if len(parts) == 3 and parts[2] == "stage":
                page = stage.render(d)
                if not s.get("approved") and not s.get("rejected") and str(s.get("verdict", "")).startswith("Ready"):
                    page = page.replace("</body>", f"""<script>
document.getElementById('approve')?.addEventListener('click', e => {{
  fetch('/night/{d.name}/approve', {{method: 'POST', headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: 't={TOKEN}'}});
}});</script></body>""")
                return self._send(page)
            report_page = (d / "report.html").read_text() if (d / "report.html").exists() else "<p>No report.</p>"
            return self._send(report_page.replace("<main>", action_bar(d.name, s) + "<main>", 1))
        self._send("<p>Not found.</p>", 404)

    def do_POST(self) -> None:  # noqa: N802
        parts = [p for p in self.path.split("/") if p]
        n = int(self.headers.get("Content-Length") or 0)
        if n > 300_000:
            return self._send("<p>Too big.</p>", 413)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode(errors="replace")).items()}
        if form.get("t") != TOKEN:
            return self._send("<p>Bad token: reload the page.</p>", 403)
        if parts == ["try"]:
            stamp, error = start_try(form)
            return self._redirect(f"/try/{stamp}") if stamp else self._send(try_page(form, error), 400)
        if len(parts) == 3 and parts[0] == "night" and (d := _night(parts[1])):
            if parts[2] == "approve":
                s = dashboard.load(d)
                # Other agents have no simulator: a person reading the diff is the whole gate, so allow it there only.
                unproven = s.get("mode") == "agent" and s.get("diff") and not s.get("guard_problems")
                dashboard.approve(d, "lucas", force=bool(unproven))
            elif parts[2] == "reject" and form.get("why", "").strip():
                dashboard.reject(d, "lucas", form["why"].strip()[:500])
            dashboard.write()
            return self._redirect(f"/night/{d.name}")
        self._send("<p>Not found.</p>", 404)


def run(port: int = 8765) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Nightshift: http://127.0.0.1:{port}  (Ctrl-C to stop)", flush=True)
    srv.serve_forever()

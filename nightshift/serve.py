"""A local web app for the morning: every night, its report, its stage view, and real Approve / Reject buttons.

Binds to 127.0.0.1 only. Each page carries a per-run token that the buttons must send back, so another site open
in the same browser can't press them. Approve and Reject call the same code as the CLI; neither puts anything live.
"""
from __future__ import annotations

import html
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

from . import config, dashboard, stage

TOKEN = secrets.token_urlsafe(16)
NAME = re.compile(r"^20\d{2}-\d{2}-\d{2}-\d{6}$")


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
        approve = (f"<form method='post' action='/night/{name}/approve' style='display:inline'>"
                   f"<input type='hidden' name='t' value='{TOKEN}'><button>Approve</button></form> " if ready else
                   "<span style='color:#e8806f'>Not approvable: the proof said no.</span> ")
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
            return self._send(page)
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
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(min(n, 10_000)).decode()).items()}
        if form.get("t") != TOKEN:
            return self._send("<p>Bad token: reload the page.</p>", 403)
        if len(parts) == 3 and parts[0] == "night" and (d := _night(parts[1])):
            if parts[2] == "approve":
                dashboard.approve(d, "lucas")
            elif parts[2] == "reject" and form.get("why", "").strip():
                dashboard.reject(d, "lucas", form["why"].strip()[:500])
            dashboard.write()
            return self._redirect(f"/night/{d.name}")
        self._send("<p>Not found.</p>", 404)


def run(port: int = 8765) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Nightshift: http://127.0.0.1:{port}  (Ctrl-C to stop)", flush=True)
    srv.serve_forever()

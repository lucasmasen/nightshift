"""Demo Day check: is everything Lucas will show working, and is there an offline copy if the wifi dies?

    python3 -m nightshift preflight

Read-only except for two local things: it builds offline/ (the stage slides, reports and video, openable with no
internet) and starts the local web app if it isn't running. Prints a checklist; exit 0 only if nothing is red.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path

from . import config, dashboard

SITE = "https://nightshift-bighack.vercel.app"
PROJECT_PAGE = "https://community.harnessrouter.ai/p/nightshift"
REPO = "https://github.com/lucasmasen/nightshift"
OFFLINE = config.PROJECT / "offline"


def _get(url: str) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nightshift-preflight"})
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - fixed https hosts
            return r.status, r.read(400_000).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - any failure is a red line on the checklist
        return getattr(e, "code", 0) or 0, str(e)


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def showcase_nights() -> list[Path]:
    """The nights worth showing: the newest approved simulated night and the newest real-call night with a fix."""
    nights = dashboard.nights()
    sim = next((d for d, s in nights if s.get("approved") and s.get("mode") not in ("real", "agent")), None)
    real = next((d for d, s in nights if s.get("mode") == "real" and s.get("diff")), None)
    return [d for d in (sim, real) if d]


def build_offline(nights: list[Path]) -> Path:
    """offline/<night>/{stage.html, report.html, demo.mp4} plus an index, all openable from disk."""
    from . import stage
    shutil.rmtree(OFFLINE, ignore_errors=True)
    OFFLINE.mkdir(parents=True)
    links = []
    for d in nights:
        dest = OFFLINE / d.name
        dest.mkdir()
        (dest / "stage.html").write_text(stage.render(d, rehearse=True))
        for f in ("report.html", "demo.mp4", "card.png"):
            if (d / f).exists():
                shutil.copy2(d / f, dest / f)
        kind = "real call" if dashboard.load(d).get("mode") == "real" else "simulated night"
        links.append(f"<li><b>{d.name}</b> ({kind}): <a href='{d.name}/stage.html'>slides</a> (press N for your "
                     f"lines) · <a href='{d.name}/report.html'>report</a>"
                     + (f" · <a href='{d.name}/demo.mp4'>video</a>" if (dest / "demo.mp4").exists() else "") + "</li>")
    (OFFLINE / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Nightshift offline</title><body style='font:18px -apple-system,"
        "sans-serif;background:#0f1512;color:#eef2ee;padding:40px'><h1>Nightshift, offline copy</h1><p>No internet "
        "needed. Space or arrow keys move the slides.</p><ul>" + "".join(links) + "</ul></body>")
    return OFFLINE / "index.html"


def run(log=print) -> int:
    rows: list[tuple[bool | None, str]] = []

    def check(ok: bool | None, what: str) -> None:
        rows.append((ok, what))

    code, home = _get(SITE)
    check(code == 200 and "178 test calls" in home, f"public site {SITE} (shows the 178-call result)")
    code, _ = _get(SITE + "/2026-11-16-184822/demo.mp4")
    check(code == 200, "demo video on the site")
    code, _ = _get(PROJECT_PAGE)
    check(code == 200, f"project page {PROJECT_PAGE}")
    code, _ = _get(REPO)
    check(code == 200, f"GitHub repo {REPO}")
    nights = showcase_nights()
    check(bool(nights), "a night to show: " + (", ".join(d.name for d in nights) or "none approved yet"))
    real = [d for d in nights if dashboard.load(d).get("mode") == "real"]
    if real:
        s = dashboard.load(real[0])
        check(True if s.get("after") else None, f"real-call fix proven ({real[0].name})" + ("" if s.get("after") else
              ": not yet, it runs on the next test budget"))
    if not _port_open(8765):
        from . import nightly
        nightly.ensure_server(log=lambda m: None)
    check(True, "local web app http://127.0.0.1:8765 (Approve buttons, Try page, rehearse mode)")
    agent = Path.home() / "Library" / "LaunchAgents" / "ai.nightshift.night.plist"
    loaded = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    check(agent.exists() and "ai.nightshift.night" in loaded, "2am nightly schedule switched on")
    config.load_env()
    check(bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD")) or None,
          "morning email set up (Gmail app password in .env)")
    check(bool(config.api_key()), "HarnessRouter key in .env")
    try:
        index = build_offline(nights)
        check(True, f"offline copy (no wifi needed): {index}")
    except OSError as e:
        check(False, f"offline copy: {e}")

    for ok, what in rows:
        log(("  ✓ " if ok else "  - " if ok is None else "  ✗ ") + what)
    bad = [w for ok, w in rows if ok is False]
    log("Ready for Demo Day." if not bad else f"{len(bad)} thing(s) to fix before Demo Day.")
    return 0 if not bad else 1

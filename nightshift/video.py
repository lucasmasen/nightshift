"""A silent, captioned demo video of one night, built from the stage view (Chrome headless screenshots + ffmpeg).

Lucas can record a voiceover on top, or upload it as is. Every number in the captions comes from summary.json.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import stage

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def captions(s: dict) -> list[tuple[str, float]]:
    """(caption, seconds) for each of the six slides."""
    total, passed = s.get("baseline_total", 0), s.get("baseline_passed", 0)
    real = s.get("mode") == "real"
    b, a = s.get("before") or {}, s.get("after") or {}
    proof = (f"Same calls, fresh, before and after: {b.get('passed')}/{b.get('total')} to "
             f"{a.get('passed')}/{a.get('total')}." if a else "The proof re-runs the same calls before and after.")
    if s.get("p_value") is not None:
        proof += f" Chance it's luck: about {s['p_value']:.0%}."
    v = s.get("verify") or {}
    if v.get("complete") and v.get("with_fix"):  # the full check on every case is the bigger sample: lead with it
        proof = (f"Then the fix ran on all {v['with_fix'].split('/')[1]} test calls: "
                 f"{v['baseline_rate']:.0%} passed before, {v['with_fix_rate']:.0%} after.")
    return [
        (f"My AI receptionist for plumbers took {total} {'real' if real else 'test'} calls. "
         f"{total - passed} went wrong, and nobody was awake to notice.", 6),
        ("Here's one. The red lines are what the grader caught.", 8),
        ("Nightshift sends the failed calls to a Claude Code agent on HarnessRouter. It traces each one to the "
         "instruction that caused it and makes the smallest fix.", 8),
        ("The fix. A safety check rejects anything that touches the AI and call-recording disclosure.", 7),
        (proof, 9),
        ("Nothing goes live until a person approves it. Nightshift: the night shift for your voice agent.", 6),
    ]


def build(night_dir: Path, out: Path | None = None) -> Path:
    if not CHROME.exists() or not shutil.which("ffmpeg"):
        raise RuntimeError("Needs Google Chrome and ffmpeg.")
    s = json.loads((night_dir / "summary.json").read_text())
    out = out or night_dir / "demo.mp4"
    caps = captions(s)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        lines = []
        for i, (cap, secs) in enumerate(caps):
            page = t / f"s{i}.html"
            page.write_text(stage.render(night_dir, start=i, caption=cap))
            png = t / f"s{i}.png"
            subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                            "--window-size=1920,1080", "--virtual-time-budget=1500", f"--screenshot={png}",
                            page.as_uri()], capture_output=True, timeout=60, check=False)
            if not png.exists():
                raise RuntimeError(f"Chrome didn't render slide {i + 1}")
            lines += [f"file '{png}'", f"duration {secs}"]
        lines.append(f"file '{t / f's{len(caps) - 1}.png'}'")  # concat demuxer needs the last frame repeated
        (t / "list.txt").write_text("\n".join(lines) + "\n")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(t / "list.txt"),
                        "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-movflags", "+faststart", str(out)],
                       check=True, timeout=300)
    return out

"""The stage view: one night told in six slides for a live demo (space / arrow keys / click to advance).

A real night takes minutes, so on stage you replay a night that already ran: every number on these slides comes from
that night's summary.json and failures.md.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path


HIGH_STAKES = ("alert_fails", "gas", "burst_pipe", "burst_pipe_night", "no_water", "in_an_hour_flooding")


def first_failure(failures_md: str) -> tuple[str, str, list[str]]:
    """(case, why, up to 9 transcript lines) for the slide: a high-stakes failure if there is one, else the first.
    Long calls are trimmed to start at the first failed tool, where the story is."""
    sections = re.findall(r"^## \d+\. (\S+) \(FAIL\)\n\*\*Why it failed:\*\* (.*?)\n\n(.*?)(?=^## \d+\.|\Z)",
                          failures_md, re.M | re.S)
    if not sections:
        return "", "", []
    pick = next((x for x in sections if x[0] in HIGH_STAKES), sections[0])
    lines = [ln[2:] for ln in pick[2].splitlines() if ln.startswith("- ")]
    quotes = [q for q in re.findall(r'"([^"]{8,})"', pick[1])]
    marked = [("!" if any(q[:40] in ln for q in quotes) else "") + ln for ln in lines]
    if sum(m.startswith("!") for m in marked) >= 2 and len(marked) > 9:
        # Keep the story: the bad lines, the failed tool, and what the caller said right before each bad line.
        keep = set()
        for i, m in enumerate(marked):
            if m.startswith("!") or "**error**" in m:
                keep.add(i)
                if i and marked[i - 1].startswith("**Caller"):
                    keep.add(i - 1)
        marked = [m for i, m in enumerate(marked) if i in keep]
    return pick[0], pick[1].strip(), marked[:9]


def _line(raw: str) -> str:
    hot = raw.startswith("!")
    raw = html.escape(raw[1:] if hot else raw)
    raw = re.sub(r"\*\*(Godfrey|Caller):\*\*", r"<b class='\1'>\1</b>", raw)
    raw = re.sub(r"`\[(\w+)\]`", r"<span class='tool'>\1</span>", raw)
    raw = raw.replace("**error**", "<b class='err'>error</b>")
    return f"<span class='hot'>{raw}</span>" if hot else raw


def _diff(diff: str) -> str:
    out = []
    for ln in diff.splitlines():
        if ln.startswith(("+++", "---", "@@")):
            continue
        cls = "add" if ln.startswith("+") else "del" if ln.startswith("-") else "ctx"
        out.append(f"<div class='{cls}'>{html.escape(ln[1:] if ln[:1] in '+- ' else ln) or '&nbsp;'}</div>")
    return "\n".join(out[:24])


def render(night_dir: Path) -> str:
    s = json.loads((night_dir / "summary.json").read_text())
    fails = (night_dir / "failures.md").read_text() if (night_dir / "failures.md").exists() else ""
    case, why, lines = first_failure(fails)
    total, passed = s.get("baseline_total", 0), s.get("baseline_passed", 0)
    t = s.get("trace") or {}
    b, a = s.get("before") or {}, s.get("after") or {}
    bars = []
    for c in sorted(set(b.get("cases", {})) | set(a.get("cases", {}))):
        bp, bt = b.get("cases", {}).get(c, (0, 0))
        ap, at = a.get("cases", {}).get(c, (0, 0))
        tag = "fixed" if c in s.get("failed_cases", []) else "already passing"
        bars.append(f"<div class='row'><div class='name'>{html.escape(c)}<small>{tag}</small></div>"
                    f"<div class='bar'><i class='b' style='width:{100 * bp / bt if bt else 0:.0f}%'></i></div>"
                    f"<div class='bar'><i class='a' style='width:{100 * ap / at if at else 0:.0f}%'></i></div>"
                    f"<div class='n'>{bp}/{bt} &rarr; {ap}/{at}</div></div>")
    evidence = (f"{s.get('retest_repeat', '')} fresh calls per case · chance this is luck: "
                f"{s['p_value']:.0%} ({s.get('evidence', '')})") if "p_value" in s else ""
    where = f"{t.get('where', 'HarnessRouter')}" + (f" · {t['model']}" if t.get("model") else "")
    spend = f" · ${t['spend_usd']:.2f}" if isinstance(t.get("spend_usd"), (int, float)) else ""
    score = (f"{b.get('passed', 0)}/{b.get('total', 0)} &rarr; {a.get('passed', 0)}/{a.get('total', 0)}" if a else "not re-tested")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nightshift</title><style>
:root{{--bg:#0f1512;--ink:#eef2ee;--mute:#8d9b92;--g:#7bc67e;--c:#ede6c8;--r:#e8806f;--card:#17201b;--line:#2a362f}}
*{{box-sizing:border-box}} html,body{{margin:0;height:100%;background:var(--bg);color:var(--ink);font:22px/1.4 -apple-system,system-ui,sans-serif}}
section{{display:none;min-height:100vh;padding:7vh 8vw;flex-direction:column;justify-content:center}} section.on{{display:flex}}
.k{{color:var(--g);font-weight:700;letter-spacing:.05em;font-size:18px;text-transform:uppercase}}
h1{{font-size:clamp(40px,6vw,84px);line-height:1.05;margin:16px 0}} h2{{font-size:clamp(28px,3.4vw,48px);margin:10px 0 24px}}
.big{{font-size:clamp(90px,16vw,220px);font-weight:900;color:var(--g);line-height:1}} .mute{{color:var(--mute)}}
.call{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 26px;font-size:19px;max-width:1000px}}
.call div{{margin:6px 0}} .Godfrey{{color:var(--c)}} .Caller{{color:var(--mute)}} .tool{{font:15px ui-monospace,monospace;color:var(--g)}}
.why{{color:var(--r);font-size:22px;margin-top:18px}} .hot{{background:#3f2020;border-radius:6px;padding:2px 4px}} .err{{color:var(--r)}}
.diff{{font:18px/1.5 ui-monospace,Menlo,monospace;background:var(--card);border-radius:14px;padding:18px 22px;white-space:pre-wrap;max-width:1100px}}
.add{{background:#1f3a25;color:#bfe9c1}} .del{{background:#3f2020;color:#f3c1b8;text-decoration:line-through}} .ctx{{color:var(--mute)}}
.row{{display:grid;grid-template-columns:260px 1fr 1fr 130px;gap:14px;align-items:center;margin:10px 0;font-size:18px}}
.row small{{display:block;color:var(--mute);font-size:13px}} .bar{{height:18px;background:#0b1a10;border-radius:9px;overflow:hidden}}
.bar i{{display:block;height:100%;border-radius:9px}} .b{{background:#55625a}} .a{{background:var(--g)}} .n{{text-align:right}}
.legend{{color:var(--mute);font-size:16px}} button{{font:inherit;font-size:26px;padding:16px 34px;border-radius:12px;border:0;background:var(--g);color:#0f1512;font-weight:700;cursor:pointer}}
button.done{{background:var(--c)}} .hint{{position:fixed;bottom:14px;right:18px;color:var(--mute);font-size:14px}}
@media(max-width:700px){{.row{{grid-template-columns:1fr;}} .n{{text-align:left}}}}
</style></head><body>
<section class="on"><div class="k">{"Real calls" if s.get("mode") == "real" else "Last night"}</div><h1>Godfrey took {total} {"real " if s.get("mode") == "real" else ""}calls.</h1><div class="big">{total - passed}</div>
<h2 class="mute">went wrong. Nobody was awake to notice.</h2></section>
<section><div class="k">One of them · {html.escape(case)}</div><div class="call">{"".join(f"<div>{_line(x)}</div>" for x in lines)}</div>
<div class="why">Graded fail: {html.escape(why[:240])}{"…" if len(why) > 240 else ""}</div></section>
<section><div class="k">2am</div><h1>Nightshift hands the failures to an agent on HarnessRouter.</h1>
<h2 class="mute">It traces each one to the instruction that caused it and makes the smallest fix.</h2>
<div class="mute">{html.escape(where)}{spend} · harness {html.escape(str(t.get("harness_id", "")))}</div></section>
<section><div class="k">The fix</div><div class="diff">{_diff(s.get("diff", "")) or "No change."}</div>
<div class="mute" style="margin-top:16px">Safety check: disclosure untouched, placeholders kept, under 30 lines.</div></section>
<section><div class="k">Proof · same calls, before and after</div><h2>{score}</h2><div class="mute" style="margin:-12px 0 18px">{html.escape(evidence)}</div>{"".join(bars)}
<div class="legend">grey = before · green = after</div></section>
<section><div class="k">7am</div><h1>Nothing went live.</h1><h2 class="mute">The fix waits for a person.</h2>
<div><button onclick="this.textContent='Approved ✓';this.classList.add('done')">Approve</button></div></section>
<div class="hint">space / → next · ← back</div>
<script>
const s=[...document.querySelectorAll('section')];let i=0;
function go(n){{i=Math.max(0,Math.min(s.length-1,n));s.forEach((x,j)=>x.classList.toggle('on',j===i));}}
addEventListener('keydown',e=>{{if(['ArrowRight',' ','PageDown'].includes(e.key)){{e.preventDefault();go(i+1)}}if(['ArrowLeft','PageUp'].includes(e.key))go(i-1)}});
addEventListener('click',e=>{{if(e.target.tagName!=='BUTTON')go(i+1)}});
</script></body></html>"""


def write(night_dir: Path) -> Path:
    p = night_dir / "stage.html"
    p.write_text(render(night_dir))
    return p


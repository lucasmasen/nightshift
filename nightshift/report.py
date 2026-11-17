"""Step 4: the morning report, one self-contained HTML page per night."""
from __future__ import annotations

import html
from datetime import datetime


def _pct(p: int, t: int) -> str:
    return f"{p}/{t} ({p / t:.0%})" if t else "n/a"


def _diff_html(diff: str) -> str:
    rows = []
    for line in diff.splitlines():
        cls = "add" if line.startswith("+") and not line.startswith("+++") else \
              "del" if line.startswith("-") and not line.startswith("---") else "ctx"
        rows.append(f'<div class="{cls}">{html.escape(line) or "&nbsp;"}</div>')
    return "\n".join(rows)


def approved_html(night: dict) -> str:
    a = night.get("approved")
    if a:
        return (f'<div class="card" style="border-color:var(--good)"><b style="color:var(--good)">Approved by '
                f'{html.escape(str(a.get("by")))} at {html.escape(str(a.get("at")))}</b>. Saved, not yet live: a person '
                f'copies it in and pushes it to the phone line.</div>')
    return ('<div class="card"><b>Nothing went live.</b> The fix waits for a person to approve it. The live phone '
            'line and the real prompt were not touched.</div>')


def render(night: dict) -> str:
    """night: the summary dict __main__ writes (see build_summary)."""
    b, a = night.get("before"), night.get("after")
    verdict = night.get("verdict", "")
    cases = sorted(set((b or {}).get("cases", {})) | set((a or {}).get("cases", {})))
    rows = []
    for c in cases:
        bp, bt = (b or {}).get("cases", {}).get(c, (0, 0))
        ap, at = (a or {}).get("cases", {}).get(c, (0, 0))
        moved = "up" if at and bt and ap / at > bp / bt else "down" if at and bt and ap / at < bp / bt else ""
        tag = "fixed" if c in night.get("failed_cases", []) else "guard"
        rows.append(f"<tr class='{moved}'><td>{html.escape(c)}</td><td>{tag}</td><td>{_pct(bp, bt)}</td>"
                    f"<td>{_pct(ap, at) if a else '-'}</td></tr>")
    problems = "".join(f"<li>{html.escape(p)}</li>" for p in night.get("guard_problems", []))
    trace = dict(night.get("trace", {}))
    if night.get("sim_cost_usd") is not None:
        trace["proof_cost_usd"] = night.get("sim_cost_usd")
    trace_bits = [f"{k.replace('_', ' ')}: {html.escape(str(v))}" for k, v in trace.items() if v not in (None, "", {})]
    big = ""
    if b and a:
        big = (f"<div class='score'><div><span>before</span><b>{_pct(b['passed'], b['total'])}</b></div>"
               f"<div class='arrow'>&rarr;</div><div><span>after</span><b>{_pct(a['passed'], a['total'])}</b></div></div>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Nightshift report</title>
<style>
:root{{--bg:#0f1512;--card:#17201b;--ink:#eef2ee;--mute:#9aa89f;--line:#2a362f;--good:#7bc67e;--bad:#e8806f;--accent:#ede6c8}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,system-ui,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:32px 16px 64px}} h1{{font-size:28px;margin:0 0 4px}} h2{{font-size:18px;margin:32px 0 10px;color:var(--accent)}}
.sub{{color:var(--mute)}} .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}}
.verdict{{font-size:18px;font-weight:600}} .score{{display:flex;gap:18px;align-items:center;margin:14px 0}}
.score span{{display:block;color:var(--mute);font-size:13px}} .score b{{font-size:30px}} .arrow{{font-size:28px;color:var(--mute)}}
table{{width:100%;border-collapse:collapse}} td,th{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);font-size:14px}}
th{{color:var(--mute);font-weight:500}} tr.up td:last-child{{color:var(--good)}} tr.down td:last-child{{color:var(--bad)}}
.diff{{font:13px/1.45 ui-monospace,Menlo,monospace;overflow-x:auto;white-space:pre-wrap}} .add{{background:#1f3a25;color:#bfe9c1}} .del{{background:#3f2020;color:#f3c1b8}} .ctx{{color:var(--mute)}}
.rep{{white-space:pre-wrap;font-size:14px}} ul{{margin:6px 0 0 18px;padding:0}} .warn{{border-color:var(--bad)}}
</style></head><body><main>
<h1>Nightshift</h1><div class="sub">Night of {html.escape(night.get("date", ""))} &middot; {html.escape(night.get("agent", "voice agent"))}
&middot; generated {datetime.now():%b %d, %I:%M %p}</div>
<div class="card"><div class="verdict">{html.escape(verdict)}</div>{big}
<div class="sub">{night.get("baseline_passed", 0)} of {night.get("baseline_total", 0)} simulated calls passed overnight;
{len(night.get("failed_cases", []))} kind{"" if len(night.get("failed_cases", [])) == 1 else "s"} of call failed and got a fix. Nightshift proposed a fix and re-ran the failed calls plus
calls that already passed (to catch anything the fix breaks).</div></div>
{f'<div class="card warn"><b>Fix rejected by the safety check</b><ul>{problems}</ul></div>' if problems else ''}
<h2>Every case it re-ran</h2><div class="card"><table><tr><th>Case</th><th>Why re-run</th><th>Before</th><th>After</th></tr>
{"".join(rows) or "<tr><td colspan=4>No re-run.</td></tr>"}</table></div>
<h2>The change it proposes</h2><div class="card diff">{_diff_html(night.get("diff", "")) or "No change."}</div>
<h2>Why (Nightshift's own report)</h2><div class="card rep">{html.escape(night.get("agent_report", ""))}</div>
<h2>Where it ran</h2><div class="card sub">{"<br>".join(trace_bits) or "-"}</div>
{approved_html(night)}
</main></body></html>"""

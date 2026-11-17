"""Bring your own agent: run Nightshift on any voice agent, not just Godfrey.

A folder with:
  prompt.md          the agent's instructions (required)
  calls/*.txt|*.md   one transcript per call (required; or calls.jsonl with {"id", "transcript", "problem"?})
  facts.md           the business's true facts (optional; the agent must never contradict them)
  protect.txt        lines that must survive word for word, one regex per line (optional; default: the AI and
                     recording disclosure)

There's no grader and no simulator for someone else's agent, so the HarnessRouter agent does the triage itself:
it reads every call against the instructions, says which went wrong and why, then makes the smallest fix. The fix
still goes through the same safety check. It can't be proven before/after without a simulator, so the verdict says
"proposed, not proven" and a person reviews the diff.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import fixer, guard
from .hr import Client

SYSTEM_PROMPT = """You are Nightshift. Each night you review a voice agent's calls and fix its instructions.

Files in your working directory:
- prompt.md: the agent's instructions (its system prompt).
- calls.md: tonight's calls, each a full transcript. Some may come with a note saying what someone thought went wrong.
- facts.md (if present): the business's true facts. Never contradict or add to them.
- protect.txt (if present): lines of prompt.md that must survive word for word.

Your job:
1. Read every call against prompt.md. A call went wrong if the agent broke an instruction, promised something it
   couldn't confirm, gave unsafe or invented information, or left the caller worse off. List those calls.
2. For each, find the instruction that caused it, or the missing instruction. Group calls that share a cause.
3. Edit prompt.md in place with the smallest change that fixes the causes. Don't rewrite sections, reorder, or
   change the style.
4. Write nightshift-report.md: the calls that went wrong (quote the bad moment), each cause, the edit, and why it
   should not break calls that went fine. Also list calls you judged fine.

Hard rules (a change that breaks one is thrown away):
- Never change or remove the AI / call recording disclosure, or any line protect.txt names.
- Keep every $placeholder and {{variable}} exactly as written.
- Never invent facts, prices, hours, promises or phone numbers.
- Change at most 30 lines in total.
- If a call went wrong for a reason the instructions can't fix (bad audio, a tool outage), say so and leave it.

End your final reply with one line of JSON:
{"changed": true|false, "causes": <number>, "calls_reviewed": <number>, "calls_wrong": <number>, "summary": "<one sentence>"}"""

TASK = ("Tonight's calls are in calls.md. Find the ones that went wrong, fix prompt.md following your instructions, "
        "then write nightshift-report.md. Edit prompt.md in place; do not create a copy with another name.")

VAR = re.compile(r"\{\{\s*[A-Za-z_][\w.]*\s*\}\}")


@dataclass
class AgentDir:
    path: Path
    prompt: str
    calls: list[dict]  # {"id", "transcript", "problem"}
    facts: str
    protect: list[re.Pattern]
    model: str = ""  # the model the agent runs on (model.txt), used to replay its bad moments

    @property
    def name(self) -> str:
        label = self.path / "name.txt"
        return label.read_text().strip()[:60] if label.is_file() and label.read_text().strip() else self.path.name


def load(path: Path) -> AgentDir:
    path = Path(path)
    prompt_file = path / "prompt.md"
    if not prompt_file.is_file():
        raise ValueError(f"{path} has no prompt.md")
    calls: list[dict] = []
    jsonl = path / "calls.jsonl"
    if jsonl.is_file():
        for n, line in enumerate(jsonl.read_text().splitlines(), 1):
            if line.strip():
                d = json.loads(line)
                calls.append({"id": str(d.get("id") or n), "transcript": d.get("transcript", ""),
                              "problem": d.get("problem") or ""})
    for f in sorted((path / "calls").glob("*")) if (path / "calls").is_dir() else []:
        if f.suffix in (".txt", ".md"):
            calls.append({"id": f.stem, "transcript": f.read_text(), "problem": ""})
    calls = [c for c in calls if c["transcript"].strip()]
    if not calls:
        raise ValueError(f"{path} has no calls (add calls/*.txt or calls.jsonl)")
    protect_file = path / "protect.txt"
    protect = [re.compile(ln.strip(), re.I) for ln in protect_file.read_text().splitlines()
               if ln.strip() and not ln.startswith("#")] if protect_file.is_file() else []
    facts = (path / "facts.md").read_text() if (path / "facts.md").is_file() else ""
    model = (path / "model.txt").read_text().strip() if (path / "model.txt").is_file() else ""
    return AgentDir(path, prompt_file.read_text(), calls, facts, protect, model)


def calls_markdown(a: AgentDir) -> str:
    out = [f"# Tonight's calls for {a.name}", f"{len(a.calls)} calls. Read each against prompt.md.", ""]
    for c in a.calls:
        out += [f"## Call {c['id']}"]
        if c["problem"]:
            out += [f"**Someone flagged this call:** {c['problem']}"]
        out += ["", c["transcript"].strip(), ""]
    return "\n".join(out)


def safety_problems(a: AgentDir, new: str) -> list[str]:
    """The standard check, plus the agent's own protected lines and {{variables}}."""
    problems = guard.check(a.prompt, new)
    new_lines = set(new.splitlines())
    for line in a.prompt.splitlines():
        if any(p.search(line) for p in a.protect) and line not in new_lines:
            problems.append("A protected line was changed or removed: " + line[:90] + "...")
    missing = set(VAR.findall(a.prompt)) - set(VAR.findall(new))
    if missing:
        problems.append("Variables were dropped: " + ", ".join(sorted(missing)))
    return problems


def run(a: AgentDir, out: Path, client: Client | None, log=print) -> fixer.Fix:
    """client=None runs the mock (no HarnessRouter call, no spend)."""
    (out / "calls.md").write_text(calls_markdown(a))
    (out / "prompt.before.md").write_text(a.prompt)
    if client is None:
        fix = fixer.run_mock(out / "prompt.before.md", out / "calls.md", out / "calls.md", log=log)
        fix.summary.setdefault("calls_reviewed", len(a.calls))
        fix.summary.setdefault("calls_wrong", sum(bool(c["problem"]) or any(pat.search(c["transcript"])
                                                  for pat, _ in fixer.MOCK_RULES) for c in a.calls))
        return fix
    files = [(out / "prompt.before.md", "prompt.md"), (out / "calls.md", "calls.md")]
    if a.facts:
        (out / "facts.md").write_text(a.facts)
        files.append((out / "facts.md", "facts.md"))
    if (a.path / "protect.txt").is_file():
        files.append((a.path / "protect.txt", "protect.txt"))
    return fixer.run_on_harnessrouter(client, SYSTEM_PROMPT, TASK, files, log=log)

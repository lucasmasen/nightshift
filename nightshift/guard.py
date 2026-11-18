"""Checks a proposed prompt before it is tested or shown for approval. A fix that fails any check is rejected."""
from __future__ import annotations

import difflib
import re

from . import config

# Lines that carry the AI + recording disclosure (CA two-party consent). They must survive word for word.
DISCLOSURE = re.compile(r"recorded|se graba|AI assistant|asistente de inteligencia artificial", re.I)
PLACEHOLDER = re.compile(r"\$[a-z_]+")
PHONE = re.compile(r"(?<!\d)\d{3}[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
REDACTED = re.compile(r"\[(NAME|PHONE|ADDRESS|UNIT|GATE|EMAIL|SHOP)\]")


def changed_lines(old: str, new: str) -> int:
    """Lines changed, counting a reworded line once (the bigger of lines removed and lines added)."""
    diff = [d for d in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
            if not d.startswith(("+++", "---"))]
    return max(sum(d.startswith("+") for d in diff), sum(d.startswith("-") for d in diff))


def diff_text(old: str, new: str) -> str:
    return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), "prompt.md (before)",
                                          "prompt.md (after)", lineterm="", n=1))


def check(old: str, new: str, max_changed: int = config.MAX_CHANGED_LINES) -> list[str]:
    """Problems with the proposed prompt; an empty list means it may be tested."""
    problems: list[str] = []
    if not new.strip():
        problems.append("The proposed prompt is empty.")
        return problems
    if new == old:
        problems.append("The proposed prompt is identical to the current one.")
    new_lines = set(new.splitlines())
    for line in old.splitlines():
        if DISCLOSURE.search(line) and line not in new_lines:
            problems.append("A disclosure line was changed or removed: " + line[:90] + "...")
    missing = set(PLACEHOLDER.findall(old)) - set(PLACEHOLDER.findall(new))
    if missing:
        problems.append("Placeholders were dropped: " + ", ".join(sorted(missing)))
    digits = lambda x: re.sub(r"\D", "", x)  # noqa: E731
    old_numbers = {digits(m) for m in PHONE.findall(old)}
    added = [d for d in (digits(m) for m in PHONE.findall(new)) if d not in old_numbers]
    if added:
        problems.append("The fix adds a phone number that wasn't in the instructions (never invent numbers): "
                        + ", ".join(f"***-***-**{d[-2:]}" for d in added))
    labels = set(REDACTED.findall(new)) - set(REDACTED.findall(old))
    if labels:
        problems.append("The fix copied scrubbing labels from the calls into the instructions: "
                        + ", ".join(f"[{x}]" for x in sorted(labels)))
    n = changed_lines(old, new)
    if n > max_changed:
        problems.append(f"The fix changes {n} lines; the limit is {max_changed}. Smaller fixes are safer.")
    return problems

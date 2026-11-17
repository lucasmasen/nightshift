"""What a night cost: the fix (model tokens on HarnessRouter, billed to the BYOK key) and the proof (simulated calls)."""
from __future__ import annotations

import re

# $ per million tokens: input, output, cache write, cache read. Same table as demo-factory/demo_line/suite_cost.py
# (claude.com/pricing, 10/26). Model ids from HarnessRouter may use dots or dashes.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
}
MEASURED = re.compile(r"measured \$(\d+\.\d+) from tokens")
ESTIMATED = re.compile(r"Paid calls: \d+, estimated \$(\d+\.\d+)")


def fix_cost(usage: dict | None, model: str | None) -> float | None:
    """Estimated $ for the fix task from its token counts; None if the model isn't in the table."""
    if not usage or not model:
        return None
    key = model.split("/")[-1].replace(".", "-")
    price = PRICES.get(key)
    if not price:
        return None
    i, o, w, r = price
    return round((usage.get("input_tokens", 0) * i + usage.get("output_tokens", 0) * o
                  + usage.get("cache_write_tokens", 0) * w + usage.get("cache_read_tokens", 0) * r) / 1e6, 4)


def sim_cost(suite_output: str) -> float:
    """$ for one simulator run, from its own closing line (measured if it printed it, else its estimate)."""
    m = MEASURED.search(suite_output) or ESTIMATED.search(suite_output)
    return float(m.group(1)) if m else 0.0

#!/bin/zsh
# Wednesday 11/18, one command, after midnight PT (fresh $2/day simulator budget). Each step runs even if one fails.
#   1. finish Claude Code vs Codex: only Codex's run is left (~$0.16; the other two are saved)
#   2. re-check the 10 cases that flipped to fail in Tuesday's full check, 4 calls each, old vs new (~$0.45)
#   3. if any case is "really worse", a fix night for just those, built on night 1's fix (~$0.35 + ~$0.20 agent)
#   4. republish the site, make the day-3 card
# Nothing goes live. Read the re-check before any golive: "really worse" cases need a fix first.
cd "${0:A:h}"
N=runs/2026-11-16-184822
python3 -m nightshift check || exit 1
python3 -m nightshift arena-prove $N --repeat 4 || echo "arena: see above"
python3 -m nightshift recheck $N --repeat 4 || echo "recheck: see above"
WORSE=$(python3 -c "
import json; s=json.load(open('$N/summary.json')); r=s.get('recheck') or {}
print(' '.join(r.get('really_worse') or []), '|', (r.get('files') or ['',''])[1])")
CASES=${WORSE%%|*}; AFTER=${WORSE##*| }
if [[ -n "${CASES// /}" && -n "$AFTER" ]]; then
  K=$(echo $CASES | wc -w | tr -d ' ')
  echo "Really worse: $CASES -> fix night on top of $N"
  python3 -m nightshift night --from-run ../demo-factory-nightshift/demo_line/suite_runs/$AFTER --on-top-of $N \
    --max-cases $K --guards 3 --repeat 4 || echo "fix night: see above"
else
  echo "Re-check: nothing really worse, no fix night needed."
fi
# All of 11/17's real-call fixes, combined into one change: prove it on every friend's call (~$0.50)
python3 -m nightshift retest runs/2026-11-17-172919 --repeat 4 || echo "real-call proof: see above"
python3 -m nightshift publish || echo "publish: see above"
python3 -m nightshift card $N --day 3
echo "Done. Arena + re-check results: python3 -m nightshift serve, then open night $N"

#!/bin/zsh
# Wednesday 11/18, one command, after midnight PT (fresh $2/day simulator budget). Each step runs even if one fails.
#   1. finish Claude Code vs Codex: only Codex's run is left (~$0.16; the other two are saved)
#   2. re-check the 10 cases that flipped to fail in Tuesday's full check, 4 calls each, old vs new (~$0.45)
#   4. republish the site, make the day-3 card
# Nothing goes live. Read the re-check before any golive: "really worse" cases need a fix first.
cd "${0:A:h}"
N=runs/2026-11-16-184822
python3 -m nightshift check || exit 1
python3 -m nightshift arena-prove $N --repeat 4 || echo "arena: see above"
python3 -m nightshift recheck $N --repeat 4 || echo "recheck: see above"
# (No automatic fix night here: a fix is already waiting for Lucas; the 2am run makes new fixes once he decides.)
# All of 11/17's real-call fixes, combined into one change: prove it on every friend's call (~$0.50)
python3 -m nightshift retest runs/2026-11-17-173940 --repeat 4 || echo "real-call proof: see above"
# If that proof holds, put its numbers in the pitch [PROOF] and X draft 14 [result] (backups in the night folder)
python3 -m nightshift fill-proof runs/2026-11-17-173940 || echo "fill-proof: left placeholders, see above"
python3 -m nightshift publish || echo "publish: see above"
python3 -m nightshift card $N --day 3
echo "Done. Arena + re-check results: python3 -m nightshift serve, then open night $N"

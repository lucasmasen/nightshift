#!/bin/zsh
# Tuesday morning, one command (fresh $2/day simulator budget). Stops at the first failure.
#   1. verify night 1's approved fix on every case (~$0.90)
#   2. prove the real-call fix on its simulated twins (~$0.40)
#   3. republish the public site, make today's post card
# It never puts anything live: golive is a separate command a person runs.
set -e
cd "${0:A:h}"
python3 -m nightshift check
python3 -m nightshift verify runs/2026-11-16-184822
python3 -m nightshift retest runs/2026-11-16-191515 --repeat 4
python3 -m nightshift publish
python3 -m nightshift card runs/2026-11-16-184822 --day 2
echo "Done. Next (a person): read runs/2026-11-16-184822/report.html, then"
echo "  python3 -m nightshift golive runs/2026-11-16-184822          # dry run, shows the change"
echo "  python3 -m nightshift golive runs/2026-11-16-184822 --really # puts it on the phone line"

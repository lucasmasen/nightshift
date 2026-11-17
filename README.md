# Nightshift

**AI phone agents make mistakes at 2am and nobody notices. Nightshift reviews every call overnight, fixes the
agent's instructions, proves the fix on simulated calls, and has it waiting for a human to approve by morning.**

Built for The Big Hack Game (Nov 2026). The fixing runs on [HarnessRouter](https://harnessrouter.ai): a Claude Code
harness reads the failed calls and edits the agent's prompt as a background task.

## How a night works
1. **Calls.** Tonight's calls are graded (here: simulated callers against Godfrey, the after-hours receptionist for
   Sunset Plumbing, a made-up shop). Each call gets code checks and a judge model.
2. **Fix (HarnessRouter).** The failed calls, the agent's prompt and the shop's facts go to a HarnessRouter task.
   The agent finds the cause of each failure, makes the smallest edit to the prompt, and writes a report.
3. **Safety check.** Rejected if it touches the AI/recording disclosure, drops a placeholder, or changes more than
   30 lines.
4. **Prove it.** The failed cases plus a few cases that already passed are re-run, same number of calls, before and
   after.
5. **Morning report.** `runs/<night>/report.html`: before/after score, the exact change, the agent's reasoning, and
   what it cost. Nothing goes live until a person approves.

## Run it
```bash
python3 -m nightshift check                      # setup check, no spend
python3 -m nightshift night --mock --from-run FILE --no-retest   # whole loop at $0
python3 -m nightshift night --from-run FILE      # real fix on HarnessRouter + re-test
python3 -m nightshift night                      # full night: simulate all cases first
python3 -m unittest discover -s tests            # tests, no network
```
Needs `HR_API_KEY` in `nightshift/.env` (git-ignored) and the `demo-factory-nightshift` test checkout. Simulated
calls go through the simulator's own $2/day cap.

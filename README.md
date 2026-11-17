# Nightshift

**AI phone agents make mistakes at 2am and nobody notices. Nightshift reviews every call overnight, fixes the
agent's instructions, proves the fix on simulated calls, and has it waiting for a human to approve by morning.**

Built for The Big Hack Game (Nov 2026). Live: https://nightshift-bighack.vercel.app The fixing runs on [HarnessRouter](https://harnessrouter.ai): a Claude Code
harness reads the failed calls and edits the agent's prompt as a background task.

## How a night works
1. **Calls.** Tonight's calls are graded (here: simulated callers against Godfrey, the after-hours receptionist for
   Sunset Plumbing, a made-up shop). Each call gets code checks and a judge model.
2. **Fix (HarnessRouter).** The failed calls, the agent's prompt and the shop's facts go to a HarnessRouter task.
   The agent finds the cause of each failure, makes the smallest edit to the prompt, and writes a report.
3. **Safety check.** Rejected if it touches the AI/recording disclosure, drops a placeholder, or changes more than
   30 lines.
4. **Prove it.** The failed cases plus a few cases that already passed are re-run with fresh calls only, same number
   before and after. A one-sided Fisher test says how likely the gain is to be luck; "ready" needs p < 0.2 and no
   passing case getting worse.
5. **Lessons.** Every rejected fix (by the proof or a person) goes into lessons.md, which the next night's agent
   reads first, so it doesn't make the same mistake twice.
6. **Morning report.** `runs/<night>/report.html`: before/after score, the exact change, the agent's reasoning, and
   what it cost. Nothing goes live until a person approves.

## Run it
```bash
python3 -m nightshift check                      # setup check, no spend
python3 -m nightshift night --mock --from-run FILE --no-retest   # whole loop at $0
python3 -m nightshift night --from-run FILE      # real fix on HarnessRouter + re-test
python3 -m nightshift night                      # full night: simulate all cases first
python3 -m nightshift retest runs/<night>         # re-run the proof for a night that has a fix
python3 -m nightshift approve runs/<night>        # human gate: saves the fix, never puts it live
python3 -m nightshift stage runs/<night>          # 6-slide demo view of one night
python3 -m nightshift export <night>              # public static copy in site/ (scanned; never deploys)
python3 -m nightshift night --real-days 14        # start from the line's REAL calls (redacted), prove on twins
python3 -m nightshift verify runs/<night>         # every case once with the fix vs the baseline (~$0.90)
python3 -m nightshift arena runs/<night>          # same failures, Claude Code vs Codex, one proof
python3 -m nightshift video runs/<night>          # 50 s captioned demo video
python3 -m nightshift card runs/<night> --day 2   # 1080x1350 post image
python3 -m nightshift publish                     # export + deploy the public site
python3 -m nightshift golive runs/<night>         # dry run; --really puts an approved+verified fix live
python3 -m nightshift serve                       # local web app: every night, real Approve / Reject buttons
python3 -m nightshift reject runs/<night> --why ".."  # human no; the reason becomes a lesson for the next night
./morning.sh                                      # verify, prove, publish, card in one go
python3 -m unittest discover -s tests            # tests, no network
```
Needs `HR_API_KEY` in `nightshift/.env` (git-ignored) and the `demo-factory-nightshift` test checkout. Simulated
calls go through the simulator's own $2/day cap.

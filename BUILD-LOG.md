# Nightshift build log

The Big Hack Game, Stanford vs Berkeley, Nov 2026. Solo, for Berkeley. Built on HarnessRouter.

## Day 1: Mon 11/16

**The problem.** I've been building an AI phone receptionist for plumbers (Godfrey; the demo shop, Sunset Plumbing,
is made up). It answers after-hours calls. When it gets something wrong at 2am, nobody notices until it's too late.
For weeks I fixed it by hand: run test calls, read the failures, tweak the instructions, run them again.

**The idea.** Nightshift does that loop by itself, overnight:
1. Read every graded call from the night.
2. Send the failures to a HarnessRouter agent (Claude Code harness) that finds the cause and makes the smallest fix
   to the instructions.
3. Safety check: never touch the AI/recording disclosure, keep every placeholder, max 30 changed lines.
4. Re-run the failed calls plus calls that already passed, same number of calls before and after.
5. Morning report. Nothing goes live until a person approves.

**What got built today.**
- The whole loop in Python (standard library only), with a HarnessRouter client written from their official API
  reference. 13 tests, no network needed.
- A separate test copy of the agent, so the live phone line and the real instructions are never touched.
- First real baseline tonight: **140 of 178 simulated calls passed (79%)**. Top failures: a failed alert handled
  wrong, booking the earliest slot, a drunk caller, a mumbler, agreeing to a time without checking.

**What broke.**
- An Xcode update mid-build made `git` refuse to run. Nightshift no longer needs git to restore the test copy.
- HarnessRouter's free plan covers agent time but not the model: first task came back `402 no_key`. Added a model
  key under Bring Your Own Key.

**First real night (6:48pm).** HarnessRouter's Claude Code agent (Claude Sonnet 5.5) read the 5 most-failed calls,
found 3 causes and changed 4 lines in under a minute:
- two questions joined with "and" or "if so" still count as two questions;
- after a failed alert, never imply a callback is on its way;
- "today please, but it's not an emergency" isn't an urgent booking.

**The number I almost posted, and why I didn't.** The first re-test said 27/33 to 30/33. But "before" reused the
exact calls that had failed, and those were picked *because* they failed, so "before" looked worse than it was.
Counting fresh calls only, there was no real gain. So Nightshift now re-tests with fresh calls only and reports how
likely the gain is to be luck.

**Clean result:** 23/32 to 29/32 fresh calls (72% to 91%). The failed-alert call went from 1/4 to 4/4. One case
(drunk caller) went 3/4 to 2/4, likely noise. Chance the overall gain is luck: about 5%. Simulator spend today:
about $1.33.

**Approved** by me at 7:02pm (saved, not live; it needs a full run of every case first).

**Night 2 (7:04pm), stacked on night 1's fix: rejected, correctly.** The agent took the next five failures (CO
alarm, a cut hand, heavy swearing, a Mandarin speaker, a tenant asking who pays), found 4 causes, and flagged the
Mandarin one as a conflict with an existing rule instead of forcing a fix. But on fresh calls it went 19/32 to
20/32 (could easily be luck), and the cut-hand call dropped from 2/4 to 0/4: its new triage rule treated injuries
as non-emergencies. So Nightshift now refuses any fix where one case drops by 2 or more, whatever the total says.
That's the point of the proof step: the agent is good, not always right.

**Real-call mode (7:15pm).** Nightshift can now start from the demo line's real calls instead of simulated ones:
read-only from Vapi, flagged by heuristics, phone numbers, names and addresses scrubbed before anything leaves the
Mac. The last 14 days: 5 real calls, all flagged. The agent fixed 2 causes in 2 lines ("bye now" before the caller
said goodbye, which hangs up; claiming to "have a name" for who calls back) and pushed back on the rest: several flags
were the heuristic's mistakes, and it said why. Real calls can't be replayed, so the proof runs on the simulator
cases that test the same behavior. Proof runs tomorrow.

**Next.** Full run of every case on night 1's fix (tomorrow, fresh $2 budget), then night 2 again with the
cut-hand lesson in the instructions.

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

**Next.** First real overnight fix, then the before/after numbers.

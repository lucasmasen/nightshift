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

## Day 2: Tue 11/17

**The scary number.** The morning's full check of night 1's fix (every test case, once each) came back **81 of
178**. Before the fix it was 140. If that were true, the fix had nearly halved the score. It wasn't true: the wifi
dropped mid-run, 83 calls crashed before the AI said a word, and Nightshift counted every crash as a failure.
Of the 95 calls that actually ran, 81 passed (85%). So Nightshift now tells the two apart: a crashed call is set
aside, never scored, and only the crashed calls are re-run (about $0.25 instead of $0.90 for all 178).

**The real number.** With every call run: **140/178 before, 153/178 after (79% to 86%)**, about a 5% chance it's
luck. That's the same odds as night 1's close-up, on more than 5x the calls, so it's now the headline.

**What that check also showed.** 10 cases flipped from pass to fail, each on a single call. One call per case is
noisy: on the same day, with no fix at all, the burst-pipe case scored 3 of 4, where night 1 had it at 1 of 4. So
the 1-of-4 to 4-of-4 story I'd been telling was partly luck. Nightshift got a `recheck` command: re-run just the
flipped cases 4 times, old prompt vs new, and label each "really worse" or "could be noise". That runs Wednesday.

**Real-call fix: not proven.** 34 to 35 of 40 calls; about a 50% chance that's luck. It stays unapproved.

**Any voice agent, not just mine.** Point Nightshift at a folder with an agent's instructions and its call
transcripts. There's no grader for someone else's agent, so the HarnessRouter agent does the triage too: it reads
every call, decides which went wrong and why, then fixes. I tested it on a made-up dental office with two planted
mistakes: the AI promising "the dentist will call you back tonight" (nobody can be paged at night) and an
ibuprofen dose. It found both, called the dose "invented, unsafe medical advice", added two lines, and judged the
other three calls fine. Cost: about 9 cents. It can't prove the fix without a simulator, so the report says
"proposed, not proven".

**Try it page.** The local web app has a page where you paste any agent's prompt and calls and press Run. That's
the Demo Day plan: offer to run it on a judge's own agent.

**Smaller fixes.** The old night had saved its baseline under a different field, which crashed the morning script.
Vercel's newer command line exits with an error after a good deploy, so Nightshift now checks for the "Aliased" line.
The privacy scan read "this is invented" as a person's name and would have blocked the site, so example-agent nights
stay off the public site unless published by hand. Claude Code vs Codex stopped at the $2 cap two-thirds done; it
now saves after each run and finishes Wednesday instead of starting over.

**Spend today:** about $1.99 of simulated calls (the cap) and about $0.10 of HarnessRouter agent time.
**Tests:** 43, all passing.

**Next (Wed).** Codex's half of the comparison, the re-check of the 10 flipped cases, then a fix for anything
that's really worse.

**Afternoon, the first real call (Tue 11/17).** A friend called the demo line and trolled it: gas smell, "reverse a
linked list", "call me at 1 800 deep nuts". Godfrey got the gas safety right but asked for a callback number about 7
times and hung up with "bye now" on swearing. The automatic flags missed the number loop, so real-call nights now
hand the fix agent the whole scrubbed calls; it found all three causes and fixed them in 3 lines. Two privacy bugs
surfaced and were fixed the same hour: a first name slipped past the scrubber in a short excerpt, and "call 911"
was being scrubbed as an address. The call became a permanent test (`real_gas_troll`), every flagged real call now
does automatically, and nightly runs skip calls already reviewed. Loop round 1: the stage slides now tell a
real-call night properly (the real call, the cause that matches it, "A real person called Godfrey."). Round 2: the real-call report says "real call" (it said "simulated") and names the permanent test made from it. Round 3: a lock so the 12:10am script and the 2am nightly can never swap prompts in the test copy at the same time. Round 4: the morning email tells a real-call night as a story (how many people called, what went wrong, the new permanent test). Round 5: `preflight`, a one-command Demo Day check (site, video, project page, repo, web app, schedule) that also builds an offline copy of the slides, reports and video for when the venue wifi dies. Round 6: a version B pitch that opens with the real call (~300 words), waiting on Wednesday's proof number. Round 7: the homepage tells the real-call story (counts and the agent's summary only, no transcript), with the proof marked pending. Round 8: `register-agent`, so other builders' voice agents (Vapi or a folder) are reviewed every night next to Godfrey and land in the same morning email.

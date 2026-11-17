# Submission pack (draft, Mon 11/16)

Where: community.harnessrouter.ai/events/the-big-hack-game, signed in with the Luma email (same account as
app.harnessrouter.ai). Teams lock and projects are due **Thu 11/19, noon PT** (organizers' email 11/17). Source: the organizers'
"Big Hack Game Guides" PDF (email 11/16 2:42pm).

## Checklist from the guide
- [ ] Sign in; event page says "Registered" (school: Berkeley)
- [ ] Builder profile: avatar, bio, GitHub, LinkedIn; add school
- [ ] Your team tab: Create your team, name "Nightshift", no teammates (a team of one is fine)
- [ ] Projects tab: Add your project (5 fields below), Save, then **Submit** (Lucas presses Submit)
- [ ] Share the project link for upvotes (Community Pick = most votes)
- [ ] Post on X with the project link + #HarnessRouter #UHP #BigHackGame (counted automatically, hourly)
- [ ] LinkedIn post with the same tags, paste its link under "Posted on LinkedIn?"
- [ ] Revenue (only if real money comes in): amount + Stripe/PayPal screenshots

**Important:** the guide only tracks **X automatically** and **LinkedIn by link**. The Instagram post may not count
toward the Distribution prize. Ask staff; meanwhile post on X and LinkedIn too.

## The five fields
**Project name:** Nightshift

**Description:**
AI phone agents make mistakes at 2am and nobody notices until a customer complains. Nightshift is the night shift
for your voice agent. Every night it reads the agent's calls, finds the ones that went wrong, and sends them to a
HarnessRouter agent (Claude Code harness) that traces each failure to the instruction that caused it and makes the
smallest fix. A safety check rejects any fix that touches the AI and call-recording disclosure. Then Nightshift
re-runs the failed calls plus calls that already worked, before and after, and leaves a morning report with the
score, the exact change and the reasoning. Nothing goes live until a person approves it. Built on my after-hours
receptionist for plumbers (demo shop: Sunset Plumbing, made up). For anyone running a voice agent.

**Live link or GitHub:** https://nightshift-bighack.vercel.app (published 11/16 ~7:35pm on Lucas's Vercel team
"kakhao", project `nightshift-bighack`; public; to update: `python3 -m nightshift export <nights...>` then redeploy
the site folder with `vercel deploy --prod`).

**Demo video:** auto-built silent captioned cut: https://nightshift-bighack.vercel.app/2026-11-16-184822/demo.mp4
(50 s). Better: upload to YouTube as Unlisted (with Lucas's voiceover) and use that link.

**Harness ID:** `chrn_2714f128e6d744ad8c8fe0fd4844c88e` (Nightshift, claude-code). If a Codex harness is added
for the side-by-side, add its ID as a second line.

## Post drafts (Lucas's voice; add the project link once the project page exists)
Lead number (Lucas picked it 11/17): night 1's fix on every test case, 140/178 to 153/178 (79% to 86%), ~5% chance
it's luck (verify run 11/17). Close-up: 23/32 to 29/32 on the cases it fixed. Simulated calls; say so. Don't lead
with the burst-pipe case's 1/4 to 4/4: on 11/17 the unfixed prompt scored 3/4 on it, so it's noisy.

**X (organizers 11/17: tag @HarnessRouter, add #BeatStanford; 277/280 with link):**
> my ai told a burst pipe caller "i'm letting the team know right now." the alert had failed. nobody was coming.
>
> nightshift fixes that overnight. 178 test calls: 79% passed before, 86% after.
>
> [project link]
> @HarnessRouter #HarnessRouter #UHP #BigHackGame #BeatStanford

**LinkedIn by link**. The Instagram post may not count
toward the Distribution prize. Ask staff; meanwhile post on X and LinkedIn too.

## The five fields
**Project name:** Nightshift

**Description:**
AI phone agents make mistakes at 2am and nobody notices until a customer complains. Nightshift is the night shift
for your voice agent. Every night it reads the agent's calls, finds the ones that went wrong, and sends them to a
HarnessRouter agent (Claude Code harness) that traces each failure to the instruction that caused it and makes the
smallest fix. A safety check rejects any fix that touches the AI and call-recording disclosure. Then Nightshift
re-runs the failed calls plus calls that already worked, before and after, and leaves a morning report with the
score, the exact change and the reasoning. Nothing goes live until a person approves it. Built on my after-hours
receptionist for plumbers (demo shop: Sunset Plumbing, made up). For anyone running a voice agent.

**Live link or GitHub:** https://nightshift-bighack.vercel.app (published 11/16 ~7:35pm on Lucas's Vercel team
"kakhao", project `nightshift-bighack`; public; to update: `python3 -m nightshift export <nights...>` then redeploy
the site folder with `vercel deploy --prod`).

**Demo video:** auto-built silent captioned cut: https://nightshift-bighack.vercel.app/2026-11-16-184822/demo.mp4
(50 s). Better: upload to YouTube as Unlisted (with Lucas's voiceover) and use that link.

**Harness ID:** `chrn_2714f128e6d744ad8c8fe0fd4844c88e` (Nightshift, claude-code). If a Codex harness is added
for the side-by-side, add its ID as a second line.

## Post drafts (Lucas's voice; add the project link once the project page exists)
Lead number (Lucas picked it 11/17): night 1's fix on every test case, 140/178 to 153/178 (79% to 86%), ~5% chance
it's luck (verify run 11/17). Close-up: 23/32 to 29/32 on the cases it fixed. Simulated calls; say so. Don't lead
with the burst-pipe case's 1/4 to 4/4: on 11/17 the unfixed prompt scored 3/4 on it, so it's noisy.

**X (must include the project link + #HarnessRouter #UHP #BigHackGame to count; 276/280):**
> my ai receptionist told a burst pipe caller "i'm letting the team know right now." the alert had failed. nobody was coming.
>
> nightshift fixes that overnight. on all 178 test calls: 79% passed before its fix, 86% after.
>
> [project link]
> #HarnessRouter #UHP #BigHackGame

**LinkedIn (paste the post's link in the project's "Posted on LinkedIn?" field):**
> my ai phone receptionist for plumbers made a promise it couldn't keep.
> a simulated caller had a burst pipe. the alert to the plumber failed. the ai still said "i'm letting the team know right now." nobody was coming.
> so for the big hack game i built nightshift. every night it reads the agent's calls, sends the failures to a claude code agent on harnessrouter, fixes the instructions, and re-tests the fix on fresh calls before anyone wakes up. nothing goes live until a person approves.
> night 1's fix, run on all 178 test calls: 79% passed before, 86% after. odds that's luck: about 5%.
> it also works on other voice agents: point it at a prompt and a folder of call transcripts and it finds the bad calls itself.
> building for berkeley.
> [project link]
> #HarnessRouter #UHP #BigHackGame #BeatStanford

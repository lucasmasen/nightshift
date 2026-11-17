# Submission pack (draft, Mon 11/16)

Where: community.harnessrouter.ai/events/the-big-hack-game, signed in with the Luma email (same account as
app.harnessrouter.ai). Teams lock and projects are due **Thu 11/19, 11:59am PT**. Source: the organizers'
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

**Live link or GitHub:** TBD. Options: (a) public GitHub repo of `nightshift/` (Lucas's call: it's public; the
Godfrey prompt is git-ignored and never in it), or (b) the dashboard page hosted as a static site.

**Demo video:** TBD, record Wed night (60-90 s: the failing call, the overnight fix, the before/after score, approve).

**Harness ID:** `chrn_2714f128e6d744ad8c8fe0fd4844c88e` (Nightshift, claude-code). If a Codex harness is added
for the side-by-side, add its ID as a second line.

## Post drafts (Lucas's voice; add the project link once the project page exists)
**X:**
> my ai receptionist for plumbers passed 79% of its test calls tonight. so i'm building nightshift: it reads every call overnight, fixes what went wrong, and proves the fix before i wake up
> [project link]
> #HarnessRouter #UHP #BigHackGame #BeatStanford

**LinkedIn:**
> building nightshift for the big hack game (berkeley).
> ai phone agents make mistakes at 2am and nobody notices. nightshift reads every call overnight, finds what went wrong, fixes the agent's instructions with a harnessrouter agent, and re-tests the fix before anyone wakes up. nothing goes live until a person approves it.
> night 1 baseline: 140 of 178 simulated calls passed (79%).
> [project link]
> #HarnessRouter #UHP #BigHackGame #BeatStanford

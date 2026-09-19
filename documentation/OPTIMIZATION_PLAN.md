# ASTRO-NEO Optimization Plan

_Written 2026-09-19. This supersedes the "suggested sequencing" at the bottom of `WORKFLOW_REVOLUTION.md` — that document was a menu of ideas; this is a decision about order, made against this branch's actual, evidenced purpose: systematic follow-up astrometry on known, numbered minor planets, via Horizons ephemeris, from one backyard station. Not "confirm brand-new NEOCP discoveries" — the codebase itself has already decided against that (see the branch-purpose note above)._

## The logic behind the order

The mission only has value at the far end: astrometry MPC actually receives and can use. Work backward from there, and the system is a chain: **select a real target → reliably collect frames of it → collect enough good frames per night to matter → get that data to MPC.** A gap anywhere in that chain caps everything upstream of it. Rank by which link is weakest *now*, not by which idea is most interesting.

**Revised 2026-09-19, same day:** the first version of this plan put building submission delivery at Tier 1. That was premature — building a real channel to MPC only makes sense once there's confidence the data flowing into it is actually correct, and that hasn't been demonstrated against real hardware and real sky yet. Everything two nights of work has verified is *structural* correctness (the code runs, doesn't crash, handles failures gracefully) — none of it has verified *astrometric* correctness (does a solved, associated, measured frame actually land on the real asteroid, at the real position, to the accuracy MPC requires). Validation comes first.

## Tier 1 — Prove it works against real sky (do first)

**This is fundamentally your task, not a code change** — it needs a real mount and camera connected to NINA, pointed at an actual numbered target on an actual clear night. What it means concretely:

- Run a real session end-to-end (now safe to do — verified twice tonight that a hardware failure fails cleanly rather than crashing).
- For each frame that solves and associates, check the things structural testing can't: does the annotated PNG (`<FITS_STEM>_annotated.png`) actually show the association landing on the real moving object, not a field star or artifact? Is the WCS RMS grade consistently A/B, not routinely C/D? Is the measured magnitude in the right ballpark versus the catalog vmag?
- Worth a specific look: an old capture queried during tonight's investigation showed a 545 arcsec offset between predicted and solved position — nearly 9 arcminutes. That may be stale/pre-fix data, but it's exactly the kind of number worth checking on a fresh real run before trusting the pipeline's pointing accuracy.

Where I can help once you have real data: reviewing specific captures/logs together, checking whether offsets/grades/associations look right, and building better verification tooling if the existing annotated-PNG/offset/grade data isn't enough to judge correctness quickly. I won't trigger real hardware sessions unprompted — this tier moves at the pace of clear nights and your availability, not mine.

## Tier 2 — The broken last mile (only once Tier 1 gives real confidence)

**Build real submission delivery.** Right now the entire pipeline terminates at "a `.ades.psv` file exists in `/data/psv`." `SubmissionService` was fixed two nights ago to stop *lying* about delivery, but it still can't actually deliver anything. Once Tier 1 has demonstrated the data is trustworthy, build the one real channel that's already half-scaffolded (`smtplib` in `submission.py` — MPC accepts ADES via email), and wire `SubmissionLog.status` through a real lifecycle so the dashboard can show "N nights ready to send" the same way it now shows "N targets ready to bundle."

## Tier 3 — Protect what gets sent — **DONE 2026-09-19** (`ccda37a`)

**Gate PSV/submission readiness on quality, not just quantity.** Turned out the quality data (z-score, grade) was computed and logged but never actually *persisted* anywhere — this plan's original text ("the data already exists") was wrong on that point, caught while implementing. Added a migration (`CandidateAssociation.quality_grade`/`.z_score`), wired `auto_associate` to persist what it already computes, and gated both `/api/psv/targets` readiness and `/api/psv/bundle` generation on grade A/B only. Verified live: all existing historical data correctly now shows `good_obs: 0`/`ready: false` (it predates grading, so that's the honest answer, not a bug), and the bundle endpoint correctly 404s naming the required grade when nothing qualifies.

**Side effect worth knowing about:** rebuilding the image for the migration (this session's first full rebuild, not just a bind-mount+restart) surfaced a real, unrelated live outage — `fastapi~=0.115` had silently drifted to fastapi 0.141.1/starlette 1.6.0 over time, breaking `TemplateResponse`'s old calling convention and 500ing both dashboard pages. Fixed (`f8c8ae7`) and pinned exact versions so a future rebuild can't repeat it silently. This is exactly the kind of thing tonight's Tier 6 test-harness idea would have caught before it ever hit a real page load.

## Tier 4 — De-risk the weakest remaining dependency — **DONE 2026-09-19**

**Revised scope, discovered while starting this tier:** a full WhatsUp replacement isn't achievable as originally written. WhatsUp does two jobs: *discovery* (searching MPC's entire catalog of numbered minor planets for what's near the sky right now — no local equivalent exists without downloading/maintaining MPCORB and running our own orbit propagation, a much bigger project) and *verification* (confirming a cached candidate is actually visible right now — this we can do ourselves). Confirmed with you: keep WhatsUp for discovery, decouple it from the hot path instead of eliminating it.

**Slice 1 (`b378046`):** `check_instant_visibility()` re-verifies a WhatsUp-cached candidate's altitude/sun/moon locally, at the actual current moment, with zero network calls — using the already-cached RA/Dec (Earth's rotation, not the object's own motion, is what makes a snapshot stale on this timescale, so recomputing against current time already fixes what matters). `_first_available` now skips a candidate that fails this (not permanently — it isn't excluded, so it can be picked again later if it rises). Verified live against real astronomical computation (correctly identified current daytime).

**Slice 2 (`ac93da6`):** a periodic background thread now checks whether the WHATSUP cache is genuinely stale (DB-backed check, survives restarts — unlike the reactive path's in-memory cooldown) and refreshes it on its own schedule, so session-start's reactive fallback becomes a rare last resort instead of the routine path. Also found and fixed a startup-handler bug that would have undermined this: `app/__init__.py` unconditionally wiped all WHATSUP candidates on *every* boot regardless of freshness, which would have thrown away good data before the new periodic thread ever got to check it — made that wipe staleness-aware too. Verified live end-to-end: a real restart with a stale cache correctly triggered exactly one refresh, which completed cleanly (found zero targets, correct for daytime).

Residual, accepted: the very first target resolution inside `start_session`'s HTTP handler is still synchronous, so if the periodic thread hasn't caught up yet (e.g. moments after a fresh boot), that one call could still block on the reactive fallback. Now rare rather than routine, and not worth restructuring the already-verified auto-advance chain code to close entirely.

## Tier 5 — Get more usable data per clear night (throughput trilogy, in order)

Confirmed live tonight: every science frame currently pays a full confirmation-exposure-and-solve cycle, serially, with the camera idle while the CPU solves. Fix in this order, each depending on the last:

1. **Confirm once per target**, not once per frame — trust the mount's tracking between consecutive exposures of the same target.
2. **Pipeline capture and analysis** — start exposure N+1 as soon as the camera is free, while frame N solves in the background, instead of making the telescope wait on the CPU.
3. **Adaptive exposure** — feed each frame's measured SNR back into the next exposure's parameters, closing the loop instead of guessing once from a static preset table.

This tier is real scientific value (more frames = more follow-up data = more useful to MPC per clear night), but it's ranked below Tiers 1–4 because it multiplies a supply that currently has nowhere to go (Tier 2) and no guarantee of firing reliably every night (Tier 4). Fix the pipe before widening it.

## Tier 6 — Operational maturity (do opportunistically, low risk)

- **Night bookends**: pre-flight (forecast, disk space, equipment reachable before building a plan) and post-flight (park, one-line night summary, prune old FITS). Purely additive, doesn't touch the hot path.
- **Test harness against `mock_nina`**: two real, live-only-discoverable bugs were found this way tonight, by hand, with a human watching logs. Tier 4 and Tier 5 both add real complexity to the capture loop — do this before them, not after, or pay tonight's discovery cost again by hand each time.

## Tier 7 — Outside the code entirely

**Host reliability.** WSL was directly observed down for 19 hours between two of these sessions. Every tier above assumes the system is actually running at 2am. This has been deprioritized twice at your request, and it stays your call — but it's the one item on this list that silently caps the value of all the others, so it belongs in the plan even parked at the bottom.

## What this plan deliberately does not include

Re-broadening to chase brand-new, unconfirmed NEOCP candidates. That's a materially different, harder mission (looser positional uncertainty, much tighter turnaround, no reliable Horizons ephemeris to point from) than the one this branch has already, deliberately, been built toward. If that's actually wanted, it's a scope decision worth its own conversation — not something to fold into this plan implicitly.

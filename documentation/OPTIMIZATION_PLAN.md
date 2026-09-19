# ASTRO-NEO Optimization Plan

_Written 2026-09-19. This supersedes the "suggested sequencing" at the bottom of `WORKFLOW_REVOLUTION.md` — that document was a menu of ideas; this is a decision about order, made against this branch's actual, evidenced purpose: systematic follow-up astrometry on known, numbered minor planets, via Horizons ephemeris, from one backyard station. Not "confirm brand-new NEOCP discoveries" — the codebase itself has already decided against that (see the branch-purpose note above)._

## The logic behind the order

The mission only has value at the far end: astrometry MPC actually receives and can use. Work backward from there, and the system is a chain: **select a real target → reliably collect frames of it → collect enough good frames per night to matter → get that data to MPC.** A gap anywhere in that chain caps everything upstream of it. Rank by which link is weakest *now*, not by which idea is most interesting.

As of tonight: target selection and frame collection are freshly hardened (two nights of fixes, verified live). The weakest link left is the one nobody has touched at all — **delivery**. No matter how good collection gets, a night of perfect data that never reaches MPC has contributed exactly nothing to the mission.

## Tier 1 — The broken last mile (do first)

**Build real submission delivery.** Right now the entire pipeline terminates at "a `.ades.psv` file exists in `/data/psv`." `SubmissionService` was fixed two nights ago to stop *lying* about delivery, but it still can't actually deliver anything. Build the one real channel that's already half-scaffolded (`smtplib` in `submission.py` — MPC accepts ADES via email), and wire `SubmissionLog.status` through a real lifecycle so the dashboard can show "N nights ready to send" the same way it now shows "N targets ready to bundle."

This is Tier 1 alone, ahead of everything else, because every other improvement in this document increases the *supply* of good data — and right now that supply has nowhere to go.

## Tier 2 — Protect what gets sent

**Gate PSV/submission readiness on quality, not just quantity.** The association pipeline already computes real quality signals per frame (z-score, WCS RMS grade, morphology flags) — `psv.py`'s readiness check ignores all of it and just counts raw frame numbers. Once Tier 1 makes delivery real, a bad night bundled and sent is worse than a bad night never sent — it costs a reviewer's time and this station's credibility. Cheap to do (the data already exists), and it needs to land before or alongside Tier 1, not after.

## Tier 3 — De-risk the weakest remaining dependency

**Replace the WhatsUp scrape with direct Horizons-based visibility.** This isn't the broader "compute visibility for any NEOCP candidate" idea from the revolution doc — narrowed to this branch's real scope, it's simpler and stronger: for a candidate list of known/numbered objects, query Horizons directly (already a proven, fast, reliable dependency in this exact codebase — confirmed repeatedly tonight) and compute altitude/sun/moon visibility ourselves, using the site config and weather gate that already exist. This removes the single remaining external point of failure on the collection path — the thing that cost two nights of retry logic, timeout tuning, and auto-refresh cooldowns to merely make *survivable*. It was never going to be more than survivable as a scrape; replacing it is de-risking collection at the root, not patching around it again.

## Tier 4 — Get more usable data per clear night (throughput trilogy, in order)

Confirmed live tonight: every science frame currently pays a full confirmation-exposure-and-solve cycle, serially, with the camera idle while the CPU solves. Fix in this order, each depending on the last:

1. **Confirm once per target**, not once per frame — trust the mount's tracking between consecutive exposures of the same target.
2. **Pipeline capture and analysis** — start exposure N+1 as soon as the camera is free, while frame N solves in the background, instead of making the telescope wait on the CPU.
3. **Adaptive exposure** — feed each frame's measured SNR back into the next exposure's parameters, closing the loop instead of guessing once from a static preset table.

This tier is real scientific value (more frames = more follow-up data = more useful to MPC per clear night), but it's ranked below Tiers 1–3 because it multiplies a supply that currently has nowhere to go (Tier 1) and no guarantee of firing reliably every night (Tier 3). Fix the pipe before widening it.

## Tier 5 — Operational maturity (do opportunistically, low risk)

- **Night bookends**: pre-flight (forecast, disk space, equipment reachable before building a plan) and post-flight (park, one-line night summary, prune old FITS). Purely additive, doesn't touch the hot path.
- **Test harness against `mock_nina`**: two real, live-only-discoverable bugs were found this way tonight, by hand, with a human watching logs. Tier 3 and Tier 4 both add real complexity to the capture loop — do this before them, not after, or pay tonight's discovery cost again by hand each time.

## Tier 6 — Outside the code entirely

**Host reliability.** WSL was directly observed down for 19 hours between two of these sessions. Every tier above assumes the system is actually running at 2am. This has been deprioritized twice at your request, and it stays your call — but it's the one item on this list that silently caps the value of all the others, so it belongs in the plan even parked at the bottom.

## What this plan deliberately does not include

Re-broadening to chase brand-new, unconfirmed NEOCP candidates. That's a materially different, harder mission (looser positional uncertainty, much tighter turnaround, no reliable Horizons ephemeris to point from) than the one this branch has already, deliberately, been built toward. If that's actually wanted, it's a scope decision worth its own conversation — not something to fold into this plan implicitly.

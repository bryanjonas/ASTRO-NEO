# Revolutionizing ASTRO-NEO

_Written 2026-09-18/19, after two nights of fixing what was broken. This document is different in kind from `IMPROVEMENT_PLAN.md` — that one is a tactical changelog of bugs found and fixed. This one asks a bigger question: now that the plumbing works, what would it take to make this genuinely excellent? These are directions, not commitments — pick what resonates, ignore the rest._

## The north star

Strip away the code and this system exists to do one thing: **turn a telescope you own into published, useful astrometry for a NEO the world is trying to track, as fast and reliably as possible.** Every design choice should be judged against that, not against "does the code look clean."

Looked at that way, the current pipeline has five places where real time and real reliability are lost between "a NEO needs follow-up" and "MPC has the data." The rest of this document is organized around closing those five gaps, plus a few throughput ideas once the gaps are closed.

---

## 1. Kill the WhatsUp scrape — compute visibility ourselves

**The problem.** The single most fragile, slowest thing in the entire system is also the thing everything else depends on: `WhatsUpService` scrapes an MPC HTML form, taking ~40 seconds per call, and it's the *only* path that marks a candidate as "currently observable" (`status=WHATSUP`). Two nights of debugging tonight were spent making this one dependency merely *survivable* (retry logic, a 60s timeout, auto-refresh) — but it's still a scrape of someone else's HTML form, on the critical path of every single session start and every auto-advance step.

**The insight.** We don't need MPC to tell us what's visible. `neocp_fetcher` already pulls the full NEOCP candidate list (127+ objects, with RA/Dec, MPC score, magnitude) directly from MPC's real feed every 15 minutes — no scraping required. And `ObservabilityService`/`target_scoring.py` already contain real astronomical visibility math (altitude, sun/moon separation, horizon mask) — built, tested in isolation, and currently unused. Visibility is a solved problem in software; we're outsourcing it to a slow form when we already have both the raw data and the math sitting in the codebase.

**The move.** Replace the WhatsUp scrape as the source of "what can I observe right now" with a local computation: for every `NeoCandidate` from `neocp_fetcher`, compute altitude/sun-separation/moon-separation ourselves (using the site's lat/long/horizon mask, already loaded for the weather gate), gate on the weather status we already compute, and rank with the brightness+urgency formula from tonight. This is strictly more capable than WhatsUp (it can factor in *our* horizon mask and *our* weather, which MPC's generic tool can't), it's local (milliseconds, not 40 seconds), and it removes an entire external dependency from the critical path. `WhatsUpService` could be kept as a fallback or retired entirely.

**Effort/risk:** Medium-high. This is the single biggest architectural change in this document — it touches target selection, which everything else (auto-advance, session start, readiness) depends on. Worth prototyping alongside the existing path before cutting over, not replacing blind.

---

## 2. Give the system a concept of "a night," not just "a chain of targets"

**The problem.** Right now there is no notion of a night as a bounded thing with a beginning and an end — just a chain that keeps picking targets until it runs out or breaks. There's no pre-flight (is the weather forecast for the *next several hours* survivable? is there disk space? is the mount actually connected before we bother building a plan?) and no end-of-night routine (park the mount, summarize what happened, prune old FITS).

**The move.** Add two bookends:
- **Pre-flight**, run once before the first target of a session: check weather forecast (not just current conditions — Open-Meteo gives hourly forecasts), confirm NINA/mount/camera are actually reachable *before* building an ephemeris plan around them, check disk space for the night's expected data volume.
- **Post-flight**, run when the chain ends for any reason: park the mount, write a one-paragraph night summary (targets attempted, frames captured, PSV-ready count) to a place you'd actually see it, and let old FITS/logs get pruned on a schedule instead of accumulating forever.

**Effort/risk:** Low-medium. Mostly new, additive code around the existing chain; doesn't touch the risky hot path from tonight.

---

## 3. Close the loop on submission — stop dead-ending at "a file exists on disk"

**The problem.** The workflow's actual goal is "MPC has the data," but the app's definition of success is "a `.ades.psv` file exists in `/data/psv`." Nothing after that point is automated or even tracked well — `SubmissionService` now honestly admits it can't deliver anything (fixed last night), but honesty isn't the same as a working delivery path.

**The move.** Now that the honest-status groundwork is in from last night's fix, actually build one real delivery channel — most realistically email (the `smtplib` scaffolding already exists in `submission.py`), since MPC accepts ADES submissions via email. Wire `SubmissionLog.status` through a real lifecycle (`archived` → `sent` → `acked`, using the existing `/submission/{id}/ack` endpoint) so the dashboard can show "3 nights of data still waiting to be sent" as clearly as it now shows "3 targets ready to bundle."

**Effort/risk:** Medium. Well-scoped (one email path), but it's the first time this app would actually contact MPC's real submission channel, so it deserves careful, deliberate testing — this is exactly the kind of thing to build slowly and test against a throwaway address first.

---

## 4. Let quality gate submission, not just quantity

**The problem.** The association pipeline already computes a real quality signal per detection — z-score, WCS RMS grade (A/B/C/D), morphology flags (near-bright-star, low-SNR, elongated) — all from the "association rigor" work. But PSV readiness (`GET /api/psv/targets`) only counts raw frame numbers per night; it has no idea whether those frames are any good.

**The move.** Feed the existing quality grade into the readiness gate: a "ready" badge should mean *good* data, not just *enough* data. Concretely — exclude individual measurements flagged `near_bright_star`/grade-D from PSV bundle generation by default, and only count a night as "qualifying" if its frames meet some minimum grade. This makes the green checkmark actually mean something scientifically, not just numerically.

**Effort/risk:** Low. The data already exists (`_scoring` on each association); this is a filtering/threshold change in `psv.py`, not new infrastructure.

---

## 5. Stop paying a full confirmation-and-solve cycle for every single frame

**The problem.** Confirmed tonight while researching this document: `execute_target_plan`'s exposure loop calls `capture_with_confirmation` once per exposure, and *that single call* does confirmation-exposure → blind-solve → mount-sync → science-exposure → solve → associate, serially, before the loop moves to the next frame. Every science frame pays for a full re-confirmation of pointing, even the 2nd, 3rd, 4th, 5th frame of the same target taken seconds apart. On a mount that's already tracking well, that's real wall-clock time not spent collecting photons — the one resource (clear sky, dark time) this whole system exists to use well.

**The move.** Confirm once per target (or every N frames, or only after a detected re-slew), not once per frame. Trust the mount's tracking between consecutive exposures of the same target, the same way a human observer would. This directly increases frames-per-clear-hour, which is a direct increase in the scientific value of every night — the closest thing this document has to a free lunch.

**Effort/risk:** Medium. Touches the core capture loop, so it needs the same care as everything else in `sequential_capture.py` — but it's a narrowing of behavior (do less, not more), which tends to be lower-risk than adding capability.

---

## 6. Decouple capture from analysis — don't let the camera wait on the CPU

**The problem.** Related to #5 but distinct: even with confirmation done once, the current design still finishes solving and associating frame N *before* starting the exposure for frame N+1 (also confirmed while researching this document — the loop is fully serial). Plate solving under the progressive-radius strategy can take anywhere from a few seconds to ~90s. That's telescope time spent computing, not exposing.

**The move.** Pipeline it: start exposure N+1 as soon as the camera is free, while frame N's solve+association happens in parallel (a background thread/task, matching the pattern this app already uses for the auto-advance chain). The camera should almost never be idle waiting on the CPU.

**Effort/risk:** Higher — this is a genuine concurrency change to a codebase whose stated philosophy is "single-threaded, synchronous, for clear debugging" (per the README). It's worth doing, but it's the one idea here that trades away some of the simplicity this system was deliberately built around. Do it deliberately, not as a drive-by change, and probably after #5, not before.

---

## 7. Adaptive exposure, not just a priori presets

**The problem.** Exposure time/binning/filter are chosen once, up front, from a static preset table keyed on vmag/urgency/motion rate — and never revisited even though every frame's actual measured SNR is already computed during association.

**The move.** Feed measured SNR from frame N back into the exposure decision for frame N+1 on the same target: if the first frame came back much brighter or fainter than the catalog vmag predicted, adjust. This turns exposure selection from an open-loop guess into a closed feedback loop, and it's a natural complement to #5/#6 since it needs per-frame results flowing back quickly.

**Effort/risk:** Medium, and it depends on #5/#6 being done first to matter — no point closing this loop faster than the loop itself runs.

---

## 8. The elephant back in the room: host reliability

Tactically deprioritized twice now, but a genuine "revolutionize this" document would be dishonest to leave out: **none of the above matters on a night WSL doesn't stay up.** We measured a 19-hour outage directly. If the goal is a system that's actually revolutionary in reliability, not just in features, the highest-leverage single change available is moving this stack off a WSL instance that idles itself down onto something that reliably stays on — a small dedicated Linux box (a Pi 4/5 or a cheap NUC draws single-digit-to-low-double-digit watts running this stack fine), or at minimum disabling whatever is currently causing WSL to go idle. This isn't a code change at all, which is exactly why it's easy to skip — but it's the one item on this list that silently caps the value of everything else.

---

## 9. Tests have to grow with the ambition

Two real, live-only-discoverable bugs were found tonight purely by *actually running a session* — no amount of code review or syntax-checking caught them. Every idea above (especially #6 and #7) adds real concurrency and feedback-loop complexity on top of a codebase that currently has close to zero automated test coverage and no CI. Before taking on the higher-risk items here (#1, #6, #7), it's worth investing in a test harness against `mock_nina/` that can exercise the capture loop, association scoring, and chain logic without needing a live mount and a human watching logs each time. Otherwise every future architectural change pays tonight's discovery cost again, by hand, live.

---

## Suggested sequencing, if you want one

**Low-risk, do anytime:** #2 (night bookends), #4 (quality-gated PSV), #9 (test harness) — all additive, none touch the risky hot path.
**The big structural bet:** #1 (kill the WhatsUp scrape) — highest impact, and it would make several of tonight's fixes (the 60s timeout, the auto-refresh cooldown) simply unnecessary rather than merely survivable.
**The throughput trilogy, in order:** #5 → #6 → #7 — each depends on the one before it, and together they're the difference between "the system works" and "the system uses your clear nights well."
**Whenever you're ready to trust it with the real thing:** #3 (real submission delivery).
**Outside the code entirely, whenever you're ready to address it:** #8 (host reliability).

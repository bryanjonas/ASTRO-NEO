# ASTRO-NEO Improvement Plan

_Compiled 2026-09-16 from a full-codebase review (data/config layer, capture & solve pipeline, ingestion & scoring, API/dashboard/reporting/tests). Updated same day after finding and fixing the root cause of the reported "not having success"._

This plan is ordered by **impact and risk**, not by how interesting the work is. Phase 0 items are safety/data-loss issues that should be fixed before any feature work, because they can silently damage equipment, lose a night's data, or submit bad astrometry to the MPC. Later phases are reliability, scientific-value, and cleanup work.

---

## OPEN — Host-level finding (2026-09-17): WSL does not stay up overnight

Observed directly: the WSL instance MELE runs everything in was **down for roughly 19 hours overnight** (SSH and the app's port both refused; `dmesg`/`last reboot` show WSL's kernel booted at 19:02 on 2026-09-17, right when a reconnect attempt succeeded, with no fetcher cycles logged since 21:04 the previous night). `docker compose`'s `restart: unless-stopped` correctly brought the containers back up once WSL itself came back, but **every fix from the previous session — ingestion, nightly backup, fault-tolerant capture — only works while WSL is actually running.** The 3:17am backup never fired (`ops/backup.log` doesn't exist) because nothing was running at 3:17am.

This is a Windows/WSL configuration issue (sleep settings, WSL's own idle-VM shutdown, or something else) outside this repo's control, and outside what's reachable over SSH into WSL itself (`/etc/wsl.conf` has nothing relevant — the controlling settings live Windows-side in `.wslconfig` or power settings). **Deprioritized for now at your request** — revisit once the pattern is clearer. Until it's addressed, treat any "the system has been quiet for N hours" observation as possibly this, not necessarily an application bug.

---

## FIXED — Root cause of the reported outage (2026-09-16)

**The target-ingestion pipeline had been completely dead since 2026-02-13, which is why no session had run since 2026-02-28.**

`docker-compose.yml`'s `neocp-fetcher` service was missing the `./data:/data` volume mount that `api` and `observability-engine` both have. `DATABASE_URL=sqlite:////data/astro_neo.db` therefore resolved to an isolated, ephemeral file inside the fetcher's own container — never the real shared database. That isolated file was never migrated correctly, so **every single fetch cycle for 7+ months crashed** with `sqlite3.OperationalError: no such table: neocpsnapshot`, silently (nothing alerted on it — see Phase 1, item 6 below). Net effect: zero new NEOCP candidates ever reached the database the API/dashboard actually read from, so there was nothing to observe.

**Fix applied:** added the missing `- ./data:/data` line to `neocp-fetcher`'s volumes (commit `4de9f55`). **Verified live:** the fetcher's `/data/astro_neo.db` is now byte-identical to the shared host file; migrations applied cleanly; a real cycle against the live MPC feed pulled in 127 candidates and 127 observation payloads, all visible from the `api` container.

This one-line infrastructure bug — not scoring quality, not solver tuning, not association rigor — was almost certainly the actual reason the system "wasn't having success." Everything below remains worth doing, but nothing else in this document mattered while the front door was closed.

### Also fixed the same session (2026-09-16), in order

1. **`4de9f55`** — the ingestion outage above.
2. **`d5e9f15`** — `WhatsUpService.fetch_targets` had no retry; a single transient network stall (reproduced live on this host) crashed with an unhandled `httpx.ReadTimeout` and a bare 500 from `/api/whatsup/refresh`, blocking session readiness entirely. Added retry-with-backoff (3 attempts). **Caveat:** while testing this, `/api/whatsup/refresh` continued to fail consistently against the live MPC WhatsUp endpoint even after the fix — one manual standalone request succeeded (6.79s) but every request since, across ~8 attempts in 10 minutes, timed out at ~30s. This looks like it could be a temporary rate-limit triggered by that debugging session rather than a permanent break. **Try "Refresh Targets" again later** (outside of a tight retry loop) to confirm it clears up; if it doesn't, the WhatsUp scrape itself needs deeper investigation.
3. **`4ab6594`** — `ops/backup.sh` still ran `pg_dump` against a Postgres service that no longer exists; there was no working backup of the live database at all. Replaced with SQLite's online backup API (safe under WAL), verified the output passes `PRAGMA integrity_check` and contains real data, and scheduled it via cron at 3:17am daily with 14-day retention.
4. **`79b4675`** — `execute_target_plan` raised on any single exposure failure, aborting the entire remaining plan for the night (the single highest-impact reliability gap identified in the review). Now it logs the failure, continues to the next exposure, and only gives up after `automation_max_consecutive_failures` (default 3) failures in a row. `start_session` now correctly marks an early-abort as an error rather than reporting it as "completed."
5. **`23d7aa8`** — `POST /api/astrometry/submit` crashed on *every* call (`archive_report()` didn't accept the `channel` kwarg `SubmissionService.submit()` passed it) — this is why `submissionlog` has been completely empty; not one submission, real or mock, ever completed. Fixed the signature mismatch, and separately fixed `SubmissionService.submit()` unconditionally overwriting `log.status = "sent"` after attempting email delivery (so an unconfigured or failed send was still logged as a false success). The "api" channel now honestly reports `not_implemented` instead of silently claiming success. **Not live-tested against the endpoint itself** (by design — avoided touching anything that could reach a real upstream channel); verified via signature introspection and a clean container restart only.

### Continued 2026-09-17, after finding WSL had been down overnight

6. **`39d3a32`** — `wait_for_mount_ready`/`wait_for_camera_idle` had no error handling around their poll requests, so a single dropped HTTP call to NINA aborted the entire wait immediately. Also, on a camera-idle timeout, nothing ever told NINA to abort the stuck exposure, so the next attempt would likely fail the same way. Both now tolerate transient poll failures (retry within the timeout window) and the camera-idle timeout now sends `abort-exposure` before giving up. **Not tested against live hardware** — verified via syntax check and clean container restart only.
7. **`625a599`** — wired the existing `WeatherService` into `/api/session/start` as a hard safety gate: refuses to start if wind/precipitation/humidity/cloud cover exceed configured thresholds. Fails open (allows the session) if no weather sensor is configured or a fetch fails with no cached data, to avoid breaking sites with no weather monitoring set up. **`config/site_local.yml` on this box has no `weather_sensors` entry, so this gate is currently dormant** — add an entry like `weather_sensors: [{name: "open-meteo", type: "open-meteo"}]` (no API key needed, uses your site's lat/long) to activate it. **Not tested end-to-end** (would require calling `/api/session/start`, which reaches hardware-control code) — verified via syntax check and clean container restart only.

All commits through `31bf0d6` are pushed to `origin/known_targets`.

### Remaining Phase 0 items

- **No authentication on any endpoint**, including session start/stop and equipment/site config — any device on the LAN can control the physical mount. **You've decided against adding auth.**
- The real MPC submission channel (email via local SMTP, or an actual MPC API integration) is still not built — `submit()` now reports honestly when it can't deliver, but nothing can actually deliver yet. Decide whether automated submission is wanted at all, or whether the manual PSV Builder page remains the intended path permanently.
- **Host-level: WSL doesn't stay up overnight** (see the OPEN section above) — deprioritized at your request, but worth revisiting since it undermines everything else.

### Streamlining pass, 2026-09-17 (your request: "what else would streamline this app and its process")

Separated into workflow friction (manual steps the app could do itself) vs. codebase complexity. Started with the highest-leverage workflow item:

1. **`81e120e`** — **Auto-advance to the next target.** `/api/session/start` used to run exactly one target's plan and stop, requiring you to manually re-click "Start Session" after every single target all night. It now runs a background thread that keeps advancing to the next best not-yet-attempted visible target as each one finishes, until the ranked list is exhausted, weather turns unsafe, `automation_max_consecutive_target_failures` (default 2) targets in a row error out, or a stop is requested. A `manual_target_override` still observes exactly one target, unchanged. Each target still gets its own `ObservingSession` row — the chain is just several such rows created in sequence by one thread instead of one per HTTP request. **Not tested end-to-end** (reaches hardware-control code) — verified via syntax checks and clean container restarts only.
2. **`7733fb9`** — **Found and fixed a second major bug while building #1**: `WhatsUpService.get_ranked_targets`'s sort key was inverted (`-vmag` ascending sorts faintest-first, not brightest-first as intended). Confirmed with concrete data. This fed both the old single-target auto-select and the new auto-advance chain, meaning **the system has been preferentially choosing the hardest, faintest, worst-SNR targets available** rather than the easiest ones — plausibly a real contributor to poor results even on nights everything else worked. Fixed to sort ascending on vmag directly.

3. **`41f9233`** — **Auto-refresh WhatsUp targets.** The other manual step blocking unattended operation: a "Refresh Targets" click was required before `/api/session/start` could ever succeed, and the same gap would stop the auto-advance chain dead once its target list ran dry. `_resolve_next_target` now calls a new throttled `WhatsUpService.maybe_auto_refresh()` when no unattempted target is available, gated by a 10-minute cooldown (`whatsup_auto_refresh_cooldown_minutes`) so repeated calls can't hammer MPC. Deliberately **not** wired into `/api/session/ready`, which the dashboard polls every 5 seconds regardless of intent — auto-refresh only fires from an actual attempt to start or advance a session. **Not tested live** (would mean hitting the WhatsUp endpoint or hardware-adjacent code) — verified via syntax checks and clean restarts only.

All commits through `41f9233` are pushed to `origin/known_targets`.

**Natural follow-up not yet done:** the dashboard doesn't show chain progress (e.g. "target 2 of 5 tonight") since that state lives only in the background thread's memory, not the DB. Worth adding if the auto-advance chain proves out in practice.

4. **`11b9857`** — **Surface PSV readiness on the main dashboard.** `GET /api/psv/targets` already computed a solid multi-night readiness flag per target (≥2 qualifying nights, magnitude data, numbered object) — it just only showed as a green checkmark on the separate `/dashboard/psv` page, easy to forget to check. The main dashboard now polls it every 30s (separate from the 5s status poll, since readiness changes slowly) and shows a badge + one-line summary whenever a target is ready. No new backend logic, purely surfacing what already existed. Verified live: page renders correctly, real endpoint data confirmed (5 historical targets, all correctly showing not-ready).

All commits through `11b9857` are pushed to `origin/known_targets`.

**Declined:** overnight alerting on session/chain failure (email/webhook/etc. when something dies unattended) — you decided you don't need separate alerts.

5. **`274dd3a`** — **Dead code removed**: `task_queue.py` (`TASK_QUEUE`, zero references anywhere), `NinaBridgeService.set_ignore_weather` (zero callers, leftover from the removed NINA "bridge" architecture). Also removed `app/api/monitor.py` + `app/services/monitoring.py` (`POST /monitor/ingest`, `GET /monitor/reschedule`) — **these were live, registered endpoints**, not unreachable code, but a half-built external-monitoring integration nothing internal ever called or consumed; removed with your explicit confirmation since it changes API surface. Verified: clean container restart, and the removed endpoint now correctly 404s.

All commits through `274dd3a` are pushed to `origin/known_targets`.

6. **`a214146`** — **Converged the two ranking systems, by enhancement rather than replacement.** `ObservabilityService`/`observability_engine.py` has never actually run in this deployment (`neoobservability` has 0 rows, container never started) — decided against resurrecting it onto the same hot path that already had five other untested changes stacked on it tonight. Instead blended MPC's own urgency score (`NeoCandidate.score`, already populated) into the already-live `get_ranked_targets`: a target with score=100 gets a `whatsup_score_bonus_mag` (default 3.0) magnitude "brightness discount," so an urgent-but-fainter target can outrank a non-urgent brighter one without score alone overriding brightness. Unknown-vmag targets still sort last regardless of score. `ObservabilityService` code is left in place, unused, if the more sophisticated scheduling is wanted later. Verified with a standalone reproduction of the exact sort key against representative data (confirmed the intended ordering in all three cases) plus a clean container restart — not exercised against live WHATSUP-status rows, since none currently exist.

All commits through `a214146` are pushed to `origin/known_targets`.

**Remaining streamlining idea from the original list**, not yet started:
- Duplicated ~150-line test-mode capture path in `sequential_capture.py` still unmerged — this one's a refactor of *active* capture logic (not dead code), so it carries real regression risk and deserves its own careful pass.

### Dashboard/UI pass, 2026-09-17 (your request: "improvements to the website/dashboard")

1. **`8e0bfbd`** — **Fixed a real timezone display bug.** Every capture/log timestamp is stored as naive UTC (`datetime.utcnow()`) and serialized with no timezone marker — confirmed live, e.g. the API returns `"2026-02-21T02:10:15.163510"`. A JS `Date` parses a marker-less ISO string as *local* time, not UTC, so the dashboard was silently displaying every timestamp shifted by your UTC offset (a 9pm-local capture showed up as ~2am). Added `toUtcDate()` (appends `Z` only when no timezone marker is already present, so it won't double-shift an already-aware string if one ever appears) and routed `formatTime`/`formatLogTime` through it. Verified the exact logic with Node against naive, `Z`-suffixed, and offset-suffixed inputs — all three resolve correctly. Deployed, dashboard confirmed still rendering (200).

All commits through `8e0bfbd` are pushed to `origin/known_targets`.

**Other dashboard findings from this pass, not yet done:**
- The dashboard depends on an external CDN (`unpkg.com`) to load Alpine.js — a MELE internet blip (which happened tonight) breaks the whole dashboard even though it controls equipment on the local LAN. Should self-host that JS file.
- All error feedback is a blocking `alert()` popup (5 places) — dated for something meant to stay open for hours; an inline banner would be less jarring.
- No weather-status visibility on the dashboard, despite the hard weather gate added earlier tonight — a refused start currently only shows via the same blocking alert.
- No auto-advance chain progress indicator (carried over from the auto-advance section above).

---

## Phase 0 — Safety & data-loss (do first)

These are the findings that matter even if nothing else in this document ever gets done.

1. **No working backup exists for the live database.**
   `ops/backup.sh` still runs `pg_dump -h db -U astro astro`, but the `db` (Postgres) service was removed from `docker-compose.yml` — the system now runs entirely on SQLite (`/data/astro_neo.db`), shared by three containers. A disk failure right now loses every candidate, ephemeris, capture, and measurement with no recovery path.
   **Fix:** replace `ops/backup.sh` with a SQLite-safe backup (`sqlite3 /data/astro_neo.db ".backup /data/backups/astro-neo-$(date +%F).db"`, safe to run under WAL), schedule it (cron/systemd timer), and prune old backups.

2. **No weather gate on the live session path — the mount can run through rain/wind/cloud.**
   A real weather-aware visibility scorer (`ObservabilityService`, using live Open-Meteo wind/precip/humidity/cloud data) exists and is well-built, but `/api/session/start` and `/api/session/ready` only call `WhatsUpService.get_ranked_targets`, which has no weather awareness at all. The `observability-engine` container that would run this scoring isn't even started. Nothing today stops an unattended session from opening the dome/slewing/exposing in unsafe weather.
   **Fix:** wire a weather safety check (reuse `WeatherService`) directly into `start_session()` in `app/api/session.py` as a hard gate, independent of whether the full observability-engine gets reconciled (Phase 2, item 6).

3. **Zero authentication on any endpoint, including mount control.**
   `app/api/deps.py` has no auth dependency anywhere. Any device on the LAN can call `/api/session/start`, `/api/session/stop`, or the equipment/site config endpoints and the dashboard talks to them with no credentials. This is a physical-hardware safety issue, not just a data issue — a stray device, a misconfigured script, or a compromised LAN client can slew the mount or start exposures.
   **Fix:** add a minimal shared-secret/API-key `Depends()` check on the mutating endpoints (`session.py` start/stop, `equipment_profiles.py`, `site.py`), even before building anything more sophisticated. This is a small change with an outsized safety benefit.

4. **The "automated" MPC submission endpoint is a mock that reports fake success.**
   `app/services/reporting.py::submit_report` (behind `POST /api/astrometry/submit`) returns "Mock submission successful" — email/API channels are literally `# TODO: Implement`. The only real, working submission path is the manual PSV Builder page. As written, an operator could reasonably believe astrometry was submitted to the MPC when it wasn't.
   **Fix, short term:** either remove the endpoint or make it fail loudly/return `501 Not Implemented` until it's real, so nobody is misled. **Fix, longer term:** see Phase 3, item 2.

5. **A second, inconsistent path writes scientifically wrong astrometry if ever called.**
   `app/services/astrometry.py::solve_capture` / `_persist_measurement` persists a `Measurement` using the **frame-center plate-solve RA/Dec and the brightest field star's flux**, not the actual associated asteroid position/photometry that `AnalysisService.auto_associate` computes. If this endpoint is invoked (even for testing), it silently writes wrong astrometry that could end up in a PSV bundle.
   **Fix:** either delete this parallel path or reroute it through `AnalysisService.auto_associate` so there is exactly one way a `Measurement` gets created.

---

## Phase 1 — Observing-loop reliability

The system is explicitly synchronous/single-threaded "for clear debugging" (README), which is a reasonable design choice, but the fault-handling around it is thin. For unattended overnight use, these are what turn "a cloud passed over" or "NINA hiccuped" into a wasted night.

1. **One failed exposure aborts the entire night's plan for a target.**
   `AutomationService.execute_target_plan` (`app/services/automation.py`) raises on any single exposure failure (solve timeout, missing FITS, NINA error), and the exception propagates all the way up through `app/api/session.py`, ending the whole session. Remaining planned exposures are simply abandoned.
   **Fix:** catch per-exposure failures, log/record them against the plan, and continue to the next exposure (or next target) rather than aborting the session. This is the single highest-value reliability fix in the codebase.

2. **NINA polling has no transient-failure tolerance, and no recovery after a timeout.**
   `wait_for_camera_idle` / `wait_for_mount_ready` (`app/services/nina_client.py`) call `_request` on every poll iteration with no try/except — one dropped HTTP call aborts the capture. Worse, if a poll times out mid-exposure, nothing calls `abort_exposure()` to reset NINA's camera state, so the *next* attempt likely fails too because NINA still thinks the camera is busy.
   **Fix:** wrap poll iterations in a short retry-with-backoff; on timeout, explicitly call an abort/reset before giving up.

3. **The confirmation loop's documented retry behavior doesn't exist.**
   `confirmation_max_attempts: int = 3` implies up to 3 re-centering attempts, but the actual code does a single pass and silently treats any exception as "continue anyway" (`confirmation_success = True`). This degrades pointing accuracy without ever actually retrying, and the config parameter is dead/misleading.
   **Fix:** either implement the retry loop the parameter promises, or remove the parameter and document the single-pass behavior honestly.

4. **Stop requests only take effect between exposures, with no UI feedback.**
   The `threading.Event` stop signal (added in the last commit) is checked only in the outer exposure loop, not inside a running capture/solve (which can run for minutes with progressive solve). This may be acceptable behavior, but the dashboard gives no indication that a stop is pending — a user sees nothing change for up to ~3 minutes after clicking Stop.
   **Fix:** surface a "stopping — waiting for current exposure" state in `/api/session/status` and the dashboard.

5. **~150 lines of capture/solve logic are duplicated for `test_mode_slew_only`** (`sequential_capture.py`). Bug fixes to timeout handling or tolerance checks in the main path won't automatically apply here — real drift risk given how actively this logic is being tuned.
   **Fix:** extract the shared logic into one path with a test-mode flag.

6. **No alerting on session failure or repeated errors.** Everything surfaces only via `docker compose logs`; an unattended overnight run that dies at 1am is discovered the next morning, not when it happens.
   **Fix:** add a simple webhook/email notification on session-stopped-due-to-error, reusing/fixing `NotificationLog` (see Phase 2, item 5).

7. **Horizons/Scout calls have no retry-with-backoff** for transient network errors on these known-flaky public APIs — a single `httpx.HTTPError` triggers immediate fallback or failure.

8. Minor cleanup: dead double-assignment of `confirmation_success`/`confirmation_result` in `sequential_capture.py`.

---

## Phase 2 — Target selection & scientific value

1. **Ranking is "brightest first" and ignores everything else.** The live path (`WhatsUpService.get_ranked_targets`) sorts purely by `vmag`. It ignores MPC's own urgency `score`, observation count (a proxy for orbital uncertainty), and how soon a target leaves the observing window — all of which matter more for genuinely useful NEO follow-up than brightness alone.
   **Fix:** fold `NeoCandidate.score`, `.observations`, and time-remaining-in-window into the ranking, not just vmag.

2. **Low-observation-count (high-uncertainty) objects are invisible to the ranker** even though they're exactly what follow-up observing is for. Surface `.observations` count as a first-class ranking input.

3. **Stale candidates are never purged** in the live `WhatsUpService` path (the ranking table just grows forever), even though `ObservabilityService` already implements a staleness cutoff that the live path doesn't use.

4. **Two independent, disagreeing ranking systems exist** (`WhatsUpService.get_ranked_targets` — live, brightest-first; `ObservabilityService` — sophisticated, weather+visibility-aware, unused). Beyond the Phase 0 weather-gate fix, decide: retire the unused one, or replace the live ranking with it. Maintaining two systems that can disagree is a standing correctness risk.

5. **`NotificationLog` is in-memory only** (lost on every restart) and nothing currently pushes events into it. Persist it and wire in the events an unattended observer actually needs to know about: a rare high-value target became observable, weather closed the session, a session auto-stopped on error.

6. **`KPIService.submission_latency_stats` is a placeholder returning zeros** — either wire it to real submission-ACK timestamps or remove the dashboard metric it feeds so it stops looking like real data.

7. **`ScoutClient` brute-forces ~5 parameter-format variants** per request to cope with an undocumented Scout API. It works, but pin the actually-observed working format with a comment/test so a future API change surfaces as a targeted test failure instead of silent fallback churn.

---

## Phase 3 — Data integrity & operations

1. **SQLite foreign keys are never enabled.** `PRAGMA foreign_keys=ON` is missing from `app/db/session.py`'s connect-time pragma setup (WAL and busy_timeout are set, FK enforcement isn't). Every `foreign_key=...` declaration across the models is currently decorative — orphaned rows are silently allowed.

2. **Build the real MPC submission pipeline**, or explicitly scope it out. Right now the only working path is the manual PSV Builder; decide whether automated submission is actually wanted, and if so build it for real (see Phase 0 item 4 for the interim fix).

3. **Legacy MPC 80-column output (`generate_mpc80`) is incomplete** — the code comment admits it "needs rigorous formatting," and packed provisional-ID handling just truncates to 12 characters, which MPC would likely reject. Either finish it or remove it if PSV/ADES is the only format actually used.

4. **Concurrent multi-process writers to one SQLite file.** `api`, `neocp-fetcher`, and `observability-engine` (if reactivated per Phase 2) all write to the same file. WAL + busy_timeout mitigates lock contention but doesn't eliminate it under sustained concurrent writes — worth monitoring, or funneling hot-path writes through a single writer.

5. **Dual schema-creation paths can desync.** `neocp_fetcher.py::main()` now calls `init_db()` directly (creates tables via SQLModel metadata) alongside the normal Alembic-on-startup path. On a brand-new database, whichever runs first "wins," and if `init_db()` runs first, the next `alembic upgrade head` can fail trying to create tables that already exist.

6. **No unique constraint prevents two "active" rows.** `SiteConfig.is_active` and `EquipmentProfileRecord.is_active` are plain booleans with no partial-unique index, even though downstream code assumes exactly one active row exists.

7. Small fixes: `ops/.sops.yaml` still has a placeholder age key (secrets pipeline in `ops/secrets/README.md` isn't actually usable as configured); duplicate `from .report import Measurement` import in `app/models/__init__.py`; `ObservingSession.status` docstring says "active, paused, ended" but the real values are `active/stopping/stopped` — fix the comment so it stops being a trap for the next person.

---

## Phase 4 — Testing & CI

Current state: **no CI pipeline exists at all**, and of the 5 files under `tests/`, only `test_nina_timing.py` has real assertions — the rest are manual/interactive scripts that require a live NINA instance and print output rather than pass/fail. For a system that drives physical hardware, this is the biggest structural gap after the Phase 0 safety issues.

1. Add real pytest unit tests for the pure-logic, no-hardware-required pieces first — highest ROI, easiest to write:
   - `AnalysisService.find_best_match_scored` (the new scored-association logic — currently has zero test coverage despite being actively tuned)
   - `AnalysisService.get_wcs_quality`
   - PSV/ADES bundle generation and validation (`reporting.py`)
   - `WhatsUpService.get_ranked_targets` once it's improved per Phase 2
2. Add a CI workflow (GitHub Actions) that runs these on every push/PR — right now nothing blocks a regression in solve/association/reporting logic from reaching the box that controls the physical mount.
3. Convert the manual NINA-dependent scripts (`test_confirmation_workflow.py`, `test_mount_sync.py`, etc.) to run against `mock_nina/` in CI where feasible, or clearly namespace them as manual/integration-only (e.g. move to `scripts/manual/`) so `tests/` means what it says.

---

## Phase 5 — Cleanup & documentation

1. **README.md documents Postgres as the metadata store** ("db – PostgreSQL database for all metadata", `localhost:5432 user astro`) — this is stale; the system runs entirely on SQLite (`/data/astro_neo.db`). Fix the README and `alembic.ini`'s hardcoded Postgres URL (harmless since `alembic/env.py` overrides it, but misleading to read).
2. Remove or wire in dead code: `app/services/task_queue.py` (`TASK_QUEUE`, never imported anywhere); `app/api/monitor.py` / `app/services/monitoring.py` (`reschedule_queue()` results are never consumed by the automation loop — a half-built feature).
3. `app/api/site.py::activate_site`'s background horizon-fetch task swallows all failures into a bare `except Exception: logger.error(...)` with no retry or caller-visible status — at least make the failure visible somewhere a user would see it.
4. `.gitignore` still excludes `postgres_data/`, a dead entry from the removed Postgres service.

---

## Suggested sequencing

Given one person operating this unattended overnight, the fastest path to meaningfully safer/more reliable operation is:

**Week 1:** Phase 0 in full (backup, weather gate, minimal auth, kill/fix the fake submission endpoint, fix the duplicate astrometry-writing path).
**Week 2–3:** Phase 1 items 1–3 (per-exposure fault isolation, NINA retry/recovery, confirmation retry) — this is where most "lost observing nights" actually come from.
**Ongoing:** Phase 4 item 1 (unit tests for the pure-logic pieces) alongside any future changes to association/scoring, so the actively-evolving scoring logic stops being untested.
**Later:** Phases 2, 3, 5 as time allows — real scientific/operational value but lower urgency than the above.

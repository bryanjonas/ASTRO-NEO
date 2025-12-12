# App-Managed Capture Loop

## Goal
Replace the current sequential NINA sequence approach with an **app-managed control loop** that iteratively:

1. Predicts the target's RA/Dec for the precise current timestamp + observatory location.
2. Commands NINA to slew (and optionally focus/guided) to that predicted location.
3. Starts a single exposure and waits for completion + plate-solve metadata.
4. Logs the result (success/failure/wcs) into `SESSION_STATE` and the database.
5. Recomputes the target position for the next exposure before looping.

Controlling each shot centrally lets the app recap the exact motion of fast-moving NEOs and surfaces exposure-level progress on the overview tab.

## Scope
- Build on the new `EphemerisPredictionService` so every loop iteration uses the freshest MPC ephemeris cache.
- Reuse the progress data returned by `nina_status.sequence.progress` to know when exposures/endpoints complete.
- Keep the existing logging traces that call out "guiding started", "sequence loaded", and "NINA solved" but translate them into the new loop’s steps.
- Ensure the loop remains fully sequential, meaning the next exposure isn’t requested until the prior capture/solve completes.

## Plan

### 1. Define new loop contract
- Add a helper (`app/services/capture_loop.py`) that receives a target descriptor (`name`, `candidate_id`, `count`, `exposure_seconds`, `filter`, `binning`) and orchestrates repeated `slew → start_exposure` retries.
- Each iteration should:
  - Predict RA/Dec with `EphemerisPredictionService.predict`.
  - Log the planned target/ra/dec.
  - Issue `bridge.slew(...)`.
  - Optionally ensure the guider is on (call `bridge.start_guiding()` once at the beginning, stop at the end or upon failure).
  - Call `bridge.start_exposure()` via the camera API (single exposure).
  - Wait on the returned response (including plate solve); log success/failure and feed the capture data into `SESSION_STATE`.
  - If the exposure succeeded, record in `CaptureLog`/`AstrometricSolution`.

### 2. Integrate into AutomationService
- Replace the sequence builder block inside `run_sequential_target_sequence` with a call to the new loop helper for each target.
- Ensure `guiding_started`/`stop_guiding` logic still runs (start guiding at the start of the loop, stop when all exposures done).
- Maintain the summary logging at the end (total solved vs. requested).
- Use the plan metadata to mark how many iterations remain and log at each step (`LOG: Slew to RA=...`, `LOG: Exposure 3/5 complete`).

### 3. Update monitoring/session tracking
- The loop should add each exposure to `SESSION_STATE` as `kind="exposure"` (existing `start_exposure` helper can be reused).
- Keep the `ImageMonitor`/`SequenceProcessor` path as a fallback for automated sequences triggered by the legacy workflow (if still needed), but ensure the new loop bypasses it.

### 4. Dashboard logging
- Surface a new overview banner or log line for each exposure: “Exposure X/Y completed, solver succeeded” or “Exposure X/Y failed → retrying”.
- Use the new `nina_status.sequence.progress` data to show the predicted “In-progress exposure” even though the app is steering exposures itself.

### 5. Rollout and testing
- Add CLI/test scripts to run the loop manually against `mock_nina`.
- Document the new workflow in this document plus `QUICK_READ.md`.
- Validate by running `docker compose exec api pytest` (or manual endpoints) and observing the overview tab for accurate exposure counts.

If you want, I can now implement the capture loop module and wire it into `AutomationService` so the app controls each exposure sequentially. Let me know.  

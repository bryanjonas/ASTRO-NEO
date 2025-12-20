# ASTRO-NEO System Description (LLM-Optimized)

This document is the canonical description for LLMs that need to understand ASTRO-NEO holistically. It summarizes the architecture, data flows, automation behaviors, integrations, and operational assumptions so downstream reasoning can reference a single authoritative source.

## 1. Mission & Scope
- **Mission**: Automate end-to-end NEOCP follow-up—ingest candidates, decide what to observe, steer hardware, solve images, and deliver MPC-compliant reports.
- **Key goals**: accuracy (Horizons-based pointing), reliability (per-exposure confirmation + defensive automation), and traceability (SESSION_STATE logs + file monitoring).
- **Deployment**: Docker Compose services running FastAPI (`api`), background workers (`neocp-fetcher`, `observability-engine`, `astrometry-worker`, `image-monitor`, `sequence-processor`), and supporting simulators (`mock_nina`, `synthetic-targets`).
- **Languages & frameworks**: Python 3.11 backend (FastAPI/Uvicorn, SQLModel + Alembic), HTMX/Alpine-driven frontend, Postgres database, and Docker volumes for shared `/data/fits`.

## 2. Architecture & Data Flow
1. **Candidate ingestion**: `neocp-fetcher` polls the MPC NEOCP feed, seeds `NeoCandidate` records, and keeps source metadata up to date.
2. **Observability engine**: `app/services/observability.py` enriches each candidate with site visibility, weather PASS/FAIL gates, and composite scores using a six-component model (MPC priority, altitude, time-to-set, motion rate, uncertainty, arc extension).
3. **Priority selection**: `AutomationService.run_sequential_target_sequence()` queries the sorted `NeoObservability` list, builds capture plans, and drives each target sequentially.
4. **SESSION_STATE**: Every capture/request is logged (`kind="exposure"`, target, index, predicted RA/Dec, platesolve info) before filesystem monitoring fills in file paths.
5. **Processing**: Once the image monitor reports a FITS file, `sequence_processor` or local `astrometry-worker` runs (if NINA didn’t plate-solve). Final astrometric products drive ADES generation and MPC submission.

## 3. Automation Control Loop
The sequential target workflow is implemented in `app/services/automation.py` (plan orchestration) together with `app/services/capture_loop.py` (per-target capture flow). The loop proceeds as follows:

1. `AutomationService` polls the highest-ranked `NeoObservability` entries, builds a sequence plan, and hands each target descriptor to `CaptureLoop`.
2. `CaptureLoop` optionally runs `TwoStageAcquisition` (app/services/acquisition.py) for the first exposure, ensuring Horizons ephemerides and mount readiness plus a short confirmation exposure before the main loop.
3. For each science frame, the loop predicts the current coordinates, slews via `NinaBridgeService`, and takes a confirmation exposure that re-solves the field before the primary capture.
4. If the confirmation solve reports an offset exceeding 120″ the loop re-slews, else it proceeds to the main exposure configured with the target’s preferred binning/filter/exposure time.
5. Each capture result is recorded into `SESSION_STATE`, including predicted coordinates, platesolve status, and placeholders for the file path; downstream services later fill the complete metadata.

This structured control loop ensures that automation never stalls on plate solve failures—confirmation failures only log warnings and the science exposure still runs—while maintaining traceability through `SESSION_STATE` events.

### 3.1 Two-Stage Acquisition
- `TwoStageAcquisition` (app/services/acquisition.py) fetches fresh JPL Horizons ephemerides (force horizon refresh around slew time) and performs:
  1. Predicted RA/Dec with topocentric corrections.
  2. Slew via `NinaBridgeService` and wait for mount/camera readiness.
  3. Short (≤8 s) binning=2 **confirmation exposure**, recorded as `{target}-CONFIRM`.
  4. Plate solve verification, offset calculation (haversine) and, if >120″, a refinement slew to the solved coordinates.
  5. Logs final status via `SESSION_STATE`.

### 3.2 Per-Exposure Confirmation Loop
- Each science exposure re-runs prediction → slew → confirm → re-slew → science capture.
- Confirmation exposures are exactly 5 seconds with bin2 for fast plate solving; they may fail gracefully (logging warns but science exposure still runs).
- Re-slew occurs only for offsets >120″; failures log warnings but continue, ensuring automation never stalls.
- **Confirmation vs. science solving**: confirmation shots still set `solve=true` so NINA can report immediate offsets, but *science* exposures now set `solve=false` and rely entirely on the local astrometry pipeline (see Sections 4 and 5) for post-capture WCS generation.
- **Timeout handling**: Confirmation exposures with waitForResult=true and solve=true get exposure + 90s timeout to allow for plate solving; non-solving exposures get exposure + 30s.

## 4. NINA Bridge Contract
- The bridge (`nina_bridge/main.py`) exposes `/capture` endpoints and strictly forwards only **minimal parameters**: `duration`, `save=true`, `solve=true/false`, and `targetName`.
- Science exposures now call the bridge with `solve=false`, `waitForResult=true`, `getResult=true`, `omitImage=false`. Confirmations still request solves so offsets can be measured without waiting for the local solver.
- Target parameter alias now matches the client (`Query(alias="targetName")`), avoiding dropped names.
- NINA does **not** return file paths; the bridge returns `{"Success": true, "Response": {"platesolve": ..., "file": None, "nina_response": ...}}`.
- Downstream services (`capture_loop`, `acquisition`, `automation`, `app/services/bridge.py`) treat `file` as optional and rely on filesystem monitoring to fill real paths.
- Bridge response includes plate solve metadata when available but still leaves file metadata empty, encouraging defensive programming.

## 5. Filesystem Monitoring & Plate-Solve Backlog
- `app/services/image_monitor.py` watches `/data/fits` recursively for new `.fits` files, parsing filenames with the pattern `{TARGET}_{YYYY-MM-DD}_{HH-MM-SS}__{EXPOSURE}s_{FRAME}.fits`.
- On startup the monitor now indexes *all* existing FITS files (last-scan time starts at 0) and keeps a cache so previously-missed captures can be matched later. Every scan runs two extra passes:
  1. **Backfill**: for any `SESSION_STATE` capture without a `path`, the monitor searches cached files (matching by target, exposure, timestamp tolerance) and retroactively links the FITS, enabling the solver tab to show frames that existed before the service booted.
  2. **Pending-solve queue**: unsolved files (no WCS) are enqueued with solver status `pending`. The monitor retries each pending entry until `solve_fits` succeeds or the max retry count (currently 3) is exceeded, spacing attempts by 30 s to avoid thrashing.
- When local solving succeeds the monitor updates `solver_status=solved`, triggers `_trigger_processing()` (ADES/association), and logs a success event. Repeated failures mark the capture as `solver_status=error` with the recorded message so the UI and DB can surface the failure explicitly.
- Confirmation exposures (suffix `-CONFIRM`) still get skipped for downstream science processing; only LIGHT frames without the confirmation suffix enter the queue.
- The monitor remains the single source of file paths—bridge responses stay `None`—and all automated solving now originates from this backlog-aware worker.

## 6. Target Selection, Scoring, & Presets
- Scoring resides in `app/services/target_scoring.py`. Each component returns 0–100:
  - MPC priority (direct MPC score)
  - Altitude (higher is better; 60°+ scores 100)
  - Time-to-set (more remaining time scores higher)
  - Motion rate (slower movers score higher but penalty is gentle)
  - Uncertainty (smaller 3σ arcsec gets a higher score)
  - Arc extension (recent observations get priority)
- Weights are configurable via settings but default to 30/25/15/10/10/10.
- Exposure presets come from `app/services/presets.py`: bright/medium/faint templates with fallback logic and motion-aware exposure reduction (target trailing limited to 5 pixels by reducing exposure duration and increasing counts as needed).
- Fast movers (>30″/min) trigger automatic exposure reductions (`max_exposure = 7.5″ / rate`) to keep trailing limited, while ensuring total integration time is maintained by increasing frame counts.

## 7. Sequence Builder & Legacy Support
- Legacy NINA sequences (SequenceRootContainer + per-target DeepSkyObjectContainer) remain available via `nina_bridge/sequence_builder.py` and are still triggered by `AutomationService` when running sequential target plans.
- Each container takes exactly one exposure, enabling motion tracking and precise per-frame logging. Sequence progress is surfaced via `nina_status.sequence.progress`.
- Sequence definition details (HMS/DMS conversion, container strategy, error behavior) follow NINA’s Advanced Sequencer requirements.

## 8. File Consumers & Downstream Processing
- `sequence_processor` handles NINA-solved images, noting WCS headers and persisting `AstrometricSolution`.
- `astrometry-worker` runs `solve-field` when NINA lacks a solution, using RA/Dec hints from the target descriptor.
- Captures feed into ADES generation; each record includes target name, exposure info, and plate solve metadata. ADES files submit to MPC once enough frames are solved.
- `SESSION_STATE` ensures exposures are traceable even before files arrive—every event logs `target`, `index`, `started_at`, `predicted_ra_deg`, `predicted_dec_deg`, `platesolve` result, and a placeholder `path`.

## 9. Testing & Evidence
- Real hardware sweeps (2025-12-13) prove NINA accepts minimal parameters, saves files under `/data/fits/YYYY-MM-DD`, and never returns file paths.
- File monitor tests confirm detection patterns and correlation logic works with target-named filenames (`MINIMAL-TEST_2025-12-13_19-19-15__2.00s_0000.fits`).
- `scripts/nina_api_monitor.py` exercises status, slew, and capture endpoints while logging errors such as “No capture processed” (to keep automation aware of transient rejects).

## 10. Remaining Work & Operators' Hooks
- Tune the new backlog correlation heuristics (target/exposure/timestamp tolerances) to handle edge cases such as multi-night sessions or renamed folders, and surface diagnostics if multiple FITS candidates match a single capture.
- Monitor the pending-solve queue health: if solves repeatedly fail after the configured retries, the UI now shows `solver_status=error`, but operators still need to investigate hardware/seeing issues or rerun solves manually.
- Verify plate solving for confirmation images: ensure the `-CONFIRM` frames continue to request `solve=true` and that NINA/automation still report offsets correctly; add regression tests if necessary.
- Verify timing between science exposures: confirm the bridge/UI resumes the capture loop promptly after `exposure_seconds + readout` instead of waiting excessively long before the next slew/verification.
- Image monitor still fails to assign some previously uncataloged FITS files to their session captures; refine the backfill matching logic until every on-disk frame resolves to a `SESSION_STATE` entry.
- Ensure `SESSION_STATE` continues recording solver status transitions so the dashboard's "Exposure X/Y solved/failed" summary stays accurate and front-end events can surface per-exposure banners.
- Use this document as the single source for LLM reasoning; all prior design notes have been archived to `documentation/archive` for historical reference.

### 10.1 Recent Fixes (2025-12-19)
- **Fixed plate-solve backlog**: The solver now writes WCS headers back to the original FITS file (not just `.wcs` sidecar), ensuring `has_wcs` detection works correctly and solved files propagate to `AstrometricSolution`.
- **Enhanced error logging**: Plate solve failures now include full exception type, message, and traceback in logs; retry queue status logged with attempt counts and remaining queue size.
- **WCS propagation**: Added `_copy_wcs_to_fits()` function that copies all WCS keywords from `.wcs` file back to original FITS header, making solved images compatible with downstream processing.
- **Solver status tracking**: Image monitor now updates `has_wcs` flag and `solver_status` field immediately after successful solve, before triggering processing.
- **Fixed confirmation exposure timing**: Reduced confirmation exposures from 10-15s to exactly 5s with bin2 for faster plate solving and reduced overhead between science exposures.
- **Improved timeout handling**: Increased plate-solve timeout from exposure + 10s to exposure + 90s for `waitForResult=true` with `solve=true`, preventing premature timeouts during plate solving.

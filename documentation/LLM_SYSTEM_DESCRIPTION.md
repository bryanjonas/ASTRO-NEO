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
Describe the sequential target workflow in `app/services/automation.py` + `app/services/capture_loop.py`.

### 3.1 Two-Stage Acquisition
- `TwoStageAcquisition` (app/services/acquisition.py) fetches fresh JPL Horizons ephemerides (force horizon refresh around slew time) and performs:
  1. Predicted RA/Dec with topocentric corrections.
  2. Slew via `NinaBridgeService` and wait for mount/camera readiness.
  3. Short (≤8 s) binning=2 **confirmation exposure**, recorded as `{target}-CONFIRM`.
  4. Plate solve verification, offset calculation (haversine) and, if >120″, a refinement slew to the solved coordinates.
  5. Logs final status via `SESSION_STATE`.

### 3.2 Per-Exposure Confirmation Loop
- Each science exposure re-runs prediction → slew → confirm → re-slew → science capture.
- Confirmation exposures are always short with bin2 and max 8 s; they may fail gracefully (logging warns but science exposure still runs).
- Re-slew occurs only for offsets >120″; failures log warnings but continue, ensuring automation never stalls.
- Science exposures follow through `NinaBridgeService.start_exposure()` with target-specific duration/binning. Plate solves provided by NINA are recorded and, if missing, the local pipeline solves later.

## 4. NINA Bridge Contract
- The bridge (`nina_bridge/main.py`) exposes `/capture` endpoints and strictly forwards only **minimal parameters**: `duration`, `save=true`, `solve=true/false`, and `targetName`.
- Target parameter alias now matches the client (`Query(alias="targetName")`), avoiding dropped names.
- NINA does **not** return file paths; the bridge returns `{"Success": true, "Response": {"platesolve": ..., "file": None, "nina_response": ...}}`.
- Downstream services (`capture_loop`, `acquisition`, `automation`, `app/services/bridge.py`) treat `file` as optional and rely on filesystem monitoring to fill real paths.
- Bridge response includes plate solve metadata when available but still leaves file metadata empty, encouraging defensive programming.

## 5. Filesystem Monitoring & File Correlation
- `app/services/image_monitor.py` watches `/data/fits` recursively for new `.fits` files, parsing filenames with the pattern: `{TARGET}_{YYYY-MM-DD}_{HH-MM-SS}__{EXPOSURE}s_{FRAME}.fits`.
- Incoming files are correlated against `SESSION_STATE` capture records by target name, timestamp tolerance (±30 s), exposure time tolerance (±0.5 s), and optional frame number.
- Once matched, the monitor updates the capture record’s `path`, `status`, and triggers downstream processing (plate solving, ADES generation, MPC submission).
- Confirmation exposures use the `-CONFIRM` suffix and are excluded from science processing.
- The monitor is the **only source** of truth for file paths—bridge responses remain `None`.

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

## 10. Remaining Work & Operators’ Hooks
- Finish file-monitor → capture correlation (matching exposures with the predicted metadata) as outlined above; currently `image_monitor` needs the enhanced matching logic.
- Expand processing pipeline to trigger plate solving/ADES generation once the monitor fills `path`.
- Monitor `SESSION_STATE` for new capture entries to keep the dashboard’s “Exposure X/Y solved/failed” summary accurate and for front-end events to surface per-exposure banners.
- Use this document as the single source for LLM reasoning; all prior design notes have been archived to `documentation/archive` for historical reference.

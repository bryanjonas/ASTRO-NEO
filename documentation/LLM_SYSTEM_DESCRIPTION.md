# ASTRO-NEO System Description (LLM-Optimized)

This document is the canonical description for LLMs that need to understand ASTRO-NEO holistically. It summarizes the architecture, data flows, automation behaviors, integrations, and operational assumptions so downstream reasoning can reference a single authoritative source.

## 1. Mission & Scope
- **Mission**: Automate end-to-end NEOCP follow-up—ingest candidates, decide what to observe, steer hardware, solve images, and deliver MPC-compliant reports.
- **Key goals**: accuracy (Horizons-based pointing), reliability (per-exposure confirmation + synchronous pipeline), and traceability (DB-backed session and capture records).
- **Deployment**: Docker Compose services running FastAPI (`api`), background workers (`neocp-fetcher`, `observability-engine`), and Postgres (`db`).
- **Languages & frameworks**: Python 3.11 backend (FastAPI/Uvicorn, SQLModel + Alembic), Alpine-driven minimal dashboard, and Docker volumes for shared `/data/fits`.

## 2. Architecture & Data Flow
1. **Candidate ingestion**: `neocp-fetcher` polls the MPC NEOCP feed, seeds `NeoCandidate` records, and keeps source metadata up to date.
2. **Observability engine**: `app/services/observability.py` enriches each candidate with site visibility and composite scores using the six-component model (MPC priority, altitude, time-to-set, motion rate, uncertainty, arc extension).
3. **Session start**: `/api/session/start` selects the highest-ranked visible target (or a manual override) and records an `ObservingSession` entry.
4. **Sequential capture loop**: `AutomationService` builds a target plan and runs `SequentialCaptureService`, which executes a synchronous per-exposure pipeline.
5. **Association & reporting**: Solved frames feed source detection and association, then flow into MPC-compliant report generation.

## 3. Sequential Capture Pipeline
The sequential capture workflow is implemented in `app/services/automation.py` and `app/services/sequential_capture.py`.

For each exposure:
1. **Horizons ephemeris**: Query fresh RA/Dec via `HorizonsClient` using site config.
2. **Confirmation loop** (max 3 attempts): slew, take short confirmation exposure, poll for FITS, solve locally, compute pointing offset, re-slew if needed.
3. **Science exposure**: capture the main frame with NINA (no NINA solve).
4. **Local solve**: run `solve-field` locally, persist WCS and `has_wcs`.
5. **Detect + associate**: detect sources and match against predicted ephemeris, create `CandidateAssociation` if matched.

All operations are synchronous and DB-backed (no in-memory SESSION_STATE).

## 4. File Polling & Local Solving
- **FITS location**: NINA writes to `/data/fits` (Docker named volume `nina_images` bound to `${NINA_IMAGES_HOST_PATH:-./data/fits}`).
- **File detection**: `app/services/file_poller.py` synchronously polls for `{TARGET}_*.fits` with exponential backoff (100ms to 3.2s).
- **Solving**: `app/services/solver.py` runs `solve-field` locally in the API container; no remote astrometry worker exists.

## 5. Target Scoring & Presets
- Scoring resides in `app/services/target_scoring.py`. Each component returns 0–100:
  - MPC priority
  - Altitude
  - Time-to-set
  - Motion rate
  - Uncertainty
  - Arc extension
- Weights default to 30/25/15/10/10/10 and are configurable via settings.
- Exposure presets come from `app/services/presets.py`: bright/medium/faint templates with motion-aware exposure reduction.

## 6. UI & Operators
- Minimal dashboard served at `/dashboard` with Alpine-based status polling.
- Primary API endpoints: `/api/session/start`, `/api/session/stop`, `/api/session/status`, `/api/observability`, `/api/captures`.

## 7. Testing & Evidence
- Local NINA integration is verified via direct REST calls (no bridge service).
- The solver pipeline writes WCS headers back into FITS for downstream compatibility.
- File polling is synchronous and tied to the capture call, avoiding background correlation/backfill logic.

## 8. Remaining Work & Operators' Hooks
- Validate confirmation loop behavior across multiple exposures and slow readouts.
- Ensure Horizons queries remain within API rate limits for long sessions.
- Monitor local `solve-field` runtime and index coverage for target magnitude ranges.

## 9. Next Fixes (Current)
- **MPC ephemeris 404s**: `MPC_EPHEMERIS_URL` is returning 404; update the endpoint or disable MPC fallback if Horizons-only is acceptable.
- **Target selection after changes**: Observability now filters out targets lacking Horizons data; refresh scores or restart `observability-engine` before starting new sessions.
- **Session runs already started**: A session that started before the Horizons filter refresh will still try bad targets; stop and start a new session after refresh.
- **Dashboard log/captures**: `/api/logs` and `/api/captures?session_id=...` power the UI; verify the API is restarted after changes.

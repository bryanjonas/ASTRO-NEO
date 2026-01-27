# ASTRO-NEO System Description (LLM-Optimized)

This document is the canonical description of ASTRO-NEO for LLMs. It reflects the current runtime behavior, data flow, and operational constraints.

## 1. Mission & Scope
- Automate NEOCP follow-up: ingest candidates, rank targets, slew, capture, solve, associate, and produce MPC-compliant PSV output.
- Reliability and traceability: all actions are DB-backed, synchronous, and logged.
- Containers: Docker Compose services for API, fetchers, and supporting tools. Shared data lives under `/data`.

## 2. Core Services
- **API**: FastAPI app (`api`) orchestrates sessions, capture flow, solving, association, and PSV output.
- **NEOCP Fetcher**: polls MPC feeds and stores `NeoCandidate` + snapshots.
- **Observability**: computes visibility/score data used for ranking and filtering.

## 3. Data Model (SQLite)
Key tables (SQLite):
- `NeoCandidate`: candidates from NEOCP.
- `NeoEphemeris`: ephemeris samples (Horizons or Scout).
- `CaptureLog`: per-exposure capture metadata and solve results.
- `CandidateAssociation`: association result linking capture to target position.
- `Measurement`: astrometric measurement for PSV output.
- `ObservingSession`: session lifecycle + stats.

## 4. Target List + Session Readiness
- UI lists **top 5** candidates (ranked by `vmag` by default).
- `/api/session/ready` enables Start Session only when:
  - WhatsUp targets exist (manual refresh required).
  - Recent Horizons ephemeris exists for the top targets.
- Refresh endpoint (`/api/whatsup/refresh`) pulls targets and ephemeris.

## 5. Ephemeris Handling
- Horizons is the authoritative source for local pointing/association.
- Before starting exposures for a target, the system fetches a **window** of ephemeris points:
  - Window: **next 15 minutes**
  - Step: **1 minute**
- All fetched rows are persisted in `NeoEphemeris`.
- Association uses the capture timestamp and interpolates between nearest ephemeris points if the nearest row is too old.

## 6. Sequential Capture Flow
Implemented in `SequentialCaptureService`:
1. Fetch and store Horizons ephemeris window for the target.
2. Compute predicted RA/Dec for the current time.
3. Slew to predicted coordinates.
4. Capture science exposure via NINA.
5. Poll for FITS and stabilize file.
6. Solve locally via `solve-field`.
7. Associate detected source with predicted position.

Notes:
- Confirmation capture/solve is currently bypassed (kept as stubs).
- All steps are synchronous and logged.

## 7. Local Plate Solving
- Uses `solve-field` locally in the API container.
- Solve parameters include RA/Dec hint, radius steps, and scale bounds.
- Progressive radius for science solves:
  - 0.2 → 0.3 → 0.4 degrees
  - timeouts: 45s → 60s → 90s
- Scale bounds are locked to ±10% of computed pixel scale when available.
- Solves are rejected when the solved center is more than **300 arcsec** from the hint.
- WCS headers are written back into the FITS.

## 8. Association & PSV Output
- `AnalysisService.auto_associate`:
  - loads WCS
  - finds predicted position (interpolated ephemeris)
  - detects sources
  - matches nearest within tolerance
  - writes `CandidateAssociation`
- Measurements are stored only when a capture is **solved and associated**.
- Magnitudes are now **photometrically measured** from the image using catalog stars in the `.corr` file.
- If no photometric magnitude can be computed, the measurement is stored with a null magnitude.
- PSV bundles are generated **manually** via the PSV Builder page (auto-generation is disabled).
- PSV bundles are written to `/data/psv`.

## 9. Annotated Outputs
- For successful associations, an annotated PNG is saved alongside the FITS:
  - `<FITS_STEM>_annotated.png`
  - Both predicted and associated positions are marked with circles + legend.

## 10. UI Summary
- Dashboard polls:
  - session status
  - recent captures
  - logs
  - target list
  - readiness
- Start Session is disabled until readiness passes.
- PSV Builder page (`/dashboard/psv`) provides:
  - per-object stats (object number, vmag, science/solved/associated counts)
  - multi-night readiness indicator (green checkmark)
  - manual PSV bundle generation and download links

## 11. Operational Defaults
- Top targets shown: 5
- Horizons window: 15 minutes
- Horizons step: 1 minute
- Max target altitude: 80 degrees
- Science exposures: multiple per target (count configurable in presets/plan)
- PSV auto-generation: disabled (manual only)

## 12. Common Failure Modes
- **No targets**: refresh not run or Horizons fetch failed.
- **Association skipped**: ephemeris too stale (fixed by windowed fetch + interpolation).
- **Solve timeout**: too-wide hint radius or scale bounds; use progressive steps.
- **Missing dependencies**: local solver requires `astropy`, `photutils`, `matplotlib`.

## 13. Key Files
- `app/services/sequential_capture.py`: capture flow + solve + association.
- `app/services/analysis.py`: auto-association + annotations.
- `app/services/solver.py`: local solve-field invocation.
- `app/services/whatsup.py`: target refresh + ephemeris cache.
- `app/core/config.py`: runtime settings.
- `app/api/psv.py`: PSV readiness + bundle endpoints.
- `app/templates/psv.html`: PSV Builder UI.

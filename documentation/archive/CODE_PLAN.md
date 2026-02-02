# Observatory Code App Plan

## Goal
Slim ASTRO-NEO to an imaging workflow focused on obtaining an MPC observatory code by imaging and associating known asteroids, using MPC WhatsUp for target selection and Scout for current position.

## Assumptions / Decisions
- **Target ordering**: "highest mag rating" interpreted as highest numeric V magnitude (faintest first). Confirm if you want brightest first instead.
- **Ephemeris source**: use JPL Horizons for WhatsUp targets; Scout is not used for WhatsUp (it only resolves NEOCP tracklets).
- **Confirmation loop**: 5s confirmation exposure, local plate solve, re-slew up to 2 additional times if outside tolerance.
- **Science exposures**: 3–5 per target, sequential, using NINA API with existing fire-and-forget capture settings.

## Scope Trim (Keep / Remove)
- **Keep**:
  - NINA control (`app/services/nina_client.py`)
  - Sequential capture flow (`app/services/sequential_capture.py`)
  - Local plate solving (`app/services/solver.py`, `app/services/astrometry.py`)
  - File polling (`app/services/file_poller.py`)
- Horizons client (`app/services/horizons_client.py`)
  - Minimal dashboard + logging (`app/templates/dashboard.html`)
  - Session API endpoints (`/api/session/*`)
- **Remove / Disable** (for this slim version):
  - NEOCP candidate ingestion (`neocp-fetcher`)
  - Observability engine (visibility scoring, horizon masks, weather gating)
  - Scout ephemeris usage for WhatsUp targets (use Horizons current position only)
  - Target scoring weights/presets unrelated to WhatsUp targets

## Target Acquisition Flow (WhatsUp → Horizons → Imaging)
1. **Fetch WhatsUp targets** using `scripts/whatsup_probe.py` logic (requests + CSRF).
2. **Parse & store** targets in DB (new model or reuse `NeoCandidate` with a new source tag).
3. **Rank targets** by magnitude (desc unless you specify otherwise).
4. **Select top 10** and fetch current Horizons positions; discard any with missing Horizons data.
5. **Re-rank to top 5** based on available Horizons data.
6. **Enable Start Session** only after top 5 exist with Horizons positions cached in DB.

## Imaging Sequence (per target)
1. Fetch **current Horizons position** for target designation.
2. **Slew**, wait for `Slewing=false`, then settle (fixed delay).
3. **Confirmation exposure**: 5s, watch file path under `/data/fits/YYYY-MM-DD/<target>/SNAPSHOT`.
4. **Local plate solve** confirmation.
5. If offset > tolerance → **re-slew** and retry (max 3 attempts total).
6. **Science exposures**: take 3–5 exposures, each followed by local solve and file verification.
7. Move to next target.

## Tolerance Logic
- Compute FOV from telescope/camera geometry (see prior formula).
- Use **center vs in-frame** thresholds:
  - `center = max(center_fraction * FOV_radius, center_floor_arcsec)`
  - `acquire = acquire_fraction * FOV_radius`
- Optional motion adjustment using Horizons rate + slew/settle time.

## Logging (UI + Server)
- Log major events only:
  - WhatsUp fetch success/failure
  - Initial ranking + top 10
  - Horizons current-position fetch for top 10
  - Final top 5 selection
  - Start Session enabled/disabled
  - Per-target: slew start/complete, confirmation solve result, recenter decisions
  - Per-exposure: exposure start/complete, FITS found, solve success/failure
  - Session stop/abort reasons

## Minimal Data Model Changes
- Add a light **WhatsUpTarget** model or reuse `NeoCandidate` with `source="WHATSUP"`.
- Store **Horizons current positions** in `NeoEphemeris` with `source="HORIZONS"`.
- Track session progress per target + per exposure (reuse `CaptureLog`).

## Implementation Steps
1. Add WhatsUp fetcher service (sync call using existing script logic) + DB persistence.
2. Replace startup refresh with WhatsUp fetch + initial ranking + Horizons top-10 + top-5 selection.
3. Update `/api/session/ready` to rely on WhatsUp top-5 + Horizons cache.
4. Adjust `SequentialCaptureService` to use WhatsUp target designation and Horizons current position only.
5. Ensure FITS polling uses local date directory.
6. Trim unused services/config to reduce noise (disable NEOCP/Horizons/MPC).

## Validation Checklist
- WhatsUp fetch returns objects and stores them.
- Top 10 Horizons fetch succeeds for most targets.
- Start Session only enables when top 5 ready.
- Test mode stops after first slew (still active).
- Confirmation loop re-slews when offset > tolerance.
- FITS detection uses local date path.

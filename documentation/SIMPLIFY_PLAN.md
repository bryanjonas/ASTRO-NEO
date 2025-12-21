# ASTRO-NEO Simplification Plan
**Branch:** `minimum_func`
**Created:** 2025-12-20
**Goal:** Transform the complex parallel processing architecture into a simple, sequential, synchronous pipeline that can be debugged and verified at each step.

---

## Problem Analysis

### Current Architecture Issues
The system currently has **7 parallel services** with complex inter-service communication:

1. **api** - FastAPI server with automation triggers
2. **neocp-fetcher** - Polls MPC NEOCP feed
3. **observability-engine** - Computes visibility/scoring
4. **image-monitor** - Watches filesystem for FITS files (runs every 2s)
5. **astrometry-worker** - HTTP service for plate solving
6. **nina-bridge** - Bridges automation ↔ NINA hardware
7. **db** - PostgreSQL database

### Communication Complexity Map

```
┌─────────────────────────────────────────────────────────────────┐
│                         Current Flow                             │
└─────────────────────────────────────────────────────────────────┘

Automation → nina-bridge → NINA hardware → FITS file written to disk
                                                    ↓
                                           (Asynchronous!)
                                                    ↓
                                         image-monitor (polls every 2s)
                                                    ↓
                                         Correlates file to capture
                                                    ↓
                                         Checks for WCS headers
                                                    ↓
                                         If no WCS → Queue for solve
                                                    ↓
                                         HTTP call to astrometry-worker
                                                    ↓
                                         Retry queue (3 attempts, 30s delays)
                                                    ↓
                                         Updates DB with solver_status
                                                    ↓
                                         Calls _trigger_processing()
                                                    ↓
                                         Loads WCS, calls analysis.auto_associate()
                                                    ↓
                                         Star subtraction + source detection
                                                    ↓
                                         Ephemeris matching
                                                    ↓
                                         Creates CandidateAssociation record
```

### Key Problems

1. **Timing Issues**: File appears on disk → monitor polls → detects → queues → solves → processes
   - Multiple 2-30 second delays at each async handoff
   - Race conditions between file creation and monitoring

2. **Correlation Failures**: Image monitor tries to match FITS files to captures by:
   - Target name (can mismatch due to -CONFIRM suffixes, FAKE- prefixes)
   - Timestamp (±30s tolerance, but files can be created late)
   - Exposure time (±0.5s tolerance)
   - This heuristic matching is fragile!

3. **Orphaned Files**: Files without matching captures create "orphan" records
   - Added as defensive programming but indicates correlation is failing

4. **Retry/Backfill Complexity**:
   - Pending solve queue with retry logic
   - Backfill pass to retroactively link old files
   - Both indicate the primary flow isn't working reliably

5. **State Synchronization**:
   - `SESSION_STATE` (in-memory) vs DB state
   - `has_wcs` flag must be kept in sync with actual file WCS headers
   - `solver_status` field updated by multiple code paths

6. **HTTP Worker Overhead**:
   - astrometry-worker runs as separate service
   - HTTP roundtrip just to run local `solve-field` command
   - Volume mounting complexity to share FITS files

---

## Minimum Viable Functionality

For the simplified system, we need to support **ONE CRITICAL PATH**:

1. **NEOCP fetching + ranking**: System retrieves targets and rank-orders them by observability
2. **Manual session start**: User starts observing session, highest-ranked visible target is selected
3. **Sequential capture loop**: For each exposure:
   - Predict target position using Horizons API
   - Slew telescope to predicted position
   - **Confirmation loop** (up to 3 attempts):
     - Capture short confirmation image
     - Plate solve locally (not NINA)
     - Check if centered (within tolerance)
     - If not centered, re-slew and retry
     - Error if 3 attempts fail
   - Capture main science exposure
   - Wait for FITS file (monitor volume)
   - Plate solve locally
   - Detect sources and associate with predicted position
4. Results viewable in simplified single-pane UI

### What We Keep (Core Requirements)

- ✅ NEOCP fetching service (retrieve and rank targets)
- ✅ Observability scoring (filter by horizon data at session start)
- ✅ Confirmation exposures with re-centering (critical for accuracy)
- ✅ Horizons API integration (fresh ephemeris for each exposure)
- ✅ Local plate solving only (never rely on NINA)
- ✅ Volume monitoring for FITS files (NINA saves to mounted volume)
- ✅ Sequential processing (acceptable latency)
- ✅ Config file for site/equipment (NOT in git)

### What We Can Drop

- ❌ Weather checking (not app's concern)
- ❌ NINA equipment status checking (not app's concern)
- ❌ User-configurable imaging plans (app decides presets)
- ❌ Complex UI tabs (single pane only)
- ❌ Retry queue and backfill logic (solve immediately or fail)
- ❌ Separate astrometry HTTP worker (call solve-field directly)
- ❌ Star subtraction (nice-to-have, adds complexity)
- ❌ SESSION_STATE in-memory cache (use DB as single source of truth)
- ❌ Two-stage acquisition (replaced by confirmation loop)

---

## Proposed Simplified Architecture

### Reduced Service Architecture

Replace the 7 services with **4 services**:
1. **api** - FastAPI server (UI + synchronous capture orchestration)
2. **db** - PostgreSQL database
3. **neocp-fetcher** - Background worker (retrieve and rank targets)
4. **observability-engine** - Background worker (compute visibility scores)

```
┌─────────────────────────────────────────────────────────────────┐
│                      Simplified Flow                             │
└─────────────────────────────────────────────────────────────────┘

Background: neocp-fetcher polls MPC → stores candidates
Background: observability-engine scores → filters by horizon
                           ↓
User clicks "Start Session" → API selects highest-ranked visible target
                           ↓
                    FOR EACH EXPOSURE (N times):
                           ↓
                    Query Horizons API for current RA/Dec
                           ↓
                    Slew telescope to predicted position
                           ↓
                    CONFIRMATION LOOP (max 3 attempts):
                           ↓
                    Capture short confirmation image (5s, bin2)
                           ↓
                    Poll for FITS file (mounted volume, timeout 30s)
                           ↓
                    Run solve-field SYNCHRONOUSLY (subprocess)
                           ↓
                    Calculate offset from predicted position
                           ↓
                    If offset > 120" → Re-slew and retry
                           ↓
                    If 3 failed attempts → ERROR and abort target
                           ↓
                    Capture main science exposure (preset-determined)
                           ↓
                    Poll for FITS file (mounted volume, timeout 60s)
                           ↓
                    Run solve-field SYNCHRONOUSLY
                           ↓
                    Run source detection SYNCHRONOUSLY
                           ↓
                    Match sources to predicted ephemeris
                           ↓
                    Create CandidateAssociation if match found
                           ↓
                    NEXT EXPOSURE (fresh Horizons query)
```

### Configuration File (NOT in Git)

**Location:** `config/site_local.yml` (gitignored)

```yaml
# ASTRO-NEO Site Configuration
# This file contains sensitive location data - DO NOT COMMIT TO GIT

site:
  name: "home-observatory"
  latitude: 51.4769  # REPLACE with your observatory latitude
  longitude: -0.0005  # REPLACE with your observatory longitude
  altitude_m: 47  # REPLACE with your altitude in meters
  timezone: "America/New_York"
  bortle: 6

equipment:
  profile_name: "RedCat 51"
  telescope:
    design: "Reflector"
    aperture_m: 0.051
    detector: "CCD"
  camera:
    type: "mono"
    max_binning: 1

imaging:
  confirmation:
    exposure_seconds: 5
    binning: 2
    max_attempts: 3
    centering_tolerance_arcsec: 120
  science:
    # Preset logic determines exposure/binning based on target magnitude
    use_presets: true
```

**Update .gitignore:**
```
config/site_local.yml
```

### Synchronous Capture Function (With Confirmation Loop)

```python
def capture_with_confirmation_and_processing(
    target_name: str,
    candidate_id: str,
    exposure_seconds: float,
    filter_name: str = "L",
    binning: int = 1
) -> dict:
    """
    Capture a single image with confirmation loop and process synchronously.

    Returns:
        {
            "success": bool,
            "capture_id": int,
            "fits_path": str,
            "solved": bool,
            "association_id": int | None,
            "error": str | None,
            "confirmation_attempts": int
        }
    """

    # 1. Get fresh ephemeris from Horizons API
    ephemeris = query_horizons_now(candidate_id)
    predicted_ra = ephemeris.ra_deg
    predicted_dec = ephemeris.dec_deg

    # 2. Confirmation loop (max 3 attempts)
    for attempt in range(1, 4):
        # Slew to predicted position
        nina_client.slew_to_coordinates(predicted_ra, predicted_dec)

        # Capture short confirmation image
        nina_client.take_exposure(
            exposure_seconds=5,
            binning=2,
            target_name=f"{target_name}-CONFIRM",
            solve=False  # NEVER rely on NINA solving
        )

        # Wait for confirmation FITS
        confirm_path = poll_for_fits_file(f"{target_name}-CONFIRM", timeout=30)
        if not confirm_path:
            if attempt == 3:
                return {"success": False, "error": "Confirmation image not created"}
            continue

        # Solve confirmation image
        try:
            solve_result = solve_field_local(
                confirm_path,
                ra_hint=predicted_ra,
                dec_hint=predicted_dec
            )
            solved_ra = solve_result["solution"]["ra"]
            solved_dec = solve_result["solution"]["dec"]
        except Exception as e:
            if attempt == 3:
                return {"success": False, "error": f"Confirmation solve failed: {e}"}
            continue

        # Calculate offset
        offset_arcsec = calculate_separation_arcsec(
            predicted_ra, predicted_dec,
            solved_ra, solved_dec
        )

        # Check if centered
        if offset_arcsec <= 120:
            logger.info(f"Centered after {attempt} attempt(s), offset={offset_arcsec:.1f}\"")
            break

        # Re-slew to solved position for next attempt
        predicted_ra = solved_ra
        predicted_dec = solved_dec

        if attempt == 3:
            return {
                "success": False,
                "error": f"Failed to center after 3 attempts (final offset={offset_arcsec:.1f}\")"
            }

    # 3. Create capture record for main exposure
    capture = CaptureLog(
        target=target_name,
        started_at=datetime.utcnow(),
        predicted_ra_deg=predicted_ra,
        predicted_dec_deg=predicted_dec
    )
    db.add(capture)
    db.commit()

    # 4. Take main science exposure
    nina_client.take_exposure(
        exposure_seconds=exposure_seconds,
        filter=filter_name,
        binning=binning,
        target_name=target_name,
        solve=False  # NEVER rely on NINA solving
    )

    # 5. Wait for FITS file (poll mounted volume)
    fits_path = poll_for_fits_file(target_name, timeout=60)
    if not fits_path:
        return {"success": False, "error": "Science image not created"}

    # 6. Update capture with path
    capture.path = str(fits_path)
    db.commit()

    # 7. Plate solve (synchronous subprocess)
    try:
        solve_result = solve_field_local(
            fits_path,
            ra_hint=predicted_ra,
            dec_hint=predicted_dec
        )
        capture.has_wcs = True
        db.commit()
    except Exception as e:
        capture.has_wcs = False
        db.commit()
        return {"success": True, "solved": False, "error": str(e)}

    # 8. Source detection & association (synchronous)
    wcs = WCS(fits_path.with_suffix(".wcs"))
    analysis = AnalysisService(db)
    sources = analysis.detect_sources(fits_path, wcs)

    # Match to ephemeris
    best_match = analysis.find_best_match(
        sources,
        predicted_ra,
        predicted_dec,
        tolerance_arcsec=10.0
    )

    if best_match:
        assoc = CandidateAssociation(
            capture_id=capture.id,
            ra_deg=best_match["ra_deg"],
            dec_deg=best_match["dec_deg"],
            predicted_ra_deg=predicted_ra,
            predicted_dec_deg=predicted_dec,
            residual_arcsec=calculate_separation_arcsec(
                predicted_ra, predicted_dec,
                best_match["ra_deg"], best_match["dec_deg"]
            ),
            snr=best_match.get("snr"),
            method="auto"
        )
        db.add(assoc)
        db.commit()
        return {
            "success": True,
            "solved": True,
            "association_id": assoc.id,
            "confirmation_attempts": attempt
        }

    return {
        "success": True,
        "solved": True,
        "association_id": None,
        "confirmation_attempts": attempt
    }
```

---

## Implementation Plan

### Phase 1: Service Consolidation & Config Setup

**Goal:** Reduce from 7 services to 4, remove unnecessary complexity, protect sensitive data

1. **Create site configuration file (CRITICAL - DO FIRST)**
   - Create `config/site_local.yml` with location/equipment data from database
   - Add `config/site_local.yml` to `.gitignore`
   - Verify sensitive data is NOT in any committed files
   - Load config in API startup instead of database queries

2. **Merge nina-bridge into api service**
   - Copy `nina_bridge/` code into `app/services/nina_client.py`
   - Remove separate nina-bridge container from docker-compose.yml
   - NINA calls now happen directly from API server (inline function calls)
   - ✅ DONE: Removed `nina_bridge/` folder and `/api/bridge/*` endpoints

3. **Remove image-monitor service**
   - Delete `app/services/image_monitor_service.py` runner
   - Keep `ImageMonitor` class for polling FITS files synchronously
   - Remove `image-monitor` container from docker-compose.yml
   - ✅ DONE: service container removed; polling is synchronous

4. **Remove astrometry-worker service**
   - Modify `app/services/solver.py` to ONLY use local solving
   - Remove HTTP client code (`_solve_remote`), keep `_solve_local` only
   - Remove `astrometry-worker` container from docker-compose.yml
   - Install astrometry.net in API container instead
   - ✅ DONE: solver uses local subprocess only; worker removed
   - ✅ DONE: `documentation/LLM_SYSTEM_DESCRIPTION.md` updated to reflect local-only solver

5. **Keep essential background workers**
   - **KEEP** `neocp-fetcher` (retrieve and store targets)
   - **KEEP** `observability-engine` (score targets by horizon visibility)

6. **Simplified docker-compose.yml:**
   ```yaml
   services:
     api:
       # Main application + nina-bridge + local astrometry
       # Contains all capture orchestration logic
     db:
       # PostgreSQL
     neocp-fetcher:
       # Background worker - fetch targets from MPC
     observability-engine:
       # Background worker - score targets by visibility
   ```
   - ✅ DONE: docker-compose.yml now contains only api/db/neocp-fetcher/observability-engine

### Phase 2: Synchronous Capture Flow with Confirmation Loop

**Goal:** Implement the full sequential capture pipeline

1. **Create new endpoint:** `POST /api/session/start`
   ```json
   {
     "manual_target_override": null  // If null, auto-select highest-ranked visible target
   }
   ```
   - Queries `NeoObservability` table for highest-scored target
   - Filters by horizon data (altitude > 0 at current time)
   - Creates `ObservingSession` record
   - Begins sequential capture loop

2. **Implement `SequentialCaptureService`:**
   - Orchestrates: Horizons → slew → confirm → science → solve → detect → associate
   - **All synchronous**, no background tasks
   - Clear error handling at each step
   - Returns detailed result per exposure

3. **Confirmation loop implementation:**
   - Query Horizons API for fresh RA/Dec
   - Slew telescope
   - Capture 5s bin2 confirmation image
   - Poll volume for FITS file (timeout 30s)
   - Solve synchronously (subprocess)
   - Calculate offset from predicted position
   - If > 120": re-slew to solved position, retry (max 3 attempts)
   - If 3 failures: abort target, log error

4. **File polling logic:**
   - Poll `/data/fits` (mounted volume) for file matching target name
   - Use NINA filename pattern: `{TARGET}_{DATETIME}__{EXPOSURE}s_{FRAME}.fits`
   - Exponential backoff: 100ms, 200ms, 400ms, 800ms, 1.6s, 3.2s...
   - Clear timeout error if file doesn't appear
   - ✅ DONE: `app/services/file_poller.py` implements strict name matching + exponential backoff

5. **Remove complex correlation:**
   - No SESSION_STATE in-memory cache
   - No timestamp/exposure matching heuristics
   - No backfill or orphan handling
   - Direct: "I just captured this, poll for the file with this exact name"

### Phase 3: Strip Automation Complexity & Remove Weather/Equipment Checks

**Goal:** Remove layers that add indirection and unnecessary checks

1. **Simplify AutomationService**
   - Remove weather checking (not app's concern)
   - Remove NINA equipment status checking (not app's concern)
   - Remove user-configurable imaging parameters
   - Keep only: target selection → preset determination → capture loop

2. **Remove old capture orchestration**
   - Delete `app/services/capture_loop.py` (replace with new sequential service)
   - Delete `app/services/acquisition.py` (replace with inline confirmation loop)
   - Consolidate into single `SequentialCaptureService`
   - ✅ DONE: legacy capture modules removed

3. **Remove SESSION_STATE in-memory cache**
   - Delete `app/services/session.py` in-memory cache
   - Use database `ObservingSession` as single source of truth
   - All status updates write directly to DB
   - Read from DB for UI status queries
   - ✅ DONE: session state is DB-backed only

4. **Preset-only imaging parameters**
   - Remove UI controls for exposure/filter/binning
   - App determines all imaging parameters via `presets.py`
   - Based on target magnitude and motion rate
   - No user overrides allowed

### Phase 4: UI Simplification

**Goal:** Single-pane interface for minimum functionality

1. **Remove unnecessary tabs:**
   - Keep ONLY: Main observing pane
   - Remove: Solver tab, Planning tab, Equipment tab, Weather tab, etc.
   - ✅ DONE: legacy HTMX dashboard routes/templates removed; minimal dashboard in use

2. **Single pane layout:**
   ```
   ┌─────────────────────────────────────────────────┐
   │ ASTRO-NEO Minimum Function                      │
   ├─────────────────────────────────────────────────┤
   │                                                  │
   │ Current Target: ZTF109i  (Rank #1, Alt 45°)    │
   │ Session Status: Capturing (3/10 complete)       │
   │                                                  │
   │ [Start Session]  [Stop Session]                 │
   │                                                  │
   │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
   │                                                  │
   │ Recent Captures:                                 │
   │ ✓ #3: Solved, Associated (residual 2.3")       │
   │ ✓ #2: Solved, Associated (residual 1.8")       │
   │ ✓ #1: Solved, Associated (residual 3.1")       │
   │                                                  │
   │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
   │                                                  │
   │ Available Targets (Top 5):                       │
   │ 1. ZTF109i      (Score 85, Alt 45°, Visible)   │
   │ 2. A11wdXf      (Score 72, Alt 30°, Visible)   │
   │ 3. P11ABCD      (Score 68, Set in 2h)          │
   │ 4. K23XYZ1      (Score 55, Not visible)         │
   │ 5. M24TEST      (Score 42, Not visible)         │
   │                                                  │
   └─────────────────────────────────────────────────┘
   ```

3. **Remove configuration UI:**
   - No manual exposure/filter/binning entry
   - No weather override controls
   - No equipment profile switching
   - All configured via `config/site_local.yml`

### Phase 5: Testing & Validation

**Goal:** Verify the simplified system works end-to-end

1. **Session Start Test:**
   - Start docker-compose (4 services: api, db, neocp-fetcher, observability-engine)
   - Verify NEOCP targets are being fetched
   - Verify observability scores are being computed
   - Click "Start Session" in UI
   - Verify highest-ranked visible target is auto-selected
   - Verify session begins

2. **Confirmation Loop Test:**
   - Verify Horizons query happens
   - Verify telescope slews
   - Verify confirmation image captured
   - Verify file appears in mounted volume
   - Verify local solve completes
   - Verify offset calculated
   - Verify re-slew if needed
   - Verify error after 3 failed attempts

3. **Science Capture Test:**
   - Verify main exposure captured
   - Verify file appears in volume
   - Verify local solve completes
   - Verify source detection runs
   - Verify association created
   - Verify results displayed in UI

4. **Error Path Testing:**
   - Test Horizons API failure
   - Test NINA connection failure
   - Test file not appearing (timeout)
   - Test solve failure
   - Test no sources detected
   - Test no ephemeris match
   - Verify errors are clearly reported in UI

5. **Multi-Image Sequence:**
   - Run full 10-image session
   - Verify fresh Horizons query per exposure
   - Verify confirmation loop runs each time
   - Verify all captures complete
   - Verify database records are correct
   - Verify no file correlation issues

---

## Migration Strategy

### Step 0: CRITICAL - Protect Sensitive Data (DO FIRST!)

**⚠️ MUST complete before any commits on this branch ⚠️**

1. **Verify .gitignore includes:**
   ```
   config/site_local.yml  ✅ ADDED
   config/site.yml
   ```

2. **Extract location data from database and create config:**
   ```bash
   # Query database for current site config
   docker compose exec db psql -U astro -d astro -c "SELECT * FROM siteconfig WHERE is_active=true;" -x

   # Create config/site_local.yml with this data ✅ DONE
   ```

3. **⚠️ FOUND: Sensitive data in committed files:**
   ```
   README.md - Contains latitude/longitude in example
   scripts/debug_horizon.py - Contains hardcoded coordinates
   ```

   **ACTION REQUIRED:**
   - Update README.md to use placeholder coordinates (e.g., 40.7128, -74.0060 for NYC)
   - Update scripts/debug_horizon.py to read from config file or use env vars
   - These changes MUST be made before pushing to GitHub
   - ✅ DONE: `scripts/debug_horizon.py` loads site config

4. **Verify nothing sensitive will be pushed:**
   ```bash
   git status
   git diff
   # Ensure config/site_local.yml is NOT staged
   # Ensure README.md and debug_horizon.py are updated with placeholders
   ```

### Step 1: Branch & Preserve
✅ **Already done:** Created `minimum_func` branch

### Step 2: Docker Compose Simplification
```yaml
# Simplified docker-compose.yml (4 services, down from 7)
services:
  api:
    build: .
    depends_on: [db]
    environment:
      DATABASE_URL: postgresql+psycopg://astro:astro@db:5432/astro
      NINA_URL: ${NINA_URL}
      NINA_IMAGES_PATH: /data/fits
      SITE_CONFIG_FILE: /app/config/site_local.yml
    ports:
      - "18080:8000"
    volumes:
      - ./app:/app/app
      - ./config:/app/config:ro  # Mount config dir
      - ./data:/data
      - nina_images:/data/fits

  db:
    image: postgres:15
    environment:
      POSTGRES_DB: astro
      POSTGRES_USER: astro
      POSTGRES_PASSWORD: astro
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  neocp-fetcher:
    build: .
    depends_on: [db]
    environment:
      DATABASE_URL: postgresql+psycopg://astro:astro@db:5432/astro
      SERVICE_NAME: neocp-fetcher
    command: ["python", "-m", "app.services.neocp_fetcher"]
    volumes:
      - ./config:/app/config:ro

  observability-engine:
    build: .
    depends_on: [db]
    environment:
      DATABASE_URL: postgresql+psycopg://astro:astro@db:5432/astro
      SERVICE_NAME: observability-engine
      SITE_CONFIG_FILE: /app/config/site_local.yml
    command: ["python", "-m", "app.services.observability_engine"]
    volumes:
      - ./config:/app/config:ro

volumes:
  postgres_data:
  nina_images:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ${NINA_IMAGES_HOST_PATH:-./data/fits}
```
✅ **DONE:** `docker-compose.yml` now contains only the 4 core services.

### Step 3: Code Consolidation

**Files to DELETE:**
- `app/services/image_monitor_service.py` (background runner - replaced by inline polling)
- `app/services/capture_loop.py` (old async complexity)
- `app/services/acquisition.py` (old two-stage - replaced by confirmation loop)
- `app/services/session.py` (SESSION_STATE in-memory cache)
- `app/worker/astrometry_server.py` (HTTP wrapper - replaced by local subprocess)
- `app/api/bridge.py` and `nina_bridge/` (bridge endpoints/service removed)
- Docker service runners: Remove `nina-bridge`, `image-monitor`, `astrometry-worker` from docker-compose.yml
✅ **DONE:** Legacy services removed and worker deleted.

**Files to KEEP (Background workers still needed):**
- `app/services/neocp_fetcher.py` (fetch targets from MPC)
- `app/services/observability_engine.py` (score targets by horizon)

**Files to SIMPLIFY:**
- `app/services/automation.py`:
  - Remove weather checking (`_ensure_weather_safe`)
  - Remove equipment status checking
  - Remove user override parameters
  - Keep only target selection + preset determination

- `app/services/solver.py`:
  - Remove `_solve_remote` function (HTTP client)
  - Keep only `_solve_local` (subprocess)
  - Remove `ASTROMETRY_WORKER_URL` setting check

- `app/services/image_monitor.py`:
  - Keep class for synchronous file polling
  - Remove all retry queue logic
  - Remove backfill logic
  - Remove orphan handling
  - Simplify to: `poll_for_file(target_name, timeout) -> Path | None`

**Files to CREATE:**
- `config/site_local.yml` - Site/equipment configuration (NOT in git)
- `app/services/config_loader.py` - Load YAML config on startup
- `app/services/sequential_capture.py` - New synchronous capture orchestrator with confirmation loop
- `app/api/session_simple.py` - New simplified session start endpoint

### Step 4: UI Simplification

Create a simple capture form:
```
Target Name: [________]
RA (deg):    [________]
Dec (deg):   [________]
Exposure:    [60] seconds
Filter:      [L ▼]
Binning:     [1 ▼]

[Capture & Process]

Status: Slewing... ✓
        Exposing... ✓
        Waiting for file... ✓
        Plate solving... ✓
        Detecting sources... ✓
        Associating... ✓ (Found at RA=123.4567, Dec=45.6789, residual=2.3")
```

---

## Benefits of Simplified Architecture

### Debugging
- **Sequential execution** - Can add logging and follow the exact flow
- **No async handoffs** - No "it should have triggered X but didn't"
- **Clear error points** - Each step returns success/failure immediately
- **Fresh coordinates** - Horizons query per exposure eliminates ephemeris drift

### Reliability
- **Confirmation loop** - Ensures telescope is centered before main exposure
- **No NINA plate solving** - All solving happens locally (controllable, debuggable)
- **Volume monitoring** - Simple file polling, no complex correlation
- **Single source of truth** - Database only, no in-memory cache to sync
- **No orphaned files** - Sequential flow means we know exactly what file we created

### Observability
- **Fewer log streams** - 4 services instead of 7
- **Clear causality** - Horizons → slew → confirm → science → solve → detect
- **Easy to add logging** - Just add logs in the function, see them immediately
- **No distributed tracing needed** - Capture logic happens in one synchronous flow

### Maintainability
- **Fewer services** - 4 containers instead of 7 (43% reduction)
- **Simpler UI** - Single pane instead of 6+ tabs
- **Config file** - Site/equipment in YAML, not database
- **Easier onboarding** - New developers can understand the flow quickly
- **Testable** - Can unit test the whole flow with mocks

### Security
- **Protected location data** - Site coordinates in gitignored config file
- **No accidental exposure** - Location never pushed to GitHub
- **Clean separation** - Public code, private config

---

## Risks & Mitigations

### Risk 1: Sequential Latency
**Problem:** Fresh Horizons query + confirmation loop adds time between exposures

**Mitigation:**
- Accept this - accuracy is more important than speed
- Confirmation loop ensures we're centered, preventing wasted exposures
- Latency is debuggable and predictable

### Risk 2: NINA Blocking
**Problem:** If NINA takes 60s to expose + readout, API request blocks for 60s

**Mitigation:**
- Accept this for now - it's debuggable and traceable
- Single-threaded execution makes debugging easier
- Can add async later if needed (but probably won't need to)

### Risk 3: File System Latency
**Problem:** FITS file might not appear immediately after NINA returns

**Mitigation:**
- Poll with exponential backoff (100ms, 200ms, 400ms, ...)
- Clear timeout message if file doesn't appear
- Mounted volume makes files visible immediately to container
- 60s timeout is generous

### Risk 4: Configuration Drift
**Problem:** Config file could get out of sync with reality

**Mitigation:**
- Single config file `config/site_local.yml` is easy to maintain
- Document clearly in README how to update it
- Add validation on startup (fail fast if config is invalid)
- Keep database tables for now (not used, but preserved)

---

## Success Criteria

The simplified system is successful when:

1. ✅ **Security**: Observatory location is in gitignored config file, NOT in repo
2. ✅ **Service reduction**: Docker compose starts 4 services (down from 7)
3. ✅ **Target selection**: NEOCP targets fetched and ranked by observability
4. ✅ **Auto-select**: Highest-ranked visible target selected at session start
5. ✅ **Fresh ephemeris**: Horizons API queried before each exposure
6. ✅ **Confirmation loop**: Telescope centered within 120" after max 3 attempts
7. ✅ **Local solving**: All plate solving via local subprocess (not NINA)
8. ✅ **Volume monitoring**: FITS files detected from mounted volume
9. ✅ **Sequential processing**: Each image fully processed before next exposure
10. ✅ **Source detection**: Sources detected and matched to predicted position
11. ✅ **Association**: CandidateAssociation created if match found
12. ✅ **Error handling**: Clear error messages at each step
13. ✅ **Simplified UI**: Single pane shows session status and recent captures
14. ✅ **No weather/equipment**: App doesn't check weather or NINA status
15. ✅ **Preset-only**: App determines all imaging parameters (no user overrides)

---

## Implementation Phases Summary

### Phase 0: Security (CRITICAL - DO FIRST!)
- Create `config/site_local.yml` with location data
- Verify .gitignore is correct
- Search for sensitive data in committed files
- **DO NOT COMMIT** until this is complete

### Phase 1: Service Consolidation (Immediate reduction)
- Remove 3 services: `nina-bridge`, `image-monitor`, `astrometry-worker`
- Merge nina-bridge into API service
- Install astrometry.net in API container
- Keep: `neocp-fetcher`, `observability-engine`
- **Result:** 7 services → 4 services

### Phase 2: Synchronous Capture Flow (Core logic)
- Implement `SequentialCaptureService`
- Confirmation loop with re-centering
- Fresh Horizons query per exposure
- Synchronous file polling
- Synchronous solving, detection, association
- **Result:** Debuggable, traceable capture pipeline

### Phase 3: Strip Complexity (Cleanup)
- Remove SESSION_STATE cache
- Remove weather checking
- Remove equipment checking
- Remove user overrides for imaging params
- Delete old async capture logic
- **Result:** Simpler, more maintainable code

### Phase 4: UI Simplification (User experience)
- Single pane interface
- Remove tabs: Solver, Planning, Equipment, Weather
- Show: Current target, session status, recent captures, available targets
- **Result:** Focused, easy-to-use interface

### Phase 5: Testing & Validation (Verification)
- Test session start and auto-select
- Test confirmation loop
- Test full capture sequence
- Test error paths
- Test 10-image session
- **Result:** Confidence that system works reliably

---

## Key Design Decisions (Per Your Requirements)

1. **NEOCP Fetching**: ✅ KEEP - Background worker retrieves and stores targets
2. **Observability Scoring**: ✅ KEEP - Background worker ranks by horizon visibility
3. **Target Selection**: ✅ AUTO - Highest-ranked visible target at session start
4. **Weather Checking**: ❌ REMOVE - Not app's concern
5. **Equipment Checking**: ❌ REMOVE - Not app's concern
6. **Imaging Parameters**: ✅ PRESET - App determines based on target, no user overrides
7. **Confirmation Loop**: ✅ KEEP - Critical for centering (max 3 attempts, 120" tolerance)
8. **Horizons Query**: ✅ PER-EXPOSURE - Fresh ephemeris for each capture
9. **Plate Solving**: ✅ LOCAL - Never rely on NINA
10. **Volume Monitoring**: ✅ KEEP - Poll mounted volume for FITS files
11. **Sequential Processing**: ✅ ACCEPT - Debuggability > speed
12. **Configuration**: ✅ FILE - YAML config (gitignored), not database
13. **UI Complexity**: ❌ SIMPLIFY - Single pane only

---

## Next Steps

1. ✅ **Review this updated plan** - Confirm it matches your vision
2. **Start Phase 0** - Protect sensitive location data (CRITICAL!)
3. **Start Phase 1** - Reduce services from 7 to 4
4. **Test incrementally** - Verify each phase works before moving on
5. **Document issues** - Track what breaks and why

**Ready to proceed?** The plan now incorporates all your requirements:
- Target retrieval and ranking
- Auto-selection of highest-ranked visible target
- Confirmation loop with re-centering
- Fresh Horizons query per exposure
- No weather/equipment checking
- Preset-only imaging parameters
- Config file for location (gitignored)
- Single-pane UI

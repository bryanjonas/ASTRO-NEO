# ASTRO-NEO: Quick Read for LLMs

## Project Overview
ASTRO-NEO is a distributed control plane designed to automate the end-to-end workflow of tracking and reporting Near-Earth Object (NEO) candidates from backyard observatories. It orchestrates data ingestion, target selection, telescope control, image processing, and reporting to the Minor Planet Center (MPC).

## Core Purpose
The system automates the following pipeline:
1.  **Ingest**: Fetches NEOCP (Near-Earth Object Confirmation Page) candidates from the MPC.
2.  **Filter**: Determines target observability based on site location, horizon masks, weather, and equipment limits.
3.  **Prioritize**: Ranks targets using multi-factor composite scoring (altitude, time-to-set, motion rate, uncertainty, arc extension).
4.  **Acquire**: Uses two-stage slew-confirm-verify workflow with fresh JPL Horizons ephemerides for accurate pointing.
5.  **Image**: Commands NINA (Nighttime Imaging 'N' Astronomy) to acquire astrometric data with motion-compensated exposures for fast movers.
6.  **Solve**: Plate-solves captured images to extract precise RA/Dec coordinates.
7.  **Report**: Generates and submits ADES 2022 compliant reports to the MPC.

## Architecture & Stack
-   **Runtime**: Docker Compose (All services run in containers).
-   **Language**: Python 3.11 (Backend), Javascript (Frontend).
-   **Frameworks**:
    -   **Backend**: FastAPI (REST API), Uvicorn.
    -   **Frontend**: Jinja2 Templates (Server-side rendering), HTMX (Interactivity), Alpine.js (Client-side state).
    -   **Database**: PostgreSQL (Metadata, Targets, Observations).
    -   **ORM**: SQLAlchemy + Alembic (Migrations).
-   **External Integrations**:
    -   **MPC**: Data source (NEOCP) and reporting destination (Observations API).
    -   **NINA**: Hardware control via REST API (Bridge pattern).
    -   **Open-Meteo**: Weather data for observability gating.
    -   **Astrometry.net**: Local plate solving (`solve-field`).

## Key Services
| Service | Description |
| :--- | :--- |
| **`app` (API)** | Central FastAPI application serving the Dashboard and REST endpoints. |
| **`neocp-fetcher`** | Background worker that polls MPC for new candidates and updates the DB. |
| **`observability-engine`** | Background worker that computes visibility windows using `astroplan`. Synthetic “FAKE-*” targets are left as-is so they remain observable immediately. |
| **`nina-bridge`** | Proxy service that standardizes communication with the local NINA instance. |
| **`astrometry-worker`** | Dedicated worker for CPU-intensive plate solving tasks. |
| **`mock_nina`** | Simulation service emulating NINA's API for offline development/testing. |
| **`synthetic-targets`** | Seeder that refreshes synthetic NEOCP entries (30°–45° altitude) for daylight/offline testing; runs continuously so test targets are always available. |

## Data Flow
1.  **Ingestion**: `neocp-fetcher` scrapes MPC -> Stores candidates in `neocandidate` table.
2.  **Observability**: `observability-engine` checks weather/horizon -> Updates `neoobservability` table with composite scores.
3.  **Prioritization**: Dynamic scoring ranks targets using 6 weighted factors (MPC priority 30%, altitude 25%, time-to-set 15%, motion 10%, uncertainty 10%, arc extension 10%).
4.  **Session**: User/Auto starts session -> `night_ops` selects highest-scored targets -> Processes targets ONE AT A TIME sequentially.
5.  **Acquisition (Per Target)**: Two-stage workflow ensures accurate pointing:
    a. Fetch fresh JPL Horizons ephemeris (topocentric corrections, motion rates, uncertainty)
    b. Slew to predicted coordinates
    c. Take 8s bin2 confirmation exposure
    d. Plate solve and calculate offset from prediction (haversine formula)
    e. Refine pointing if offset > 120" threshold
6.  **Imaging (Per Target)**: For each exposure, the automation service runs a 5-step verification workflow:
    a. Recalculate target RA/Dec via `EphemerisPredictionService` (Horizons or MPC) for current time
    b. Slew to predicted coordinates and wait for mount/camera ready
    c. Take short confirmation exposure (max 8s, bin2) and plate solve
    d. If offset > 120" from predicted position, re-slew to solved position
    e. Take science exposure (full duration/binning) with plate solving enabled
    f. Log exposure in `SESSION_STATE` (target, index, predicted coordinates, confirmation offset, platesolve result)
7.  **Monitoring**: `image_monitor` watches `/data/fits` directory for new FITS files from NINA. `sequence_processor` checks if NINA plate-solved each image (WCS headers in FITS).
8.  **Solving**: If NINA solved the image, solution is recorded directly. Otherwise, local `astrometry-worker` runs `solve-field` with RA/Dec hints and updates `astrometricsolution`.
9.  **Repeat**: Steps 5-8 repeat for each target sequentially until all targets complete.
10. **Reporting**: Operator reviews data -> `reporting` generates ADES XML -> `submission` sends to MPC.

## Automation Sequence Plan

- The automation path routes through `AutomationService.run_sequential_target_sequence()`, driving each target through the capture loop. Each exposure recalculates RA/Dec, slews, and calls `NinaBridgeService.start_exposure()` before the next capture begins.
- `build_sequential_target_plan()` refreshes every candidate's RA/Dec via `EphemerisPredictionService` before the run, and the capture loop logs the "Exposure X/Y solved/failed" events that power the overview tab.
- The detailed control loop follows: predict → slew → expose → solve → repeat. This keeps automation sequential while eliminating stale solutions for fast-moving NEOs.
- Legacy sequences built via `nina_bridge/sequence_builder.py` remain available for the GUI/fallback workflows; the automation scheduler keeps itself inside the capture loop so auto-mode stays consistent without changing the control flow.

## Development Rules
-   **Container-First**: No local Python environments. Run everything via `docker compose`.
-   **Troubleshooting**: Always run troubleshooting commands (e.g., database queries, python scripts) inside the running containers using `docker compose exec <service> <command>`.
-   **Secrets**: Stored in `.env` (gitignored). Site config in `config/site.yml` (gitignored).
-   **State**: All persistent state resides in Postgres or mounted `/data` volumes.
-   **Frontend**: Keep it simple with HTMX/Alpine. No complex build steps (React/Vue) unless necessary.

## NINA Integration Notes
-   **App-managed capture loop**: Automation keeps each target inside the capture loop service, recalculating RA/Dec, slewing, and taking one exposure at a time. The control flow is: predict coordinates → slew mount → wait for readiness → expose → plate solve → repeat for next exposure.
-   **Sequence Format**: Legacy NINA sequences (GUI/fallback) expect a single **SequenceRootContainer** object (NOT an array) containing properly nested instruction containers.
-   **Sequential Target Processing**: When those legacy sequences run, targets are processed ONE AT A TIME: slew → center → expose (N times) → solve before moving to the next target.
-   **One Exposure Per Container**: `build_target_sequence()` creates one DeepSkyObjectContainer per exposure (not per target). If a target needs 4 exposures, we create 4 containers. This enables motion tracking by re-centering before each exposure.
-   **Sequence Progress Watch**: `bridge_status()` now surfaces `nina_status.sequence.progress` (total exposures, current index, and per-item status flags) so the overview tab and automation logic can see which exposures are done before polling the file system.
-   **Coordinates**: NINA uses Hours/Minutes/Seconds format for RA and Degrees/Minutes/Seconds for Dec. The `sequence_builder.py` automatically converts from decimal degrees.
-   **Implementation**: `nina_bridge/sequence_builder.py` generates properly formatted NINA Advanced Sequencer payloads that include:
    -   Start/End containers for setup/teardown
    -   Multiple DeepSkyObjectContainer entries (one per exposure, for ONE target at a time)
    -   Each container has: Plate-solve/center instruction (inherits coordinates from parent), optional filter switching, and exactly ONE exposure
    -   TakeExposure instruction with proper binning format and ExposureCount=0 (NINA's 0-based count for 1 exposure)
-   **Image Monitoring**: System watches `/data/fits` for new images using NINA's filename template: `$$DATEMINUS12$$\$$TARGETNAME$$\$$IMAGETYPE$$\$$TARGETNAME$$_$$DATETIME$$_$$FILTER$$_$$EXPOSURETIME$$s_$$FRAMENR$$`
-   **Plate Solving**: Checks FITS headers for WCS keywords (NINA's solve). If not present, runs local astrometry.net with RA/Dec hints from target coordinates.

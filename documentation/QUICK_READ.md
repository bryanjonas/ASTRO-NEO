# ASTRO-NEO: Quick Read for LLMs

## Project Overview
ASTRO-NEO is a distributed control plane designed to automate the end-to-end workflow of tracking and reporting Near-Earth Object (NEO) candidates from backyard observatories. It orchestrates data ingestion, target selection, telescope control, image processing, and reporting to the Minor Planet Center (MPC).

## Core Purpose
The system automates the following pipeline:
1.  **Ingest**: Fetches NEOCP (Near-Earth Object Confirmation Page) candidates from the MPC.
2.  **Filter**: Determines target observability based on site location, horizon masks, weather, and equipment limits.
3.  **Schedule**: Prioritizes targets based on urgency, visibility, and magnitude.
4.  **Image**: Commands NINA (Nighttime Imaging 'N' Astronomy) to acquire astrometric data.
5.  **Solve**: Plate-solves captured images to extract precise RA/Dec coordinates.
6.  **Report**: Generates and submits ADES 2022 compliant reports to the MPC.

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
2.  **Observability**: `observability-engine` checks weather/horizon -> Updates `neoobservability` table.
3.  **Session**: User/Auto starts session -> `night_ops` selects targets -> Processes targets ONE AT A TIME sequentially.
4.  **Imaging (Per Target)**: For each target:
    a. `nina-bridge` generates a NINA Advanced Sequencer payload (SequenceRootContainer) for SINGLE target with N exposures.
    b. Each exposure gets ONE DeepSkyObjectContainer to enable motion tracking (re-center before each exposure).
    c. NINA executes: slew → center → expose → save → repeat for each exposure.
    d. System waits for all images from current target before moving to next target.
5.  **Monitoring**: `image_monitor` watches `/data/fits` directory for new FITS files from NINA. `sequence_processor` checks if NINA plate-solved each image (WCS headers in FITS).
6.  **Solving**: If NINA solved the image, solution is recorded directly. Otherwise, local `astrometry-worker` runs `solve-field` with RA/Dec hints and updates `astrometricsolution`.
7.  **Repeat**: Steps 4-6 repeat for each target sequentially until all targets complete.
8.  **Reporting**: Operator reviews data -> `reporting` generates ADES XML -> `submission` sends to MPC.

## Automation Sequence Plan

- The automation path always routes through `AutomationService.run_sequential_target_sequence()`; it loops over candidates one at a time, so each call emits a single-target NINA sequence before picking the next target.
- Before building a plan we now refresh each target’s RA/Dec via MPC ephemerides for the current UTC time, so the slews sent to NINA reflect the most recent prediction even before the solver centers.
- Targets are processed sequentially—each complete NINA Advanced Sequencer payload (built via `build_sequential_target_plan`) slews, centers, and takes the configured number of exposures, then waits for all image files to arrive and be solved before advancing to the next entry.
- This approach guarantees that the session scheduler can continue to call the same sequential workflow repeatedly, ensuring the “auto” mode always works off of the sequential NINA Advanced Sequencer design and can easily scale to multiple targets without changing the basic control flow.

## Development Rules
-   **Container-First**: No local Python environments. Run everything via `docker compose`.
-   **Troubleshooting**: Always run troubleshooting commands (e.g., database queries, python scripts) inside the running containers using `docker compose exec <service> <command>`.
-   **Secrets**: Stored in `.env` (gitignored). Site config in `config/site.yml` (gitignored).
-   **State**: All persistent state resides in Postgres or mounted `/data` volumes.
-   **Frontend**: Keep it simple with HTMX/Alpine. No complex build steps (React/Vue) unless necessary.

## NINA Integration Notes
-   **Sequence Format**: NINA's `/v2/api/sequence/load` endpoint expects a single **SequenceRootContainer** object (NOT an array). See `documentation/NINA_SEQ_INSTRUCTIONS.md` for the complete, tested format.
-   **Sequential Target Processing**: Targets are processed ONE AT A TIME. Each target gets its own complete sequence: slew → center → expose (N times) → solve. Only after all images from Target A are received and solved does the system move to Target B.
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

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
| **`observability-engine`** | Background worker that computes visibility windows using `astroplan`. |
| **`nina-bridge`** | Proxy service that standardizes communication with the local NINA instance. |
| **`astrometry-worker`** | Dedicated worker for CPU-intensive plate solving tasks. |
| **`mock_nina`** | Simulation service emulating NINA's API for offline development/testing. |

## Data Flow
1.  **Ingestion**: `neocp-fetcher` scrapes MPC -> Stores candidates in `neocandidate` table.
2.  **Observability**: `observability-engine` checks weather/horizon -> Updates `neoobservability` table.
3.  **Session**: User/Auto starts session -> `night_ops` selects top target -> Sends commands to `nina-bridge`.
4.  **Imaging**: `nina-bridge` commands NINA -> NINA captures FITS -> Saves to shared volume.
5.  **Processing**: `astrometry-worker` picks up FITS -> Solves -> Updates `astrometricsolution`.
6.  **Reporting**: Operator reviews data -> `reporting` generates ADES XML -> `submission` sends to MPC.

## Development Rules
-   **Container-First**: No local Python environments. Run everything via `docker compose`.
-   **Troubleshooting**: Always run troubleshooting commands (e.g., database queries, python scripts) inside the running containers using `docker compose exec <service> <command>`.
-   **Secrets**: Stored in `.env` (gitignored). Site config in `config/site.yml` (gitignored).
-   **State**: All persistent state resides in Postgres or mounted `/data` volumes.
-   **Frontend**: Keep it simple with HTMX/Alpine. No complex build steps (React/Vue) unless necessary.

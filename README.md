# ASTRO-NEO

<p align="center">
  <img src="Logo.png" alt="ASTRO-NEO logo" width="240">
</p>

ASTRO stands for **Astrometric System for Tracking & Reporting Objects**, describing the distributed control plane this repo delivers for backyard NEO follow-up.

End-to-end orchestration stack for backyard NEOCP follow-up observations that automates candidate ingestion, target prioritization, telescope control, image acquisition with confirmation exposures, plate solving, and MPC-compliant astrometry reporting.

## Key Components

- `app/` – FastAPI application with REST APIs, database models, services, and a minimal Alpine dashboard
- `mock_nina/` – Standalone FastAPI service that emulates NINA endpoints for testing (optional)
- `scripts/` – Container-only utilities and tests
- `alembic/` – Database migrations for the Postgres metadata store
- `documentation/` – Project documentation:
    - [LLM System Description](documentation/LLM_SYSTEM_DESCRIPTION.md) – Comprehensive architectural overview (canonical reference)
    - [Target Scoring & Scheduling](documentation/TARGET_SCORING.md) – Details on how targets are ranked and exposure presets selected
    - `archive/` – Historical design notes and implementation guides

## Architecture Overview

The system runs four Docker services that work together:

- **api** – Main FastAPI application with the minimal dashboard and synchronous capture orchestration
- **neocp-fetcher** – Polls MPC NEOCP feed and persists candidates to database
- **observability-engine** – Computes visibility windows and scores targets
- **db** – PostgreSQL database for all metadata

NINA is accessed directly from the API container via its REST API; there is no separate NINA bridge.

### Data Flow

1. **Ingestion** → NEOCP candidates fetched and stored
2. **Scoring** → Observability engine scores candidates for visibility
3. **Session start** → User starts a session; highest-ranked visible target is selected
4. **Per-exposure loop** → Horizons ephemeris → slew → confirmation exposure → local plate solve → re-center if needed → science exposure → local plate solve
5. **Association** → Sources detected and matched to the predicted ephemeris
6. **Reporting** → Solved frames drive MPC-compliant astrometry reports


## Getting Started

### Prerequisites

- Docker + Docker Compose v2

### Build and run (containers only)

All services run exclusively in containers. Always rebuild the images before starting:

```bash
docker compose up --build --pull always
```

### Service Endpoints

- **Dashboard**: http://localhost:18080/dashboard (minimal session UI)
- **API**: http://localhost:18080/api/health (health check), `/api/site` (site config), `/api/observability` (target scores)
- **Postgres**: localhost:5432 (user `astro`, password `astro`, db `astro`)
- **Prometheus Metrics**: http://localhost:19500/metrics (neocp-fetcher metrics: cycle latency, MPC requests, rate limits)

### Background Services

View logs for any service using `docker compose logs -f <service-name>`:

- **neocp-fetcher** – Polls MPC NEOCP feed and persists candidates
- **observability-engine** – Recomputes visibility scores every 15 minutes

### Key Features

- **Sequential capture**: Single-threaded, synchronous capture flow for clear debugging
- **Confirmation loop**: Short confirmation shots verify pointing before each science frame; offsets >120" trigger re-slew
- **Local plate solving**: `solve-field` runs locally in the API container
- **Ephemeris refresh**: Horizons is queried for each exposure to reduce ephemeris drift

The containers automatically apply Alembic migrations on startup (with retries until Postgres is reachable), so `docker compose up` is usually enough to bootstrap a fresh database.

If you ever need to run migrations manually, you can still do so:

```bash
docker compose run --rm api alembic upgrade head
```

### Management commands

- Sync the live MPC NEOCP list into Postgres:

  ```bash
  docker compose run --rm neocp-fetcher python -m app.services.neocp_fetcher --oneshot
  ```

  Pass `--local` to parse the local snapshot defined by `NEOCP_LOCAL_HTML` (defaults to `/data/neocp_snapshots/toconfirm.html`) instead of hitting the network.

- Run a one-shot execution of the `neocp-fetcher` worker (useful for debugging formats or forcing an immediate poll):

  ```bash
  docker compose run --rm neocp-fetcher python -m app.services.neocp_fetcher --oneshot
  ```

  Append `--local` to use the offline HTML snapshot or `--formats ADES_DF OBS80` to override the MPC output formats requested.

- Recompute observability windows via API (from another terminal):

  ```bash
  curl -X POST http://localhost:8000/api/observability/refresh
  ```

- Run a one-shot execution of the observability engine (same logic as the background worker, useful for validating config/env changes):

  ```bash
  docker compose run --rm observability-engine python -m app.services.observability_engine --oneshot
  ```


### Site configuration

- Store your observatory configuration in `config/site_local.yml` (gitignored). Point the app at it via `SITE_CONFIG_PATH` if needed.
- The FastAPI app loads this file on startup and seeds/updates the `siteconfig` table automatically. Example snippet:

  ```yaml
  site:
    name: home-observatory
    latitude: 51.4769  # Example: Greenwich Observatory
    longitude: -0.0005
    altitude_m: 47
    equipment_profile:
      camera:
        type: mono        # or osc
        filters: ["L", "R", "G", "B"]
        max_binning: 2
      presets:
        - name: bright
          max_vmag: 15.0
          exposure_seconds: 20.0
          count: 8
          filter: L
          binning: 1
        - name: medium
          max_vmag: 18.5
          exposure_seconds: 45.0
          count: 10
          filter: L
          binning: 1
        - name: faint
          max_vmag: 99.0
          exposure_seconds: 90.0
          count: 12
          filter: L
          binning: 2
      focuser:
        position_min: 10000
        position_max: 80000
      mount:
        supports_parking: true
  ```
- Update `config/site_local.yml` and restart the API to ensure the default site record reflects any edits.
- The compose stack mounts `./config` into the API container read-only, keeping sensitive files local while still letting the service read them.
- The `equipment_profile` block is optional but recommended; it lets the preset selector tailor sequences to the active camera/mount.

### Sample data & utilities

- Horizon mask JSON examples live under `config/horizon/` (gitignored; drop in your own profile).
- Offline NEOCP snapshots belong under `./data/neocp_snapshots/` (gitignored). Drop MPC `neocp.txt` exports there (mounted at `/data/neocp_snapshots/neocp.txt` via `NEOCP_LOCAL_TEXT`) and, if desired, the older HTML snapshot (`toconfirm.html`) for fallback parsing.
- Configure ingestion defaults via `.env` (override `NEOCP_TEXT_URL`, `NEOCP_HTML_URL`, `NEOCP_LOCAL_TEXT`, `NEOCP_LOCAL_HTML`, or set `NEOCP_USE_LOCAL_SAMPLE=true` to always stay offline).
- Tune ephemeris/observability behavior via `.env`:
  - `MPC_EPHEMERIS_URL`, `MPC_EPHEMERIS_TIMEOUT` (defaults provided in `app/core/config.py`)
  - `OBSERVABILITY_*` knobs for sampling cadence, altitude limits, sun/moon constraints, and maximum candidate age.
  - `OBSERVABILITY_REFRESH_MINUTES` to control how often the background worker recomputes visibility windows (default 15).
- Tune observability thresholds via `.env` as needed (`OBSERVABILITY_MIN_ALTITUDE_DEG`, `OBSERVABILITY_MAX_SUN_ALTITUDE_DEG`, etc.); see `app/core/config.py` for the full list of knobs.
- Configure the direct NINA connection via `.env`:
  - `NINA_URL` to point at the live NINA REST API (defaults to the bundled mock at `http://mock-nina:1888/api`).
  - `NINA_TIMEOUT` to handle long exposures and solves.

## Repository Layout

| Path | Description |
| --- | --- |
| `app/` | FastAPI app, models, API routers, services, and dashboard templates |
| `app/services/` | Background workers and automation services |
| `mock_nina/` | Mock NINA FastAPI app + Dockerfile (optional for testing) |
| `alembic/` | Database migrations |
| `scripts/` | Container-only utilities and tests |
| `documentation/` | LLM system description, scoring logic, implementation guides, and archived design notes |
| `docker-compose.yml` | Orchestrates the API, workers, and Postgres |
| `config/` | Site configuration (YAML), horizon masks, equipment profiles |
| `data/` | FITS files, NEOCP snapshots, and processing artifacts |

## Key Services Detail

### Automation & Capture
- **AutomationService** ([app/services/automation.py](app/services/automation.py)) – Builds target plans and runs sequential captures
- **SequentialCaptureService** ([app/services/sequential_capture.py](app/services/sequential_capture.py)) – Confirmation loop, local solve, and association

### Processing & Solving
- **Solver** ([app/services/solver.py](app/services/solver.py)) – Local `solve-field` subprocess wrapper
- **AnalysisService** ([app/services/analysis.py](app/services/analysis.py)) – Source detection and association

### Scoring & Observability
- **ObservabilityEngine** ([app/services/observability.py](app/services/observability.py)) – Target visibility scoring
- **TargetScoring** ([app/services/target_scoring.py](app/services/target_scoring.py)) – Individual scoring components (altitude, motion, uncertainty, etc.)
- **ExposurePresets** ([app/services/presets.py](app/services/presets.py)) – Bright/medium/faint templates with motion-aware exposure reduction

## Monitoring & Troubleshooting

### Dashboard Views
Access the dashboard at http://localhost:18080/dashboard for real-time monitoring:
- **Session** – Active target, status, and counts
- **Captures** – Recent exposures and association status
- **Targets** – Top-ranked observable targets

### Common Issues

**FITS files not detected:**
- Verify NINA is saving into the mounted `/data/fits` path
- Check the API logs for file polling timeouts: `docker compose logs -f api`

**Solve failures:**
- Verify `solve-field` and index files are installed in the API container
- Check the API logs for solver stderr output: `docker compose logs -f api`

**Sessions not starting:**
- Check observability scores: `curl http://localhost:18080/api/observability | jq`
- Review automation logs: `docker compose logs -f api | grep automation`

### Logs & Debugging

```bash
# View all service logs
docker compose logs -f

# Filter specific service
docker compose logs -f api
docker compose logs -f neocp-fetcher
docker compose logs -f observability-engine

# Check Prometheus metrics
curl http://localhost:19500/metrics | grep neocp
```

## Contributing

- Run management commands through Docker only (no local Python environments)
- Keep mock data and secrets out of git (`.gitignore` already excludes sensitive folders)
- See [LLM_SYSTEM_DESCRIPTION.md](documentation/LLM_SYSTEM_DESCRIPTION.md) for architectural details
- Historical design notes archived in `documentation/archive/`

## License

TBD – specify before public release.

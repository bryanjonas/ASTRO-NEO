# ASTRO-NEO

<p align="center">
  <img src="Logo.png" alt="ASTRO-NEO logo" width="240">
</p>

ASTRO stands for **Astrometric System for Tracking & Reporting Objects**, describing the distributed control plane this repo delivers for backyard NEO follow-up.

End-to-end orchestration stack for backyard NEOCP follow-up observations that automates candidate ingestion, target prioritization, telescope control, image acquisition with confirmation exposures, plate solving, and MPC-compliant astrometry reporting.

## Key Components

- `app/` – FastAPI application with REST APIs, database models, services, and HTMX/Alpine dashboard
- `nina_bridge/` – NINA Bridge service that fronts the telescope control API with weather/safety gates
- `mock_nina/` – Standalone FastAPI service that emulates NINA endpoints for testing (optional)
- `scripts/` – Container-only utilities and tests
- `alembic/` – Database migrations for the Postgres metadata store
- `documentation/` – Project documentation:
    - [LLM System Description](documentation/LLM_SYSTEM_DESCRIPTION.md) – Comprehensive architectural overview (canonical reference)
    - [Target Scoring & Scheduling](documentation/TARGET_SCORING.md) – Details on how targets are ranked and exposure presets selected
    - `archive/` – Historical design notes and implementation guides

## Architecture Overview

The system runs multiple Docker services that work together:

- **api** – Main FastAPI application with dashboard UI and REST endpoints
- **neocp-fetcher** – Polls MPC NEOCP feed and persists candidates to database
- **observability-engine** – Computes visibility windows and scores targets using six-component model
- **image-monitor** – Watches `/data/fits` for new FITS files, handles plate-solve backlog with retries, and links files to SESSION_STATE captures
- **astrometry-worker** – Local plate-solving service using solve-field
- **nina-bridge** – REST facade for NINA with weather gates and manual override controls
- **db** – PostgreSQL database for all metadata

### Data Flow

1. **Ingestion** → NEOCP candidates fetched and stored
2. **Scoring** → Observability engine enriches with visibility, weather, altitude, motion, uncertainty scores
3. **Automation** → Sequential target workflow builds capture plans and executes via CaptureLoop
4. **Two-stage acquisition** → Fresh Horizons ephemeris + confirmation exposure + refinement slew before first science frame
5. **Per-exposure confirmation** → Each science exposure preceded by short confirmation shot to verify pointing
6. **SESSION_STATE logging** → Every capture logged with predicted coordinates, platesolve status, and file path placeholders
7. **Filesystem monitoring** → Image monitor detects FITS files, matches to captures, runs solver backlog with retries
8. **Plate solving** → Local astrometry-worker generates WCS when NINA doesn't solve
9. **ADES generation** → Solved frames drive MPC-compliant astrometry reports


## Getting Started

### Prerequisites

- Docker + Docker Compose v2

### Build and run (containers only)

All services run exclusively in containers. Always rebuild the images before starting:

```bash
docker compose up --build --pull always
```

### Service Endpoints

- **Dashboard**: http://localhost:18080 (HTMX/Alpine UI for monitoring automation, targets, exposures, and solver status)
- **API**: http://localhost:18080/api/health (health check), `/api/site` (site config), `/api/observability` (target scores)
- **NINA Bridge**: http://localhost:1889/api/status (aggregate status with weather + NINA telemetry)
- **Astrometry Worker**: http://localhost:18100 (local plate-solving service)
- **Postgres**: localhost:5432 (user `astro`, password `astro`, db `astro`)
- **Prometheus Metrics**: http://localhost:19500/metrics (neocp-fetcher metrics: cycle latency, MPC requests, rate limits)

### Background Services

View logs for any service using `docker compose logs -f <service-name>`:

- **neocp-fetcher** – Polls MPC NEOCP feed and persists candidates
- **observability-engine** – Recomputes visibility scores every 15 minutes using weather, ephemerides, and site config
- **image-monitor** – Watches `/data/fits` for new FITS files, correlates with SESSION_STATE captures, runs plate-solve backlog with retries
- **nina-bridge** – Enforces weather/manual overrides and proxies telescope/camera commands to NINA

### Key Features

- **SESSION_STATE logging**: Every exposure is logged before capture with predicted RA/Dec, target info, and platesolve placeholders
- **Confirmation exposures**: Short (≤8s, bin2) confirmation shots verify pointing before each science frame; offsets >120″ trigger re-slew
- **Plate-solve backlog**: Image monitor maintains a pending-solve queue with retries (3 attempts, 30s spacing) for any unsolved FITS files
- **Ephemeris cache**: `/api/observability/refresh` fetches per-minute MPC ephemerides and stores in Postgres (`neoephemeris` table) for reuse
- **Weather gating**: NINA bridge blocks captures when wind/humidity/precipitation/clouds exceed safety thresholds

The containers automatically apply Alembic migrations on startup (with retries until Postgres is reachable), so `docker compose up` is usually enough to bootstrap a fresh database.

If you ever need to run migrations manually, you can still do so:

```bash
docker compose run --rm api python scripts/nina_bridge_smoke.py
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

- Exercise the NINA bridge (pause/resume automation, check status):

  ```bash
  # Pause automation (manual override)
  curl -X POST http://localhost:1889/api/override -H "Content-Type: application/json" -d '{"manual_override": true}'

  # Check aggregate status (weather + NINA telemetry + equipment profile)
  curl http://localhost:1889/api/status | jq

  # Resume automation
  curl -X POST http://localhost:1889/api/override -H "Content-Type: application/json" -d '{"manual_override": false}'

  # Capture a single exposure via bridge (science frame with no solve)
  curl -X POST http://localhost:1889/api/capture \
    -H "Content-Type: application/json" \
    -d '{"duration": 30.0, "targetName": "TEST-TARGET", "solve": false}'
  ```

### Site configuration

- Populate `.env` with `SITE_LATITUDE`, `SITE_LONGITUDE`, and `SITE_ALTITUDE_M` for the observatory (already included by default).
- Extend `config/site.yml` with horizon masks, Bortle scale, and (optionally) remote weather API definitions; the FastAPI app loads this file on startup and seeds/updates the `siteconfig` table automatically. Example snippet for Open-Meteo:

  ```yaml
  site:
    name: home-observatory
    latitude: 51.4769  # Example: Greenwich Observatory
    longitude: -0.0005
    altitude_m: 47
    weather_sensors:
      - name: Open-Meteo
        type: open-meteo
        endpoint: "https://api.open-meteo.com/v1/forecast?latitude=51.4769&longitude=-0.0005&current=temperature_2m,wind_speed_10m&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m"
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
- Update `.env` or `config/site.yml` and restart the API to ensure the default site record reflects any edits.
- The compose stack mounts `./config` into the API container read-only, keeping sensitive files local while still letting the service read them.
- The `equipment_profile` block is optional but recommended; it lets the NINA bridge validate filters/binning, constrain focuser moves, and tailor sequence templates to the active camera/mount.

### Sample data & utilities

- Horizon mask JSON examples live under `config/horizon/` (gitignored; drop in your own profile).
- Offline NEOCP snapshots belong under `./data/neocp_snapshots/` (gitignored). Drop MPC `neocp.txt` exports there (mounted at `/data/neocp_snapshots/neocp.txt` via `NEOCP_LOCAL_TEXT`) and, if desired, the older HTML snapshot (`toconfirm.html`) for fallback parsing.
- Configure ingestion defaults via `.env` (override `NEOCP_TEXT_URL`, `NEOCP_HTML_URL`, `NEOCP_LOCAL_TEXT`, `NEOCP_LOCAL_HTML`, or set `NEOCP_USE_LOCAL_SAMPLE=true` to always stay offline).
- Tune ephemeris/observability behavior via `.env`:
  - `MPC_EPHEMERIS_URL`, `MPC_EPHEMERIS_TIMEOUT` (defaults provided in `app/core/config.py`)
  - `OBSERVABILITY_*` knobs for sampling cadence, altitude limits, sun/moon constraints, and maximum candidate age.
  - `OBSERVABILITY_REFRESH_MINUTES` to control how often the background worker recomputes visibility windows (default 15).
- Tune observability thresholds via `.env` as needed (`OBSERVABILITY_MIN_ALTITUDE_DEG`, `OBSERVABILITY_MAX_SUN_ALTITUDE_DEG`, etc.); see `app/core/config.py` for the full list of knobs.
- Configure the bridge service via `.env`:
  - `NINA_BRIDGE_NINA_BASE_URL` to point at the live NINA REST API (defaults to the bundled mock at `http://mock-nina:1888/api`).
  - `NINA_BRIDGE_HTTP_TIMEOUT`, `NINA_BRIDGE_MAX_RETRIES`, `NINA_BRIDGE_REQUIRE_WEATHER_SAFE` for networking/safety behavior.
- The FastAPI backend exposes `/api/bridge/*` endpoints that proxy to the bridge for scheduler/dashboard usage. Examples:

  ```bash
  # Check aggregate status (bridge, weather, equipment profile)
  curl http://localhost:18080/api/bridge/status | jq

  # Request a sequence template for a mag 18 target
  curl -X POST http://localhost:18080/api/bridge/sequence/plan \
    -H "Content-Type: application/json" \
    -d '{"vmag": 18.0}' | jq

  # Start a sequence using the returned plan
  curl -X POST http://localhost:18080/api/bridge/sequence/start \
    -H "Content-Type: application/json" \
    -d '{"name":"medium","count":10,"filter":"L","binning":1,"exposure_seconds":45}'
  ```
- Configure weather gating via `.env`:
  - `WEATHER_SNAPSHOT_TTL_MINUTES` (default 15) controls how long cached Open-Meteo payloads remain valid.
  - `WEATHER_API_TIMEOUT` (default 10s) guards outbound HTTP calls.
  - `WEATHER_MAX_WIND_SPEED_MPS`, `WEATHER_MAX_RELATIVE_HUMIDITY_PCT`, `WEATHER_MAX_PRECIP_PROBABILITY_PCT`, `WEATHER_PRECIP_BLOCK_THRESHOLD_MM`, and `WEATHER_MAX_CLOUD_COVER_PCT` define the safety thresholds that mark observability windows as blocked.

## Repository Layout

| Path | Description |
| --- | --- |
| `app/` | FastAPI app, models, API routers, services, and dashboard templates |
| `app/services/` | Background workers and automation services |
| `nina_bridge/` | Standalone NINA Bridge service with weather gates and override controls |
| `mock_nina/` | Mock NINA FastAPI app + Dockerfile (optional for testing) |
| `alembic/` | Database migrations |
| `scripts/` | Container-only utilities and tests |
| `documentation/` | LLM system description, scoring logic, implementation guides, and archived design notes |
| `docker-compose.yml` | Orchestrates all services: API, workers, bridge, astrometry, and Postgres |
| `config/` | Site configuration (YAML), horizon masks, equipment profiles |
| `data/` | FITS files, NEOCP snapshots, and processing artifacts |

## Key Services Detail

### Automation & Capture
- **AutomationService** ([app/services/automation.py](app/services/automation.py)) – Orchestrates sequential target workflow, builds capture plans
- **CaptureLoop** ([app/services/capture_loop.py](app/services/capture_loop.py)) – Per-target capture flow with confirmation exposures
- **TwoStageAcquisition** ([app/services/acquisition.py](app/services/acquisition.py)) – Fetches Horizons ephemeris, runs confirmation exposure, refines pointing

### Processing & Monitoring
- **ImageMonitorService** ([app/services/image_monitor_service.py](app/services/image_monitor_service.py)) – Watches `/data/fits`, correlates files with SESSION_STATE, manages plate-solve backlog
- **NinaBridgeService** ([app/services/bridge.py](app/services/bridge.py)) – Internal client for NINA bridge API calls

### Scoring & Observability
- **ObservabilityEngine** ([app/services/observability.py](app/services/observability.py)) – Six-component scoring model for target prioritization
- **TargetScoring** ([app/services/target_scoring.py](app/services/target_scoring.py)) – Individual scoring components (altitude, motion, uncertainty, etc.)
- **ExposurePresets** ([app/services/presets.py](app/services/presets.py)) – Bright/medium/faint templates with motion-aware exposure reduction

## NINA Bridge & Plate-Solve Contract

The NINA bridge enforces a strict contract for telescope control:

### Capture Parameters
- **Science exposures**: `solve=false` – NINA saves the FITS but doesn't plate-solve; local astrometry-worker handles WCS generation post-capture
- **Confirmation exposures**: `solve=true` – NINA plate-solves immediately to report offsets for re-slew decisions (≤8s, bin2)
- Bridge always forwards minimal parameters: `duration`, `save=true`, `solve=true/false`, `targetName`
- Bridge response includes plate-solve metadata when available but **never returns file paths** – all file correlation happens via filesystem monitoring

### File Path Strategy
- NINA saves FITS to `/data/fits/YYYY-MM-DD/{TARGET}_{YYYY-MM-DD}_{HH-MM-SS}__{EXPOSURE}s_{FRAME}.fits`
- Image monitor watches this directory recursively and matches files to SESSION_STATE captures by target name, exposure duration, and timestamp tolerance
- Backfill pass on startup links previously-missed captures to existing FITS files
- Pending-solve queue retries failed solves up to 3 times with 30s spacing

### SESSION_STATE Traceability
Every capture is logged before filesystem monitoring fills in the actual file path:
- `kind="exposure"` – Capture type marker
- `target`, `index` – Target name and exposure sequence number
- `predicted_ra_deg`, `predicted_dec_deg` – Horizons-based pointing prediction
- `platesolve` – Initial solve status from NINA (for confirmations) or null (for science frames)
- `path` – Placeholder filled by image monitor once FITS file detected
- `solver_status` – Tracks pending/solved/error state for backlog processing

## Monitoring & Troubleshooting

### Dashboard Views
Access the dashboard at http://localhost:18080 for real-time monitoring:
- **Targets** – Current NEOCP candidates with observability scores and visibility windows
- **Automation** – Sequential target execution status and capture progress
- **Exposures** – SESSION_STATE log with per-exposure status (pending/solved/error)
- **Solver Status** – Plate-solve backlog queue and retry attempts

### Common Issues

**FITS files not matched to SESSION_STATE captures:**
- Check `docker compose logs -f image-monitor` for correlation warnings
- Verify filename pattern matches: `{TARGET}_{YYYY-MM-DD}_{HH-MM-SS}__{EXPOSURE}s_{FRAME}.fits`
- Confirm timestamp tolerance (currently 60s) allows for mount/camera delays
- Restart image-monitor service to trigger backfill pass: `docker compose restart image-monitor`

**Pending images never plate-solved:**
- Check solver status in dashboard Exposures tab (`solver_status=pending/error`)
- Verify astrometry-worker is running: `docker compose logs -f astrometry-worker`
- Check solver retry count (max 3 attempts, 30s spacing)
- Manually trigger solve via API if needed (see astrometry-worker endpoints)

**Confirmation exposures failing:**
- Check NINA bridge logs: `docker compose logs -f nina-bridge`
- Verify confirmation shots request `solve=true` and NINA can plate-solve quickly
- Offsets >120″ trigger re-slew; check SESSION_STATE for `platesolve` offset values
- Confirmation failures log warnings but science exposures still proceed

**Automation not starting captures:**
- Check weather gates: `curl http://localhost:1889/api/status | jq .weather`
- Verify manual override is disabled: `curl http://localhost:1889/api/status | jq .manual_override`
- Check observability scores: `curl http://localhost:18080/api/observability | jq`
- Review automation service logs: `docker compose logs -f api | grep automation`

### Logs & Debugging

```bash
# View all service logs
docker compose logs -f

# Filter specific service
docker compose logs -f image-monitor
docker compose logs -f nina-bridge
docker compose logs -f neocp-fetcher

# Check Prometheus metrics
curl http://localhost:19500/metrics | grep neocp

# Query SESSION_STATE via database
docker compose exec db psql -U astro -d astro -c "SELECT * FROM session_state ORDER BY started_at DESC LIMIT 10;"
```

## Contributing

- Run management commands through Docker only (no local Python environments)
- Keep mock data and secrets out of git (`.gitignore` already excludes sensitive folders)
- See [LLM_SYSTEM_DESCRIPTION.md](documentation/LLM_SYSTEM_DESCRIPTION.md) for architectural details
- Historical design notes archived in `documentation/archive/`

## License

TBD – specify before public release.

# ASTRO-NEO

<p align="center">
  <img src="Logo.png" alt="ASTRO-NEO logo" width="240">
</p>

ASTRO stands for **Astrometric System for Tracking & Reporting Objects**, describing the distributed control plane this repo delivers for backyard NEO follow-up.

End-to-end orchestration stack for backyard NEOCP follow-up observations. This repo contains:

- `app/` – FastAPI application exposing REST APIs (health, site config, future services).
- `mock_nina/` – Standalone FastAPI service that emulates key NINA endpoints for telescope/camera control and writes dummy FITS files.
- `scripts/` – Container-only utilities and tests.
- `alembic/` – Database migrations for the Postgres metadata store.
- `documentation/` – Project documentation, including:
    - [Target Scoring & Scheduling](documentation/TARGET_SCORING.md) – Details on how targets are ranked and exposure presets selected.
    - [Streamlining Report](documentation/STREAMLINE.md) – Maintenance log and design decisions.
    - [Quick Read](documentation/QUICK_READ.md) – High-level architectural summary.
- `BUILD_NOTES.md` – Detailed design notes and backlog.

## Getting Started

### Prerequisites

- Docker + Docker Compose v2

### Build and run (containers only)

All services run exclusively in containers. Always rebuild the images before starting:

```bash
docker compose up --build --pull always
```

- API: http://localhost:8000 (health: `/api/health`, site endpoints under `/api/site`)
- Observability: `/api/observability` (GET for latest scores, POST `/api/observability/refresh` to recompute windows)
- `neocp-fetcher`: background worker that polls MPC, persists snapshots/observations, and publishes logs via `docker compose logs -f neocp-fetcher`
- `observability-engine`: background worker that periodically recomputes visibility scores using the latest weather, ephemerides, and site config (`docker compose logs -f observability-engine`)
- `nina-bridge`: REST facade that fronts the real (or mock) NINA instance, enforces weather/manual overrides, and exposes simplified telescope/camera endpoints to the rest of the stack. Logs available via `docker compose logs -f nina-bridge`.
- `neocp-fetcher` metrics: http://localhost:19500/metrics (Prometheus format; includes cycle latency, MPC request counts, rate-limit hits)
- Ephemeris cache: `/api/observability/refresh` will fetch per-minute MPC ephemerides, store them in Postgres (`neoephemeris` table), and reuse cached values for future scoring runs.
- Mock NINA: http://localhost:1888/api
- Postgres: localhost:5432 (user `astro`, password `astro`, db `astro`)

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

- Exercise the NINA bridge (example: enable manual override, then clear it):

  ```bash
  # Pause automation
  curl -X POST http://localhost:1889/api/override -H "Content-Type: application/json" -d '{"manual_override": true}'
  # Check aggregate status (includes weather + upstream NINA telemetry)
  curl http://localhost:1889/api/status | jq
  # Resume automation
  curl -X POST http://localhost:1889/api/override -H "Content-Type: application/json" -d '{"manual_override": false}'
  ```

### Site configuration

- Populate `.env` with `SITE_LATITUDE`, `SITE_LONGITUDE`, and `SITE_ALTITUDE_M` for the observatory (already included by default).
- Extend `config/site.yml` with horizon masks, Bortle scale, and (optionally) remote weather API definitions; the FastAPI app loads this file on startup and seeds/updates the `siteconfig` table automatically. Example snippet for Open-Meteo:

  ```yaml
  site:
    name: home-observatory
    latitude: 51.4769
    longitude: -0.0005
    altitude_m: 25
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
- The FastAPI backend now exposes `/api/bridge/*` endpoints that proxy to the bridge for scheduler/dashboard usage. Examples:

  ```bash
  # Check aggregate status (bridge, weather, equipment profile)
  curl http://localhost:8000/api/bridge/status | jq

  # Request a sequence template for a mag 18 target
  curl -X POST http://localhost:8000/api/bridge/sequence/plan \
    -H "Content-Type: application/json" \
    -d '{"vmag": 18.0}' | jq

  # Start a sequence using the returned plan
  curl -X POST http://localhost:8000/api/bridge/sequence/start \
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
| `app/` | FastAPI app, models, API routers, services |
| `nina_bridge/` | Standalone NINA Bridge service source code |
| `mock_nina/` | Mock NINA FastAPI app + Dockerfile |
| `alembic/` | Database migrations |
| `scripts/` | Container-only utilities and tests |
| `documentation/` | Architecture, scoring logic, and maintenance notes |
| `docker-compose.yml` | Orchestrates API, Postgres, mock NINA |

## Contributing

- Follow the tasks/backlog in `BUILD_NOTES.md` (see "Proposed Build Order" and Running To-Do).
- Run management commands through Docker only (no local Python environments).
- Keep mock data and secrets out of git (`.gitignore` already excludes sensitive folders).

## License

TBD – specify before public release.

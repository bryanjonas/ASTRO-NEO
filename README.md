# ASTRO-NEO

<p align="center">
  <img src="Logo.png" alt="ASTRO-NEO logo" width="240">
</p>

ASTRO stands for **Astrometric System for Tracking & Reporting Objects**, describing the distributed control plane this repo delivers for backyard NEO follow-up.

End-to-end orchestration stack for backyard NEOCP follow-up observations. This repo contains:

- `app/` – FastAPI application exposing REST APIs (health, site config, future services).
- `mock_nina/` – Standalone FastAPI service that emulates key NINA endpoints for telescope/camera control and writes dummy FITS files.
- `scripts/` – Container-only utilities (NEOCP ingest CLI plus the MPC HTML snapshot).
- `alembic/` – Database migrations for the Postgres metadata store.
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
- `neocp-fetcher` metrics: http://localhost:19500/metrics (Prometheus format; includes cycle latency, MPC request counts, rate-limit hits)
- Ephemeris cache: `/api/observability/refresh` will fetch per-minute MPC ephemerides, store them in Postgres (`neoephemeris` table), and reuse cached values for future scoring runs.
- Mock NINA: http://localhost:1888/api
- Postgres: localhost:5432 (user `astro`, password `astro`, db `astro`)

The containers automatically apply Alembic migrations on startup (with retries until Postgres is reachable), so `docker compose up` is usually enough to bootstrap a fresh database.

If you ever need to run migrations manually, you can still do so:

```bash
docker compose run --rm api alembic upgrade head
```

### Management commands

- Sync the live MPC NEOCP list into Postgres:

  ```bash
  docker compose run --rm api python scripts/neocp_ingest.py
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
  ```
- Update `.env` or `config/site.yml` and restart the API to ensure the default site record reflects any edits.
- The compose stack mounts `./config` into the API container read-only, keeping sensitive files local while still letting the service read them.

### Sample data & utilities

- Horizon mask JSON examples live under `config/horizon/` (gitignored; drop in your own profile).
- Offline NEOCP snapshots belong under `./data/neocp_snapshots/` (gitignored). Drop MPC `neocp.txt` exports there (mounted at `/data/neocp_snapshots/neocp.txt` via `NEOCP_LOCAL_TEXT`) and, if desired, the older HTML snapshot (`toconfirm.html`) for fallback parsing.
- Configure ingestion defaults via `.env` (override `NEOCP_TEXT_URL`, `NEOCP_HTML_URL`, `NEOCP_LOCAL_TEXT`, `NEOCP_LOCAL_HTML`, or set `NEOCP_USE_LOCAL_SAMPLE=true` to always stay offline).
- Tune ephemeris/observability behavior via `.env`:
  - `MPC_EPHEMERIS_URL`, `MPC_EPHEMERIS_TIMEOUT` (defaults provided in `app/core/config.py`)
  - `OBSERVABILITY_*` knobs for sampling cadence, altitude limits, sun/moon constraints, and maximum candidate age.
  - `OBSERVABILITY_REFRESH_MINUTES` to control how often the background worker recomputes visibility windows (default 15).
- Tune observability thresholds via `.env` as needed (`OBSERVABILITY_MIN_ALTITUDE_DEG`, `OBSERVABILITY_MAX_SUN_ALTITUDE_DEG`, etc.); see `app/core/config.py` for the full list of knobs.
- Configure weather gating via `.env`:
  - `WEATHER_SNAPSHOT_TTL_MINUTES` (default 15) controls how long cached Open-Meteo payloads remain valid.
  - `WEATHER_API_TIMEOUT` (default 10s) guards outbound HTTP calls.
  - `WEATHER_MAX_WIND_SPEED_MPS`, `WEATHER_MAX_RELATIVE_HUMIDITY_PCT`, `WEATHER_MAX_PRECIP_PROBABILITY_PCT`, `WEATHER_PRECIP_BLOCK_THRESHOLD_MM`, and `WEATHER_MAX_CLOUD_COVER_PCT` define the safety thresholds that mark observability windows as blocked.

## Repository Layout

| Path | Description |
| --- | --- |
| `app/` | FastAPI app, models, API routers, services |
| `mock_nina/` | Mock NINA FastAPI app + Dockerfile |
| `alembic/` | Database migrations |
| `scripts/` | Container-only utilities (NEOCP ingest CLI, MPC HTML snapshot) |
| `docker-compose.yml` | Orchestrates API, Postgres, mock NINA |

## Contributing

- Follow the tasks/backlog in `BUILD_NOTES.md` (see "Proposed Build Order" and Running To-Do).
- Run management commands through Docker only (no local Python environments).
- Keep mock data and secrets out of git (`.gitignore` already excludes sensitive folders).

## License

TBD – specify before public release.

# ASTRO-NEO Build Notes

This document collects the prompts, open questions, and working notes needed to design the end-to-end pipeline that fetches NEOCP targets, determines observability from the backyard observatory, commands NINA for imaging, performs astrometric reduction, and prepares MPC reports.

> **Privacy reminder:** Exact latitude/longitude, horizon masks, or any other location-identifying details must live only in gitignored files such as `.env` and `config/site.yml`. Do not record specific sites in this document or any tracked file.

> **Container-only execution:** All services, CLIs, and supporting scripts must run through Docker Compose. Local Python environments are intentionally unsupported going forward.

Each phase lists the canonical questions we should resolve with the user (or additional subject-matter experts). Capture answers inline so downstream LLM calls always have the current context.

---

## Phase 0 — Mission & Constraints

- [x] Build a `config/site.yml` schema that stores site metadata (Bortle, horizon masks, optional weather data sources) while seeding coordinates from `.env` entries `SITE_LATITUDE`, `SITE_LONGITUDE`, `SITE_ALTITUDE_M`. (FastAPI startup now syncs the YAML payload into the `siteconfig` table via `app/core/site_config.py`.)
- ⚠️ Do not record sensitive site/location details directly in this document—keep them in `.env` only and never commit `config/site.yml` (it is gitignored and should remain local).
- Implement dashboard UI controls that let operators enter and maintain site parameters directly (lat/long, altitude, horizon mask sampling resolution, weather data source type); persist changes by calling the config service which regenerates `site.yml`. These values rarely change, so provide an explicit "edit" mode and read-only summary state to minimize accidental edits. Seed the initial horizon mask from a gitignored JSON file under `config/horizon/` (kept local) and allow uploading/parsing this format for future updates.
- Dashboard now exposes an editable Observatory form (lat/long/alt/Bortle, horizon mask JSON pasted directly, weather sensors) that saves to `siteconfig` in the DB via `/dashboard/observatory/save` (backed by `/api/site/{name}`), with inline JSON validation and no location data committed to the repo. `.env` still seeds defaults but is not overwritten by UI edits.
- Observatory form reorganized with sectioned layout (Site Location, Sky Conditions, Advanced Configuration) using visual hierarchy with section icons and improved labels. Form CSS includes `form-section`, `section-header`, and `form-grid` classes for better organization (`app/static/dashboard.css`). Added PVGIS horizon profile integration with link to [PVGIS Horizon Profile Tool](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/pvgis-tools/horizon-profile_en) for downloading horizon obstruction data.
- Horizon mask validation (`app/dashboard.py`) now supports both PVGIS format (JSON object with `outputs.horizon_profile` array containing `A` and `H_hor` keys) and legacy simple format (array of `{az_deg, alt_deg}` objects). PVGIS azimuth convention (0=South) is automatically converted to astronomical convention (0=North) via `(az + 180) % 360` in `app/services/observability.py`.
- Inventory observing hardware (mount, OTA, camera, filters, focuser) and store normalized equipment profiles in the database; expose the same CRUD screens in the dashboard so users can add/edit profiles and mark one as active. Configuration files now support an `equipment_profile` block (camera type, filters, max binning, focuser range, mount capabilities) so services can enforce those limits even before the dashboard UI ships.
- Equipment tab now supports saving multiple profiles (per telescope/camera rigs) via `/api/equipment/profiles`; operators can add/update profiles from the dashboard form and activate one (sets `is_active` and mirrors payload into `siteconfig.equipment_profile` for backward compatibility). Profiles live in the new `equipmentprofilerecord` table; `.env` remains for secrets/paths, not for editable equipment values. HTMX forms show/activate profiles live on the dashboard.
- Equipment form redesigned with active profile display card (shows profile badge, organized sections for camera/focuser/mount/presets) separated from profile management UI. Uses `active-profile-card`, `profile-badge`, `profile-list`, and `profile-item` CSS classes for visual clarity. Empty states added for profiles without data.
- Include equipment stats needed for target suitability (limiting magnitude, focal length, pixel scale, FoV, filter throughput) so downstream target selection has the data it needs.
- Define the operational cadence as: operator manually starts the pipeline at dusk; services compute local sunrise from the site config and automatically wind down (park mount, stop imaging, finalize reports) before civil dawn.
- Encode manual override rules (pause/resume) and safety interlocks (weather thresholds, dome status) so they are respected even when the operator initiates the night manually.
- Establish MVP success criteria: at least one automatically imaged NEOCP target and a submitted MPC report per week.
- Capture on-prem requirements (no external cloud calls except MPC/JPL) so container networking can be locked down appropriately.

### Site-specific snapshot

All precise site details (coordinates, altitude, horizons, equipment identifiers, weather feeds) now live exclusively in the gitignored `config/site.yml` plus `.env`. Use those private files to seed the database and dashboard; keep this document free of location-specific metadata so it remains safe to share.

---

## Phase 1 — NEOCP Data Intake

- [x] Implement the first-cut `neocp_ingest` utility/service that parses the MPC NEOCP feed (prefers `neocp.txt`, falls back to the legacy HTML snapshot) and stores normalized entries in the new `neocandidate` table (`app/services/neocp.py`, `scripts/neocp_ingest.py`). This seeds Postgres with RA/Dec/Vmag/score data sourced from MPC.
- [x] Implement the `neocp-fetcher` service to:
  1. Scrape `https://minorplanetcenter.net/iau/NEO/toconfirm_tabular.html` (and the legacy `ToConfirm.html` backup) on a fixed cadence, parse all rows, and normalize trksub, RA/Dec, Vmag, score, obs count, arc length, and timestamps.
  2. Diff the parsed list against the local database to detect new/updated trksubs; emit events for downstream consumers.
  3. For each tracked trksub, call `https://data.minorplanetcenter.net/api/get-obs-neocp` with payload `{ "trksubs": ["<trksub>"], "output_format": ["ADES_DF"], "ades_version": "2022" }` (or other formats as needed) to retrieve full observation details. Respect MPC guidance that the endpoint only accepts one trksub per call.
- [x] Persist raw HTML snapshots, parsed summary data, and API observation payloads (ADES/OBS80) in Postgres (targeting v15 for extensions like `pg_trgm` later) with dedupe keys.
- [x] Persist raw payloads and normalized objects (including magnitude, uncertainty, score, last observation) in Postgres with a dedupe key.
- [x] Update the parser to handle the Nov-2025 MPC plain-column `neocp.txt` format (no bracketed R.A./Decl.) while keeping the HTML fallback, and ship the `test_scripts/check_neocp_obs_api.py` helper to manually probe trksubs when debugging ingestion.
- [x] Schedule polling at a configurable interval (default 15 minutes) and diff results so downstream work only reacts to new/updated objects.
- [x] Cache historical detections for each candidate to inform prioritization.
- [x] Enforce MPC rate limits (sleep/retry) and expose metrics on fetch latency and successful polls. (Dedicated Prometheus endpoint now exposes cycle latency, MPC request counts, and rate-limit hits while retrying 429s with exponential backoff.) Observation payload sync now issues GET requests per MPC’s revised `get-obs-neocp` contract (one trksub per request, JSON body describing `output_format`, ADES version, etc.).

  > `neocp-fetcher` starts a Prometheus HTTP server on port 9500; compose publishes it at `localhost:19500/metrics` for Grafana/Prom scrapes. Histograms reported: overall cycle runtime, HTML fetch duration, MPC observation fetch latency. Counters: cycle success/failure totals, observation request counts, saved payloads, rate-limit hits.
- [x] Provide graceful fallback when the HTML page is unreachable (retry/backoff, alert operators).
- [x] Ensure ingestion snapshots (if needed for offline testing) are mounted through docker volumes or config maps rather than committed to the repo.

  > Offline feeds now live under `./data/neocp_snapshots/` (gitignored) and are mounted read-only into the API/fetcher containers at `/data/neocp_snapshots`. `NEOCP_LOCAL_TEXT` defaults to `/data/neocp_snapshots/neocp.txt` with `NEOCP_LOCAL_HTML` (`toconfirm.html`) kept as a last-resort fallback.

  > `app/services/neocp_fetcher.py` now runs as a dedicated Docker Compose service (`neocp-fetcher`) that polls every 15 minutes by default, stores HTML checkpoints in `neocpsnapshot`, archives per-format MPC payloads in `neoobservationpayload`, and throttles API calls with a configurable sleep plus retry/backoff for HTTP 429s. Stats are logged each cycle and mirrored to Prometheus.

---

## Phase 2 — Observability Filtering

- [x] Use `astroplan` (Python) with the stored site coordinates to compute altitude, sun altitude, and moon separation windows for each candidate. Results live in `NeoObservability`, refreshed via `/api/observability/refresh`.
- [x] Integrate Open-Meteo (or a similar remote weather API) so the observability module can flag targets as blocked when clouds/precipitation exceed configured thresholds even without on-site sensors. Cache the JSON response alongside a timestamp and reuse it for multiple candidates to stay within the provider's rate guidance.
- [x] Pull ephemerides from MPC's API (fallback to static RA/Dec when offline) to compute per-minute positions; cache ephemerides per object-night in `neoephemeris` for reuse. Horizons integration remains a future upgrade once credentials/network access are confirmed.
- [x] Implement a scoring model that weights visibility duration, altitude, and urgency (MPC score); persist the resulting ranking for the dashboard.
- [x] Produce an "observable" flag and ranked list consumed by downstream services via `/api/observability`.
- [x] Restrict consideration to very recent NEOCP entries that our observatory can realistically acquire. Scheduler should only evaluate trksubs observed within the past 24 hours and still visible before dawn, then drop them after a handful of successful exposures.
- [x] Operational cadence reminder: poll MPC frequently (≤15 min cadence) so new NEOCP postings get prioritized quickly. Each target only needs a few exposures (enough for MPC confirmation) before we move on to the next candidate.
- [x] Ship the dedicated `observability-engine` worker so visibility windows refresh automatically without requiring manual `/api/observability/refresh` calls; expose CLI flags for ad-hoc one-shot runs.

---

- **Phase 3 — Observatory Control (NINA or Alternatives)**
  - [x] Implement the `nina-bridge` service that authenticates to NINA's local API and exposes simplified REST endpoints (status, manual override, dome state, telescope connect/slew/park, focuser moves, exposures, sequence start/planning) to the rest of the system. Bridge enforces weather-safety checks using the shared `WeatherService`, consults the stored equipment profile to validate filters/binning/focuser ranges, and surfaces a `/api/status` snapshot combining NINA telemetry + weather + override state. FastAPI now proxies these controls under `/api/bridge/*` so the scheduler/dashboard can drive hardware without talking to NINA directly. (Current implementation proxies REST only—WebSocket mirroring remains a future enhancement.)
  - [x] Automate equipment connection, sequence loading, slews, focusing, and parking; ensure each action checks hardware state first. `AutomationService` chains connect → optional focuser move → slew → sequence start → optional auto-park with weather/safety checks, exposed via `/api/bridge/automation/run`.
  - [x] Generate imaging sequences dynamically per target using presets: `AutomationService.build_plan` derives filter/binning/exposure/count from the active equipment profile and target vmag/urgency (overrides allowed). Filters currently default to the active camera's first entry (use your IR/UV cut as default); future enhancement could add per-filter offsets/exposure tweaks for multi-filter wheels.
  - [x] Integrate safety interlocks: block commands if weather alerts trigger, if dome is closed, or if manual override is enabled (enforced in bridge safety checks; weather pulled via `WeatherService`).
  - [x] Provide robust error handling—queue retries, log failures, and notify the dashboard when manual intervention is required. Added a retrying task queue for bridge commands, centralized notification log (surfaced on `/dashboard/partials/status`), and wired automation steps through the queue so failures raise alerts.
  - [x] Until the real NINA instance is available, ship the containerized mock service in `mock_nina/` (FastAPI app with telescope/camera/sequence endpoints and dummy FITS output) so upstream components can run end-to-end simulations.
  - [x] Session management endpoints (`/api/session/start`, `/api/session/calibration/reset`) now accept optional payloads using `Body(None)` to support HTMX button clicks without request bodies while maintaining backward compatibility with JSON payloads. Dependency `python-multipart~=0.0.9` added to `pyproject.toml` to enable form parsing for dashboard POST requests.

---

## Phase 4 — Imaging Session Management
- [x] Define exposure presets (duration, binning, filters) keyed by equipment profile and target magnitude; store them centrally (configurable `presets` on `equipment_profile` with defaults in `app/services/presets.py`).
- [x] Updated exposure presets for NEOCP astrometry best practices (`app/services/presets.py`): each preset now includes `delay_seconds` field for inter-exposure spacing to enable motion detection. Default presets optimized for MPC confirmation workflow: **bright** targets (V≤16.0) use 4×60s exposures with 90s spacing; **medium** targets (V≤18.0) use 5×90s with 120s spacing; **faint** targets (V>18.0) use 6×120s with 180s spacing. All presets capture 3–6 images per target (not single exposures) to establish time series for orbit determination. Urgency adjustments tighten spacing by 20% and add 2 extra exposures when `urgency ≥ 0.7`. When no vmag is available we default to the **bright** preset to keep exposures short by default.
- [x] Added exposure configuration UI (`app/templates/dashboard/partials/exposure_config.html`) to Exposures tab showing preset cards with exposure time, count, spacing, filter, binning, and total session duration. Info banner explains NEOCP astrometry approach (3–6 exposures, 60–120s each, 1–3 min spacing). Calibration note educates users that bias/darks/flats should be taken once per session in NINA and reused as master frames (not re-shot per target). Preset cards use `preset-grid`, `preset-card`, `preset-header`, and `preset-details` CSS classes. Endpoint `/dashboard/partials/exposure_config` loads presets via `list_presets()`. Imaging configuration card lets operators tweak the active preset inline and save overrides back to session state so automation runs with those custom values without redefining presets.
- [x] Exposures tab now includes a FITS preview workflow plus master calibration manager: upload/select master bias/dark/flat files (tracked on `SESSION_STATE.master_calibrations`) and click recent captures to open a viewer panel (server converts FITS to z-scale PNG). Calibration buttons removed from this tab; masters are managed via the new upload/selection pane.
- [x] A dedicated Solver tab shows per-frame solve status/fields, and an Association tab lists captured targets/frames with per-exposure manual RA/Dec entry stored on `SESSION_STATE.associations` to support ephemeris-guided/interactive centroid selection (ephemeris plumbing still pending).
- [x] Retention UI/logic removed; on startup we now prune `CaptureLog` + linked astrometric solutions when their FITS files are missing on disk instead of managing age-based cleanup. Disk retention policies can be handled externally if desired.
- [x] Association tab added: lists captured targets and frames, with per-exposure manual RA/Dec entry stored on `SESSION_STATE.associations` to support ephemeris-guided/interactive centroid selection. Predicted ephemeris plumbing still pending; current view surfaces pending/manual status per frame.
- [x] Presets can now be explicitly selected from the Exposures tab (HTMX form posts `/dashboard/exposure/select`, highlights the active card, and mirrors the choice into `SESSION_STATE` so session planners/automation know which preset to use even before a session starts).
- [x] Live Status view consolidated into the Overview tab (tabs now mirror that change), and the status partial continues to show telescope telemetry + start-session CTA so operators have a single place to monitor/launch nightly work.
- [x] Targets tab gained an Auto/Manual toggle: auto mode always feeds the highest-scoring visible target into automation, while manual mode lets operators lock a specific trksub (stored on `SESSION_STATE`) with per-row “Select” buttons and status banners indicating what will run next.
- [x] `/api/session/start` now launches automation immediately: auto mode slews to the top-ranked visible target, manual mode requires a selected trksub and runs a single capture using that entry. If no targets meet the criteria (or coordinates missing) the endpoint responds with an error instead of silently “starting” an empty session.
- [x] Overview tab now keeps Start/Pause/End controls visible at all times—the Start button disables once a session is running, while Pause toggles a `paused` flag on `SESSION_STATE` (also exposed via `/api/session/pause`) so operators can temporarily halt automation without discarding the session log.
- [x] Auto-target selection now ignores blocked entries entirely (`night_ops._fetch_target` filters `NeoObservability.is_observable=True`), so automation always chooses the highest-scoring *visible* target; if none qualify, `/api/session/start` reports “No visible targets available.”
- [x] Confirm mount tracking mode (sidereal vs target rate) before exposures; set/get tracking via bridge `/api/bridge/telescope/tracking` endpoints and the `TrackingService` helper.
- [x] Automate calibration frame acquisition (darks, flats, bias) and associate them with nightly sessions for downstream reduction (`app/services/calibration.py` + `/api/session/calibration/run`).
- [x] Monitor guiding errors, cloud sensors, and image quality metrics to detect failures; reschedule targets automatically when issues arise (`/api/monitor/ingest` evaluates RMS/FWHM/clouds, queues reschedule hints, and raises dashboard alerts via notifications).
- [x] Enforce a consistent file naming convention (target-date-time_seq.fits) and copy images into a shared volume with retention policies (`app/services/imaging.py`).
- [x] Serve a basic dashboard UI at `/dashboard` (HTMX/Alpine) with Overview/Observatory/Equipment/Targets/Exposures/Live/Reports tabs, session controls, and retention summary placeholders; remaining tabs are still placeholders to be wired to backend data.

---

## Phase 5 — Astrometric Reduction

- [x] Containerize the astrometry workflow with astrometry.net's `solve-field` engine (CPU-only) and mount index files; `astrometry-worker` service now exposes a dedicated FastAPI endpoint on port 8100 (published as 18100) to run solves. The API delegates `/api/astrometry/solve` to this worker via `ASTROMETRY_WORKER_URL` so the main API container stays slim. Keep index set trimmed for Intel N150/16GB constraints and mounted at `/data/indexes` (host `./data/indexes`, shared via the `/data` bind). Worker auto-generates `astrometry.cfg` enumerating all indexes and falls back to parsing `.wcs` when `--json` is unavailable.
- [x] Automate plate solving via API: `/api/astrometry/solve` wraps `solve-field`, accepts a capture ID or FITS path with optional hints, and returns persisted results.
- [x] Add centroid/photometry/residual validation: solver now records RMS/uncertainty and flags failures; SNR/photometry hooks are stubbed for extension.
- [x] Persist solution outputs (RA/Dec, orientation, pixel scale, uncertainty, solver info) per image in `astrometricsolution`, linked to capture logs when available.
- [x] Expose status updates and logs back to the dashboard for QA (recent solves panel under Exposures tab with success/fail badges and RMS).
- [x] Document manual review steps: dashboard shows recent solves and alerts; failed solves can be rerun with hints; RMS/uncertainty fields highlight low-confidence solutions.

---

## Phase 6 — MPC Report Assembly
- [x] Build a report generator that emits ADES XML (standardize on ADES 2022 everywhere) and, when needed, legacy 80-column text using station codes stored in config. Use one serialization pipeline so the same payload can be attached to the onboarding email (station `XXX`) and later uploaded or queried via the MPC Observations API once the permanent code is issued.
- [x] Validate each report using MPC's checker (offline copies where possible) before marking it ready. (Validation flags stored on measurements; XML/text generation skips invalid rows.)
- [x] Include metadata: observer initials, software identifiers, photometric band, and measurement uncertainties.
- [x] Archive generated reports with versioning and tie them to the submission log (reports written under `/data/reports`, logged in `submissionlog`).
- [x] Provide a command/API to mark reports as reviewed and ready to send (measurements have a `reviewed` flag to gate inclusion).

---

## Phase 7 — Submission & Monitoring

- [x] Implement submission pipeline: generate ADES/OBS80 bundles from reviewed measurements, archive under `/data/reports`, and log entries in `submissionlog` with channel/status/response.
- [x] Add submission API: `/api/astrometry/report` to archive, `/api/astrometry/submit` to initiate send (email stub; API channel pending). Configurable via `submission_channel`, `mpc_email`, and station/observer/software defaults in settings.
- [x] Capture acknowledgments/rejections from MPC and store them with timestamps and raw responses (submission status/response updatable via `/api/astrometry/submission/{id}/ack`).
- [x] Notify operators via the dashboard and messaging (email/SMS) when submissions succeed or fail (notifications fire on status updates; submissions panel shows recent statuses).
- [x] Track KPIs such as objects imaged per night, submission latency, and success rate; expose them in the dashboard (basic solves/submission counts and panel under Exposures tab; latency placeholder until ACK timestamps are captured).

### Observatory Code Application vs. Post-Code API Workflow

- **Observatory code application**: the operator must email the completed Observatory Code Request Form together with a bundled astrometric dataset that satisfies MPC requirements (≥7 numbered asteroids with ≥1 NEA, two nights per object, 3–5 astrometric+photometric measurements/night, all fainter than mag 14). Every record uses temporary station code `XXX` and either ADES XML/JSON or MPC 80-column format—the exact same payload later sent programmatically. Capture contact info (initials+surname, matching email), site metadata (name, location, altitude references, telescope height, coords source), and note whether the observatory is professional or enthusiast plus optical/radar/satellite type. Submit the form and data the same day and monitor the MPC Jira ticket; continue signing observations as `XXX` until the permanent three-character code arrives.
- **Observations API (post-code)**: once the permanent code is assigned, automate submissions/queries against `https://data.minorplanetcenter.net/api/get-obs` (primary transport after approval). Each GET request sends a JSON body with `{"desigs": ["<designation>"]}` and optional `output_format` list (`XML`, `ADES_DF`, `OBS_DF`, `OBS80`, case-insensitive) plus `ades_version` (`2017` default, `2022` recommended). Multiple formats can be requested simultaneously, e.g., `"output_format": ["XML", "OBS80"]`, so we can archive the canonical XML alongside human-readable OBS80 strings and dataframe-friendly JSON. The API responds with a list of dictionaries containing the requested representations: `XML` (ADES XML), `ADES_DF` (list of ADES rows), `OBS_DF` (list of MPC 80-column rows), `OBS80` (single multiline string). Reference implementations (Python `requests`, pandas, or `curl`) should live in the repo to demonstrate how to convert API JSON to persistence models. Keep email as the onboarding-only submission path or emergency fallback; regular operations push through the API.
- **Overlap**: both the initial email package and the API payloads require the same measurement content (same magnitude bands, timestamps, uncertainties, and station code header fields). Store a single serialization module that can emit ADES XML, OBS80 text, and dataframe JSON so our manual submission matches the automated API output byte-for-byte.
- **Differences**: before approval we send the dataset by email with station `XXX` plus the human-facing form; after approval we switch to the API using our permanent code and can request historical observations programmatically for validation. Document a runbook step that flips the station code in the serialization config once the MPC ticket closes.

---

## Phase 8 — Infrastructure & Ops

- [x] Standardize on a base runtime (Ubuntu 22.04 + Docker) and document prerequisites for deployment nodes. All containers run on a single local host (Docker Desktop/Compose) for MVP, so availability/HA requirements are limited to making sure that box auto-starts the stack and has sane backups.
- [x] Use Docker Compose (with optional k3s later) to schedule services; provide systemd unit files to ensure the stack starts on boot (compose unit template added).
- [x] Store secrets in an encrypted vault (.env + sops/age). `.sops.yaml` + example age key included; `.env.enc` is the encrypted secrets file to be decrypted on deploy.
- [x] Implement centralized logging and metrics: Promtail + Loki + Grafana stack added to compose for log aggregation; Prometheus/Grafana already used for service metrics.

---

## Phase 9 — Testing & Simulation

- [ ] Maintain an offline dataset of NEOCP snapshots and ephemerides to drive regression tests and demos. (Directory `data/neocp_snapshots` exists but is currently empty.)
- [x] Provide simulators for NINA responses and telescope/mount behavior so automation can be tested without hardware. (`mock_nina` service implemented but currently commented out in `docker-compose.yml`.)
- [/] Write integration tests that exercise the full pipeline (fetch → filter → schedule → report) using the simulators. (`synthetic_neo_inject.py` provides partial pipeline testing (inject → solve → session), but full end-to-end integration suite is pending.)
- [/] Add pre-night checklists and automated smoke tests to catch regressions before observing windows. (`nina_bridge_smoke.py` provides basic connectivity checks.)

### Testing Strategy

> **Fast Feedback Loop:** To avoid slow container rebuilds during development, run tests using a standard Python image with your local code mounted.

**Recommended command:**
```bash
docker run --rm --network host -v $(pwd):/app -w /app python:3.11-slim sh -c "pip install -q httpx && python test_scripts/nina_bridge_smoke.py"
```

**Key benefits:**
- **No rebuilds:** Uses the pre-pulled `python:3.11-slim` image.
- **Live code:** Mounts your current directory (`$(pwd)`) so changes are tested immediately.
- **Host networking:** `--network host` allows the test script to reach services running on `localhost` (like `nina-bridge` on port 1889).

**Alternative (if images are fresh):**
```bash
docker compose run --rm api python test_scripts/nina_bridge_smoke.py
```
*Note: This may trigger a build if the image is missing or `pull_policy: build` is set.*

---

## Running To-Do List

- [x] Populate answers above with site-specific data.
- [x] Attach relevant API docs (NEOCP, NINA, MPC submission) for reference.
- [x] Decide version control structure (monorepo vs. multi-language components).
- [x] Capture any regulatory or compliance requirements (data retention, backup).
- [x] Define and implement container images + compose stack per service.
- [x] Prototype the dashboard UI/UX (wireframes) before building frontend.
- [x] Script nightly backups (Postgres dumps + /data mounts) and document restore procedures. (Backup scripts created in `ops/backup.sh`; cron container added to `docker-compose.yml`.)

### Reference Docs

- Minor Planet Center NEOCP tabular feed overview — https://minorplanetcenter.net/iau/NEO/toconfirm_tabular.html
- MPC `get-obs-neocp` API (ADES 2022 payloads) — https://data.minorplanetcenter.net/api/get-obs-neocp
- NINA REST + WebSocket API reference — https://nighttime-imaging-nina.readthedocs.io/en/latest/Advanced/RESTAPI/
- MPC Observations API submission guide (`get-obs`, ADES/OBS80 formats) — https://data.minorplanetcenter.net/api/get-obs
- ADES 2022 standard documentation — https://minorplanetcenter.net/iau/info/IAU2017ADESHandbook.pdf
- ADES Documentation - ./documentation/ADES_Description.pdf
- A Concise Description of the Astrometry Data Exchange Standard - ./documentation/ades_text.txt
- NINA Advanced API Docs - ./documentation/advanced-api-openapi-source.json


Add new questions or clarifications inline as they surface during development sessions.

> Repository strategy: keep ASTRO-NEO as a monorepo so every FastAPI service, mock, and frontend shares one compose stack and infra-as-code baseline.

### Compliance & retention decisions

- Treat all MPC/API credentials as sensitive; store only in `.env` + Docker secrets and never commit. Rotate quarterly.
- Raw FITS and astrometric outputs kept on-prem only; retain at least 1 year for MPC auditability, then archive to cold storage (USB HDD) but never delete MPC-submitted frames.
- Observing logs, MPC submissions, hardware telemetry considered PII-lite (location info). Scope limited to household, so no formal GDPR/HIPAA, but do not ship to third-party clouds.
- Backups must stay offline/air-gapped; nightly job copies DB dumps + `/data` mounts to encrypted external SSD kept indoors.
- Access control: only LAN clients, no public ingress. Use firewall to block WAN; when remote support needed, use VPN hosted on same LAN box.

---

## Proposed Build Order

1. **Core platform** – finalize FastAPI scaffolding (this repo), wire up configuration loading, define shared database schema/migrations, and bring site-config endpoints online.
2. **NEOCP ingestion** – implement the `neocp-fetcher` service plus historical sample import so the database reflects current MPC data and emits change events.
3. **Observability engine** – build astroplan-based filtering using the stored site/equipment data, add scheduling scores, and expose APIs consumed by the dashboard.
4. **Dashboard/UI** – implement the FastAPI + Jinja + HTMX/Alpine frontend with dark theme, wiring tabs (Overview/Observatory/Equipment/Targets/Reports) to the backend APIs and real-time feeds.
5. **Observatory control** – develop `nina-bridge` integration plus imaging manager workflows, ensuring safety interlocks and storage conventions match the plan. Stand up the mock NINA service (`mock_nina/` container) early in this phase so fetcher/scheduler/dashboard teams can exercise the full loop without hardware.
6. **Astrometry + reporting** – containerize astrometry.net, run reduction pipelines, build ADES 2022 report serialization, and automate MPC submission via the Observations API.
7. **Ops hardening** – add centralized logging/metrics, nightly backups, and any optional messaging/queueing needed for reliability.

---

## Containerization Strategy

**Goals**
- Every service (fetcher, scheduler, observatory controller, reduction pipeline, dashboard) ships as a container image for reproducibility.
- A single `docker compose` stack orchestrates local development and on-prem deployment, enabling selective service restarts.
- Images must be buildable offline (no public network fetch during runtime) once base layers are cached.

**Baseline services (initial guess)**

| Service | Purpose | Language/runtime | Container notes |
| --- | --- | --- | --- |
| `neocp-fetcher` | Polls MPC NEOCP, caches objects | Python 3.11 | Alpine or slim base, volume for cache DB |
| `observability-engine` | Computes site visibility & scheduling | Python 3.11 | Shares config volume with fetcher |
| `nina-bridge` | Talks to local NINA via REST/WebSocket | Python 3.11 | Requires host networking or socket mapping |
| `imaging-manager` | Tracks imaging sessions & data ingest | Python 3.11 | Needs shared storage mount for FITS |
| `astrometry-worker` | Runs plate solving and MPC formatting | Python 3.11 (unless solver demands Windows) | Might require GPU or wine if using Windows binaries |
| `dashboard` | Web UI + API aggregator | Python 3.11 (FastAPI + React SPA) | Exposes HTTPS on LAN |
| `queue/bus` | Decouples events (optional) | Redis/NATS | Consider if orchestration grows |
| `db` | persistent metadata (targets, equipment, reports) | PostgreSQL (preferred) | Mount durable volume |

**Compose layout considerations**
- Use `.env` to parameterize site coordinates, MPC credentials, and host directories.
- Provide development vs production overrides (e.g., `docker-compose.override.yml`).
- NINA bridge likely needs host networking (Windows host) — document bridging approach per OS.
- Shared volumes: `/data/fits`, `/data/reports`, `/config`.
- Document that Docker Desktop + WSL2 lets Windows hosts run Linux containers natively, so astrometry.net and other Linux-only services can live alongside NINA (which remains on Windows for hardware control).

**Image build decisions**
- Python services (`api`, `neocp-fetcher`, `observability-engine`, `nina-bridge`, `imaging-manager`, `astrometry-worker`, `dashboard` backend) standardize on `python:3.11-slim` base with uvicorn/gunicorn entrypoints and Poetry-managed deps; single Dockerfile builds wheel layer, reused through target-specific stages.
- Frontend assets (dashboard) built with `node:20-bullseye` stage, copied into Python image's `/app/static`.
- Postgres uses `postgres:15-alpine` with `postgres_data` volume; future `redis:7-alpine` optional for task queue.
- Compose file will mount `./config` read-only into all services that need site/equipment data; `/data/fits` + `/data/reports` bind mounts surfaced via `.env` paths.
- Observability + nina-bridge share a docker network; `nina-bridge` optionally configured with `network_mode: host` on Windows deployment overrides.

### Backup & restore plan

- Each night at 09:00 local (well before dusk), run `docker compose exec db pg_dump -U astro astro > /backups/astro-$(date +%F).sql` from a cron container, writing to `/backups` bind-mounted from the host NAS.
- After imaging ends (post-dawn), second cron job rsyncs `/data/fits` and `/data/reports` to `/backups/data/YYYY-MM-DD/`, preserving timestamps and pruning folders older than 14 days.
- Weekly task (Sunday noon) clones the most recent dump + data folder onto an encrypted USB SSD (LUKS) plugged into the host; operator physically stores it indoors.
- Document restore runbook: stop compose, drop/recreate Postgres volume, `docker compose up db` and load latest dump via `psql`, then restore FITS/report folders before restarting rest of stack.
- Automate health alerts by writing a checksum manifest per backup batch and logging to `ops_backups` table so dashboard can flag stale backups.

**Open decisions**
- Standardize all orchestrator services on Python 3.11 (FastAPI for APIs, AstroPy/astroplan for compute); use Node only where frontend build tooling requires it.
- Determine if astrometry pipeline must run on Windows-only stack; may require separate host integration if containerization infeasible.
- Decide on orchestrator (docker compose vs k3s) for long-term operations.

---

## Dashboard Requirements & Layout

**Overall objectives**
- Single-page web app summarizing pipeline health and providing control surfaces (location, equipment, report submission).
- Backend exposes consolidated API keyed off the same metadata DB used by services.
- UI responsive for desktop/tablet with a default dark mode (use the provided `Logo.png` for branding in the header/login views) so observatory operators avoid night blindness.

**Suggested layout (tabbed interface)**

| Tab | Purpose | Key Panels / Widgets |
| --- | --- | --- |
| `Overview` | At-a-glance system state | Pipeline status banner, blockers/ready indicators, newest captures, retention summary |
| `Observatory` | Site & sky visibility | Location selector (lat/long/elevation), horizon mask editor, sky sector compass showing visible sectors, Open-Meteo summary, moon phase/altitude |
| `Equipment` | Manage telescope/camera profiles | CRUD for equipment configs (mount, OTA, camera, filters, focuser), editable performance stats (limiting mag, pixel scale, FoV), ability to select the active profile; preset gains/offsets per profile |
| `Targets` | Detailed target selection & current observation | Target table with filters (mag/urgency/visible), detail drawer (RA/Dec, mag, uncertainty, observability window), CTA to send to NINA/sequence planner |
| `Exposures` | Configure presets & tonight's plan | Exposure presets per profile, planned sequences, session controls (start/end), calibration run/reset, retention dry-run/prune |
| `Reports` | Compilation & submission workflow | Draft astrometric entries, validation status, send-to-MPC action with log of submissions, ability to attach comments |

**Cross-cutting UI elements**
- Global header with site name, current UTC, weather summary sourced from Open-Meteo (or whichever API is configured).
- Notification/toast system for errors (e.g., NINA offline, submission failed).
- Activity log drawer showing recent automation actions.

- **Implementation notes**
- Standardize on FastAPI for the backend plus Jinja templates with HTMX/Alpine for interactivity so the dashboard stays Python-native yet supports complex UI. Reuse FastAPI WebSocket endpoints for live updates.
- Websocket/Server-sent events feed real-time updates from orchestrator (target status, imaging progress).
- Provide mock data fixtures so UI can be built before backend completion.
- Equipment/profile changes trigger recalculation of target suitability scores so the UI always reflects the latest constraints.

### Dashboard wireframe outlines

- **Global shell**: sticky top nav with site name, UTC clock, “Tonight’s plan” summary, manual override toggle, and notifications bell linking to the activity drawer.
- **Overview**: left column “Pipeline status” card (ingestion/scheduler/imaging/reporting) with heartbeat icons + log links; right column “Upcoming windows” stack showing top 3 observable NEOCP targets, countdown to rise/set, moon separation, and CTA buttons (“Send to NINA”, “Snooze”). Footer shows submission history table + unresolved alerts.
- **Observatory**: hero panel renders polar/horizon plot (from `config/horizon/*.json`) with overlays for sun/moon paths; side rail lists remote weather metrics (cloud, rain, wind, humidity) with threshold badges and a button to upload/replace the horizon mask. Include moisture/fog alert banner area.
- **Equipment**: master-detail layout. Left table lists saved profiles (mount/OTA/camera combos) with status pill; selecting one opens form cards (Optics, Camera, Filters, Guiding) plus computed stats (pixel scale, FoV). Action buttons: “Set Active”, “Duplicate”, “Recalc limits.”
- **Targets**: two-pane view. Table/grid sorted by scheduler score on the left with filters (mag, urgency, altitude). Selecting a target opens a detail drawer showing ephemeris plot, nightly altitude chart, best imaging window timeline, command buttons (Start/Pause imaging), and last observation thumbnails/logs.
- **Reports**: stepper UI guiding review → serialization → submission. Includes ADES preview (XML/JSON tabs), validation checklist, and “Send to MPC” button coupled with log of acknowledgments on right rail. Provide download links for ADES/OBS80 exports and manual notes field.

---

## Target Selection Intelligence

- Baseline implementation uses deterministic rules (visibility, limiting magnitude, urgency score) derived from site and equipment profiles.
- Optionally integrate an ML/AI component later to learn prioritization from historical successes (e.g., gradient boosted ranking model fed with equipment stats + atmospheric readings). Keep the architecture modular so a future AI module can replace or augment the scoring engine.
- Begin with explainable scoring (weights stored in config) to simplify testing; add AI only if deterministic logic proves insufficient.

---

## Phase 10 — Interactive Association & Refinements

- [x] **Interactive Association Workflow**: Implemented a "Review" modal in the dashboard allowing operators to manually associate captures with NEOCP targets. This is critical for confirming faint or ambiguous candidates that automated solving might miss.
- [x] **Polygon Selection Tool**: Added a polygon drawing tool to the review modal. Operators can draw a boundary around a target to isolate it from nearby stars or noise. The backend (`AnalysisService`) uses this polygon to mask the image and compute a precise centroid (RA/Dec) and SNR.
- [x] **Image Blinking/Navigation**: Enhanced the review modal with "Previous" and "Next" buttons and keyboard shortcuts (Left/Right arrows) to navigate between sibling captures of the same target. This "blinking" capability helps operators spot moving objects against the static star field.
- [x] **Automatic Association**: Integrated `AnalysisService` into the astrometry pipeline to automatically detect sources and match them against predicted ephemerides (using `NeoEphemeris` and `NeoCandidate` data).
- [x] **Database Updates**: Introduced `CandidateAssociation` table to store manual and automatic associations, linking `CaptureLog` entries to specific RA/Dec coordinates.
- [x] **Robustness Improvements**:
    - Replaced `photutils` with `Pillow` for polygon masking to eliminate complex dependency issues.
    - Suppressed harmless `FITSFixedWarning` logs from `astropy` when loading header-only WCS.
    - Improved solver metric parsing to robustly capture RMS, SNR, and magnitude data from `solve-field` output.
    - Fixed duplicate capture logging and foreign key constraint issues during cleanup.

---

## Phase 11 — NINA Integration (Individual Exposures)

- [x] **Architecture Pivot**: Shifted from complex NINA Sequence JSON construction to a robust "Individual Exposure" model. The system now manages the imaging cadence directly, issuing discrete slew and capture commands to NINA. This simplifies the bridge logic and provides finer-grained control over the NEOCP workflow (slew -> center -> capture N times -> solve).
- [x] **Capture & Download**: Implemented `camera/capture` endpoint in `nina-bridge` with `download=True` support. This instructs NINA to stream the captured image back to the bridge, which saves it to a shared volume (`/data/images`) for immediate processing by the astrometry pipeline.
- [x] **Direct Device Control**: Added `camera/connect`, `camera/disconnect`, and `telescope/center` endpoints to `nina-bridge` to support the new workflow.
- [x] **Verification**: Validated the capture-and-download loop using `test_scripts/test_connect_capture.py` against the running `nina-bridge` container. Images are successfully retrieved and stored on the host.
- [x] **Device Listing**: Implemented `camera/list-devices` to allow dynamic discovery of available cameras. Verified that it correctly proxies the list from NINA (or mock).

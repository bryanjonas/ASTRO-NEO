````text
Implement a “Mock NINA” HTTP service that simulates the parts of N.I.N.A.’s API my pipeline needs.

## Goals

- Run as a **single process web service** (FastAPI or Flask) that I can put in a Docker container.
- Provide a **NINA-like REST API** for:
  - Telescope control (slew, current position)
  - Camera control (start exposure, exposure status, path to FITS file)
  - Simple sequencing (start a scripted run of multiple exposures)
- Maintain **in-memory state** so responses are consistent.
- Optionally write **dummy FITS files** to disk so downstream code can exercise real file I/O.
- Be easy to configure via **environment variables**.

I don’t need to perfectly clone NINA’s real API surface – just something stable and realistic. We’ll define our own endpoints with a NINA-style structure.

Use **Python 3 + FastAPI + Uvicorn**.

## High-level architecture

Create a module `mock_nina` with:

- `main.py` – FastAPI app and endpoint definitions.
- `models.py` – Pydantic models for request/response payloads.
- `state.py` – In-memory “observatory” state (telescope, camera, sequence).
- `fits_utils.py` – helper to create tiny dummy FITS files.
- `config.py` – read environment variables.

Expose the app as `app = FastAPI()` in `main.py` so it can be run both directly and under Uvicorn in Docker.

## Configuration

Read these env vars (with sensible defaults):

- `MOCK_NINA_PORT` (default: `1888`)
- `MOCK_NINA_DATA_DIR` (default: `/data`) – where dummy FITS go.
- `MOCK_NINA_EXPOSURE_SECONDS` (default: `5`) – simulated exposure duration.
- `MOCK_NINA_MIN_ALT_DEG` (default: `5`) – minimum allowed telescope altitude.
- `MOCK_NINA_FAIL_RATE` (default: `0.0`) – probability (0–1) to randomly fail an exposure, for testing error handling.

If `MOCK_NINA_DATA_DIR` doesn’t exist, create it on startup.

## Internal state model

Use a singleton `ObservatoryState` class stored in `state.py`:

```python
class TelescopeState(BaseModel):
    ra_deg: float
    dec_deg: float
    is_slewing: bool = False

class CameraState(BaseModel):
    is_exposing: bool = False
    last_exposure_start: Optional[datetime] = None
    last_exposure_duration: Optional[float] = None
    last_image_path: Optional[str] = None
    last_status: str = "idle"  # idle, exposing, complete, failed

class SequenceState(BaseModel):
    is_running: bool = False
    current_index: int = 0
    total: int = 0
    name: Optional[str] = None
````

`ObservatoryState` holds:

```python
class ObservatoryState:
    telescope: TelescopeState
    camera: CameraState
    sequence: SequenceState
```

Initialize telescope position to something reasonable (e.g., `ra_deg=0, dec_deg=0`).

## Endpoints

Base path: `/api` (so URLs look like `http://host:1888/api/...`).

### 1. Health & status

* `GET /api/status`

  Returns snapshot of all state.

  Response JSON:

  ```json
  {
    "telescope": { "ra_deg": 12.3, "dec_deg": -5.4, "is_slewing": false },
    "camera": {
      "is_exposing": false,
      "last_status": "complete",
      "last_image_path": "/data/IMG_0001.fits"
    },
    "sequence": {
      "is_running": false,
      "current_index": 0,
      "total": 0,
      "name": null
    }
  }
  ```

### 2. Telescope endpoints

* `POST /api/telescope/slew`

  Request body:

  ```json
  {
    "ra_deg": 123.456,
    "dec_deg": -12.34
  }
  ```

  Behavior:

  * Immediately set `is_slewing = True`.
  * Simulate slew time by:

    * either sleeping a small fixed delay (e.g., 0.2s) or
    * just instantly setting `is_slewing = False` and updating RA/Dec.
  * Enforce `MOCK_NINA_MIN_ALT_DEG`:

    * For now, don’t compute true alt/az – just accept everything. Add a TODO to optionally reject low-altitude commands.

  Response:

  ```json
  { "status": "ok", "ra_deg": 123.456, "dec_deg": -12.34 }
  ```

* `GET /api/telescope/position`

  Response:

  ```json
  {
    "ra_deg": <current>,
    "dec_deg": <current>,
    "is_slewing": false
  }
  ```

### 3. Camera endpoints

* `POST /api/camera/start_exposure`

  Request:

  ```json
  {
    "exposure_seconds": 10.0,
    "filter": "L",
    "binning": 1
  }
  ```

  Behavior:

  * If `camera.is_exposing` is already true, return HTTP 409 with JSON `{ "error": "exposure_already_running" }`.
  * Set:

    * `is_exposing = True`
    * `last_status = "exposing"`
    * `last_exposure_start = now`
    * `last_exposure_duration = exposure_seconds`
    * Clear `last_image_path` for now.
  * Schedule completion:

    * Use a background task (e.g., FastAPI `BackgroundTasks`) that waits `exposure_seconds` and then:

      * draw a random number; if `< MOCK_NINA_FAIL_RATE`, mark failure:

        * `last_status = "failed"`
        * `is_exposing = False`
        * `last_image_path = None`
      * otherwise:

        * create a dummy FITS file in `MOCK_NINA_DATA_DIR`
        * set `last_image_path` to that path
        * `last_status = "complete"`
        * `is_exposing = False`

  Response:

  ```json
  {
    "status": "started",
    "expected_finish_utc": "2025-01-01T12:34:56Z"
  }
  ```

* `GET /api/camera/status`

  Response:

  ```json
  {
    "is_exposing": false,
    "last_status": "complete",
    "last_exposure_start": "2025-01-01T12:34:01Z",
    "last_exposure_duration": 10.0,
    "last_image_path": "/data/IMG_0001.fits"
  }
  ```

### 4. Sequence endpoints

Sequence = repeated exposures with optional slews.

* `POST /api/sequence/start`

  Request:

  ```json
  {
    "name": "test_sequence",
    "count": 5,
    "exposure_seconds": 10.0,
    "filter": "L"
  }
  ```

  Behavior:

  * If a sequence is already running, return 409.
  * Initialize `sequence` state: `is_running = True`, `current_index = 0`, `total = count`.
  * Launch a background task that:

    * Loops from 1..count:

      * updates `current_index`
      * calls the same logic as `start_exposure` (but internally)
      * waits for exposure to complete
    * At end: `is_running = False`.

  Response:

  ```json
  { "status": "started", "name": "test_sequence", "total": 5 }
  ```

* `GET /api/sequence/status`

  Response:

  ```json
  {
    "is_running": true,
    "name": "test_sequence",
    "current_index": 3,
    "total": 5
  }
  ```

### 5. FITS generation

Implement a helper `create_dummy_fits(path: Path, width=100, height=100)`:

* Use `astropy.io.fits` if available; if not, write a placeholder binary file.
* Fill with a small 2D numpy array (e.g., gradient or random noise).
* Include minimal header keywords like:

  * `SIMPLE  = T`
  * `BITPIX  = 16`
  * `NAXIS   = 2`
  * `EXPTIME`
  * `DATE-OBS`
  * `FILTER`
* The operation should be fast and not memory intensive.

### 6. Error simulation

Add ability to trigger errors for testing:

* If `MOCK_NINA_FAIL_RATE > 0`, randomly fail some exposures as described.
* Add optional query param `?force_fail=true` on `start_exposure` to force a failure path for unit tests.

### 7. Logging

* Log every request with method, path, and response status.
* Log important events:

  * telescope slews
  * exposure start / completion / failure
  * sequence start / step / completion

Use Python’s `logging` module with INFO level by default.

### 8. Running and Docker

In `main.py`, include:

```python
if __name__ == "__main__":
    import uvicorn
    from config import settings
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=False
    )
```

Create a `Dockerfile`:

* Base image: `python:3.11-slim`
* Install requirements: `fastapi`, `uvicorn[standard]`, `pydantic`, `numpy`, `astropy` (optional).
* Expose port 1888.
* Set default CMD to run uvicorn.

### 9. Usage examples

Example: start server locally:

```bash
MOCK_NINA_DATA_DIR=/tmp/mock_nina \
MOCK_NINA_EXPOSURE_SECONDS=2 \
python -m mock_nina.main
```

Then:

```bash
curl http://localhost:1888/api/status
curl -X POST http://localhost:1888/api/telescope/slew \
     -H "Content-Type: application/json" \
     -d '{"ra_deg": 120.0, "dec_deg": 22.0}'

curl -X POST http://localhost:1888/api/camera/start_exposure \
     -H "Content-Type: application/json" \
     -d '{"exposure_seconds": 5.0, "filter": "L", "binning": 1}'

curl http://localhost:1888/api/camera/status
```

The downstream system should be able to treat this service as a stand-in for NINA during development and CI.

```

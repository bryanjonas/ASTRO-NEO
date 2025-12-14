# NINA API Handling

Automated operations that steer NINA must follow a simple rule: **issue the command, then wait for the device state to settle before declaring success/failure**. The new helper script `scripts/nina_api_monitor.py` (run via `python scripts/nina_api_monitor.py`) encapsulates that mantra while exercising the vital bridge endpoints we care about today:

| Action | Endpoint(s) | Lesson |
| --- | --- | --- |
| Snapshot health | `/api/status` (via `NinaBridgeService.get_status()`) | Aggregated status polls the camera, mount, and focuser so we know when each device reports `is_slewing=False`/`is_exposing=False` before the next command. |
| Mount slew | `/equipment/mount/info` + `/equipment/mount/slew` | Re-read coordinates, issue the slew, then loop with `wait_for_mount_ready()` before assuming the mount has stopped moving. |
| Camera capture | `/equipment/camera/info` + `/equipment/camera/capture` | Validate the camera is connected/idle, request a short exposure, and treat `No capture processed` (or other 409/502 errors) as a warning while logging the failure so automation can decide to skip or retry safely. |

## Recommendations

1. **Never assume completion.** Each command in the script is followed by a polling helper (`wait_for_mount_ready`, `wait_for_camera_idle`) or a status snapshot so that the automation only moves on once the instrument reports readiness.
2. **Log and continue on recoverable errors.** The capture test logs errors such as “No capture processed” without crashing, demonstrating how the real system should treat transient rejections while keeping the main loop alive.
3. **Record the final state** (`post-slew`, `finish`) for debugging. The script logs key telemetry after each test block so operators can see if a device reverted to an unexpected state.

Run the script any time you need to revalidate NINA’s readiness; the lessons above should stay true when the automation issues `slew` and `capture` commands from `app/services/capture_loop.py` and `app/services/acquisition.py`.

## Previous test run (2025-12-13 13:21 UTC)

- Command: `docker compose exec api python scripts/nina_api_monitor.py`.
- **Mount slew**: Passed—`/equipment/mount/info` → `/equipment/mount/slew` → repeated `/equipment/mount/info` polls confirmed `is_slewing=False` before the final `post-slew` snapshot captured `ready=310.511×-0.734`.
- **Camera capture**: `/equipment/camera/capture` triggered a 502 gateway error because the underlying NINA instance responded `{"Success":false,"Error":"No capture processed"}` (the bridge logs that payload); the script now logs the failure and still exits cleanly so higher layers can decide whether to retry later.

## Test run (2025-12-13 13:56 UTC)

- Command: `docker compose exec api python scripts/nina_api_monitor.py`.
- **Mount slew**: Completed successfully—the slew target (`RA 311.4755°, Dec -0.3964°`) was issued, repeated `/equipment/mount/info` polls confirmed `is_slewing=False`, and the `post-slew` snapshot reports `ready=311.808×-0.302`.
- **Camera capture**: The quick 1 s exposure succeeded; `/equipment/camera/capture` returned `saved=True`, `solved=False`, and the returned file path `/data/images/NINA-API-TEST_20251213_185643.fits` proves NINA processed (but did not plate-solve) the frame. The bridge still records this result so automation can decide how to use the image.
- **Overall run**: The helper finished with a final `status` snapshot reporting the mount/camera idle (no mock-nina containers were involved—every call hit the real NINA service).

## Previous test run (2025-12-13 13:59 UTC)

## Previous test run (2025-12-13 14:01 UTC)

## Latest test run (2025-12-13 14:06 UTC)

- Command: `docker compose exec api python scripts/nina_api_monitor.py`.
- **Mount slew**: Completed successfully—the slew target (`RA 312.7709°, Dec 0.0411°`) was issued, repeated `/equipment/mount/info` polls confirmed `is_slewing=False`, and the `post-slew` snapshot reports `ready=313.103×0.138`.
- **Camera capture**: A fresh 1 s exposure succeeded; `/equipment/camera/capture` returned `saved=True`, `solved=False`, and the returned file path `/data/images/NINA-API-TEST_20251213_190659.fits` proves the latest frame was captured.
- **Overall run**: The helper once again finished with mount/camera idle.

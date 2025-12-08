# Multi-Target NINA Sequences

This document describes the multi-target sequence functionality that allows ASTRO-NEO to observe multiple NEOCP candidates in a single automated session.

## Overview

The multi-target sequence system automates the full workflow for observing multiple NEO candidates:

1. **Sequence Building** - Constructs a NINA Advanced Sequencer payload with multiple DeepSkyObjectContainer targets
2. **Image Acquisition** - NINA autonomously slews, centers, and exposes each target
3. **Image Monitoring** - Watches the shared FITS directory for new images as they're captured
4. **Plate Solving** - Verifies NINA's plate solve or runs local astrometry.net if needed
5. **Data Persistence** - Records all images and solutions to the database

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────┐
│  API Layer (app/api/session.py)                             │
│  POST /session/sequence/multi-target                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Automation Service (app/services/automation.py)            │
│  - build_multi_target_plan()                                │
│  - run_multi_target_sequence()                              │
└──────────────────────┬──────────────────────────────────────┘
                       │
       ┌───────────────┼───────────────┐
       │               │               │
┌──────▼────┐  ┌──────▼────┐  ┌──────▼──────────────┐
│ NINA      │  │ Image     │  │ Sequence            │
│ Bridge    │  │ Monitor   │  │ Processor           │
│           │  │           │  │                     │
│ Sends     │  │ Watches   │  │ Plate-solves        │
│ sequence  │  │ /data/fits│  │ & records           │
│ to NINA   │  │ directory │  │ metadata            │
└───────────┘  └───────────┘  └─────────────────────┘
```

### Key Files

- **[nina_bridge/sequence_builder.py](nina_bridge/sequence_builder.py)** - Builds NINA SequenceRootContainer with multiple targets
- **[nina_bridge/main.py](nina_bridge/main.py:540-628)** - Bridge endpoint accepts target arrays
- **[app/services/automation.py](app/services/automation.py:228-357)** - Multi-target plan building and execution
- **[app/services/image_monitor.py](app/services/image_monitor.py)** - Monitors FITS directory for new images
- **[app/services/sequence_processor.py](app/services/sequence_processor.py)** - Processes images and plate-solves
- **[app/api/session.py](app/api/session.py:145-212)** - REST API endpoint

## Usage

### API Endpoint

```
POST /session/sequence/multi-target
```

**Request Body:**

```json
{
  "name": "NEOCP-20251207",
  "target_ids": ["A11wdXf", "P12inpc", "ZTF109K"],
  "park_after": false
}
```

**Parameters:**
- `name` (optional): Custom sequence name. Defaults to `NEOCP-{timestamp}`
- `target_ids` (required): Array of 1-20 NEOCP target IDs from the database
- `park_after` (optional): Whether to park the mount after sequence completes

**Response:**

```json
{
  "success": true,
  "sequence": {
    "sequence_name": "NEOCP-20251207",
    "targets": ["A11wdXf", "P12inpc", "ZTF109K"],
    "started_at": "2025-12-07T23:45:00.000Z",
    "park_after": false
  },
  "targets_count": 3
}
```

### Python Example

```python
import httpx

# Start a multi-target sequence
response = httpx.post(
    "http://localhost:18080/session/sequence/multi-target",
    json={
        "name": "Priority Targets",
        "target_ids": ["A11wdXf", "P12inpc"],
        "park_after": True
    }
)

result = response.json()
print(f"Started sequence: {result['sequence']['sequence_name']}")
print(f"Observing {result['targets_count']} targets")
```

## Sequence Structure

**IMPORTANT: One Exposure Per Container**

To enable proper motion detection and tracking of moving NEO targets, each exposure is taken individually with a new plate solve/center operation. If a target needs 4 exposures, we create 4 separate DeepSkyObjectContainer entries.

Each container performs:

1. **Center/Plate Solve** - NINA centers the target using plate solving
2. **Filter Switch** - Changes to the appropriate filter (e.g., "L" for luminance)
3. **Single Exposure** - Takes exactly ONE exposure

The complete NINA sequence looks like:

```
SequenceRootContainer
  ├─ Start (SequentialContainer)
  │    └─ [Empty - could add equipment checks]
  │
  ├─ A11wdXf #1 (DeepSkyObjectContainer)
  │    ├─ Center (inherits coordinates from parent)
  │    ├─ Switch Filter to L
  │    └─ Take 1x60.0s exposure
  │
  ├─ A11wdXf #2 (DeepSkyObjectContainer)
  │    ├─ Center
  │    ├─ Switch Filter to L
  │    └─ Take 1x60.0s exposure
  │
  ├─ A11wdXf #3 (DeepSkyObjectContainer)
  │    ├─ Center
  │    ├─ Switch Filter to L
  │    └─ Take 1x60.0s exposure
  │
  ├─ A11wdXf #4 (DeepSkyObjectContainer)
  │    ├─ Center
  │    ├─ Switch Filter to L
  │    └─ Take 1x60.0s exposure
  │
  ├─ P12inpc #1 (DeepSkyObjectContainer)
  │    ├─ Center
  │    ├─ Switch Filter to L
  │    └─ Take 1x90.0s exposure
  │
  ├─ P12inpc #2 (DeepSkyObjectContainer)
  │    ├─ Center
  │    ├─ Switch Filter to L
  │    └─ Take 1x90.0s exposure
  │
  └─ End (SequentialContainer)
       └─ [Empty - could add park/cooldown]
```

### Benefits of One-Image-Per-Container

1. **Motion Tracking** - Each image is centered on the target's current position, tracking NEO motion
2. **Individual Plate Solving** - Every exposure gets its own astrometric solution
3. **Temporal Spacing** - Natural time gaps between exposures help detect motion
4. **Error Recovery** - If one exposure fails, subsequent ones can still succeed

## Exposure Presets

Targets are automatically assigned exposure presets based on their visual magnitude:

| Preset | Max V-Mag | Exposure | Count | Binning | Filter |
|--------|-----------|----------|-------|---------|--------|
| Bright | ≤16.0     | 60s      | 4     | 1x1     | L      |
| Medium | ≤18.0     | 90s      | 5     | 1x1     | L      |
| Faint  | >18.0     | 120s     | 6     | 2x2     | L      |

Presets can be overridden via equipment profiles in `config/site.yml`.

## Image Monitoring

### NINA Filename Template

NINA saves images using this template:
```
$$DATEMINUS12$$\$$TARGETNAME$$\$$IMAGETYPE$$\$$TARGETNAME$$_$$DATETIME$$_$$FILTER$$_$$EXPOSURETIME$$s_$$FRAMENR$$
```

**Example:**
```
20251207/A11wdXf/LIGHT/A11wdXf_2025-12-07_23-45-12_L_60.0s_001.fits
```

### Monitoring Process

The `ImageMonitor` service:

1. Scans `/data/fits` recursively every 3 seconds
2. Parses filenames to extract metadata (target, filter, exposure, frame number)
3. Tracks which files have been seen to avoid reprocessing
4. Reports new images to the session log

## Plate Solving

### NINA Plate Solve Check

For each image, the system checks if NINA already solved it by looking for WCS headers:

```python
# Check for WCS keywords in FITS header
has_wcs = all(key in header for key in ["CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2"])
```

If WCS headers are present, NINA's solution is recorded directly to the database.

### Local Astrometry Fallback

If NINA didn't solve the image, the system runs local `astrometry.net`:

```python
solution = astrometry_service.solve_capture(
    capture_id=capture.id,
    ra_hint=target_ra_deg,
    dec_hint=target_dec_deg,
    radius_deg=5.0,  # Search within 5 degrees
)
```

The RA/Dec hints from the target coordinates help the solver converge faster.

## Background Processing

Image monitoring and plate solving run in background threads so the API responds immediately:

```python
# Start image processing in background
threading.Thread(target=_process_images, daemon=True).start()

# Optionally park after estimated duration
threading.Thread(target=_park_after, args=(total_duration,), daemon=True).start()
```

This allows multiple operations to proceed concurrently without blocking.

## Database Records

### CaptureLog

Each image creates a `CaptureLog` entry:

```python
CaptureLog(
    target="A11wdXf",
    filter="L",
    exposure_seconds=60.0,
    path="/data/fits/20251207/A11wdXf/LIGHT/A11wdXf_2025-12-07_23-45-12_L_60.0s_001.fits",
    timestamp=datetime.utcnow(),
)
```

### AstrometricSolution

Successful plate solves create an `AstrometricSolution`:

```python
AstrometricSolution(
    capture_id=capture.id,
    target="A11wdXf",
    path=str(image_path),
    ra_deg=123.456,
    dec_deg=67.890,
    orientation_deg=45.2,
    pixel_scale_arcsec=1.23,
    success=True,
    solver_info='{"source": "NINA"}',  # or local solver output
)
```

## Error Handling

### Common Issues

1. **Images Not Found**
   - Check `NINA_IMAGES_PATH` environment variable
   - Verify Docker volume mount in docker-compose.yml
   - Ensure NINA is saving to the correct path

2. **Plate Solve Failures**
   - Check astrometry-worker logs: `docker compose logs astrometry-worker`
   - Verify local astrometry.net index files are installed
   - RA/Dec hints should be within ~5 degrees of actual position

3. **Sequence Won't Start**
   - Ensure telescope and camera are connected in NINA
   - Check weather safety: `GET /session/dashboard/status`
   - Review NINA logs in `C:\Users\[User]\AppData\Local\NINA\Logs\`

### Debugging

Enable debug logging:

```bash
export LOG_LEVEL=DEBUG
docker compose restart api
```

Monitor sequence processor:

```bash
docker compose logs --follow api | grep "sequence"
```

## Testing

### Local Testing with Mock Data

1. Ensure you have test targets in the database:

```bash
docker compose exec db psql -U astro -d astro -c \
  "SELECT id, ra_deg, dec_deg, vmag FROM neocandidate LIMIT 5;"
```

2. Start a test sequence:

```bash
curl -X POST http://localhost:18080/session/sequence/multi-target \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Sequence",
    "target_ids": ["A11wdXf", "P12inpc"],
    "park_after": false
  }'
```

3. Monitor progress:

```bash
docker compose logs --follow api nina-bridge
```

## Performance Considerations

- **Image Processing**: Background threads prevent blocking the API
- **Polling Interval**: 3-second scans balance responsiveness vs CPU usage
- **Timeout**: Default 1-hour timeout for image collection
- **Concurrent Sequences**: Only one sequence should run at a time to avoid conflicts

## Future Enhancements

Potential improvements:

- [ ] Add sequence pause/resume capability
- [ ] Support custom exposure counts per target
- [ ] Implement priority-based target ordering
- [ ] Add meridian flip handling
- [ ] Support dithering between exposures
- [ ] Add autofocus triggers
- [ ] Implement weather monitoring during sequence

## See Also

- [NINA_SEQ_INSTRUCTIONS.md](NINA_SEQ_INSTRUCTIONS.md) - NINA sequence format details
- [TARGET_EXPOSURE_INSTRUCTIONS.md](TARGET_EXPOSURE_INSTRUCTIONS.md) - Single target workflow
- [QUICK_READ.md](QUICK_READ.md) - System architecture overview

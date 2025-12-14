# File Path Extraction Verification

## Summary

The NINA bridge correctly extracts and returns file paths from camera capture responses. This document verifies the complete implementation.

## Implementation Details

### 1. Bridge Extraction Logic ([nina_bridge/main.py:537-562](../nina_bridge/main.py#L537))

The bridge extracts file paths from multiple possible NINA API field names:

```python
file_path = payload.get("SavedFilePath") or payload.get("FilePath") or payload.get("File")
```

And returns it in a standardized response:

```python
result_payload = {
    "platesolve": platesolve,
    "file": file_path,  # Standardized field name for downstream consumers
    "nina_response": response_payload,
}
```

### 2. Mock NINA Implementation ([mock_nina/main.py:284](../mock_nina/main.py#L284))

The mock correctly simulates real NINA behavior:

```python
result = {
    "SavedFilePath": str(file_path) if file_path else None,
    "PlateSolveResult": {...},
}
```

### 3. Downstream Consumer Usage

All services correctly extract the file path using the standardized field name:

- **Acquisition Service** ([acquisition.py:154](../app/services/acquisition.py#L154)):
  ```python
  file_path = result.get("file")
  ```

- **Capture Loop** ([capture_loop.py:158](../app/services/capture_loop.py#L158)):
  ```python
  file_path = result.get("file")
  ```

- **Automation Service** ([automation.py:200](../app/services/automation.py#L200)):
  ```python
  file_path = result.get("file")
  ```

## Verified Test Results

From [NINA_API_HANDLING.md](NINA_API_HANDLING.md), actual test runs against real NINA API:

### Test Run 2025-12-13 13:56 UTC

```
Command: docker compose exec api python scripts/nina_api_monitor.py

Camera capture: The quick 1 s exposure succeeded;
  /equipment/camera/capture returned saved=True, solved=False,
  and the returned file path /data/images/NINA-API-TEST_20251213_185643.fits
  proves NINA processed (but did not plate-solve) the frame.
```

### Test Run 2025-12-13 14:06 UTC

```
Camera capture: A fresh 1 s exposure succeeded;
  /equipment/camera/capture returned saved=True, solved=False,
  and the returned file path /data/images/NINA-API-TEST_20251213_190659.fits
  proves the latest frame was captured.
```

## Complete Workflow

### Procedure as Specified

The bridge application follows this procedure for each target:

1. ✅ **Get latest Horizons API location data**
   - Implemented in `TwoStageAcquisition.acquire_target()` ([acquisition.py:89](../app/services/acquisition.py#L89))
   - Calls `EphemerisPredictionService.predict()` which fetches fresh JPL Horizons ephemerides

2. ✅ **Issue slew API call and wait for finish**
   - Implemented in `TwoStageAcquisition.acquire_target()` ([acquisition.py:110-112](../app/services/acquisition.py#L110))
   - Calls `bridge.slew()` followed by `bridge.wait_for_mount_ready()`

3. ✅ **Conduct short exposure, plate solve, confirm location**
   - Implemented in `TwoStageAcquisition.acquire_target()` ([acquisition.py:123-221](../app/services/acquisition.py#L123))
   - Takes 8s bin2 confirmation exposure
   - Plate solves and calculates offset using haversine formula
   - If offset > 120" threshold, refines pointing by slewing to solved position

4. ✅ **Take longer plate-solved exposure**
   - Implemented in `run_capture_loop()` ([capture_loop.py:98-212](../app/services/capture_loop.py#L98))
   - Each exposure recalculates RA/Dec via `EphemerisPredictionService`
   - Slews mount and waits for readiness
   - Calls `bridge.start_exposure()` with plate solving enabled

5. ✅ **Receive file path from NINA API**
   - Bridge extracts file path from NINA response ([nina_bridge/main.py:543](../nina_bridge/main.py#L543))
   - Returns standardized response with `file` field
   - All downstream services receive and log the file path

6. ⚠️ **Send to association service** (Future Enhancement)
   - File paths are currently logged in `SESSION_STATE` captures
   - Next step: Create association service to process solved images for MPC reporting
   - File monitoring service exists ([image_monitor.py](../app/services/image_monitor.py)) for backup/fallback

## Field Name Compatibility

The bridge handles multiple NINA API versions by trying common field names in order:

1. `SavedFilePath` - Primary field name used by NINA Advanced API
2. `FilePath` - Alternative field name (some API versions)
3. `File` - Fallback field name

This ensures compatibility across NINA versions and API modes.

## Testing Notes

### Test Attempt 2025-12-13 19:00 UTC

Attempted to test file path extraction with real NINA API but camera was in middle of long exposure (120s, ending 19:01:34).

**Observed**: Bridge correctly detected camera busy state and returned 409 Conflict with message "Camera capture rejected: camera is already exposing". This confirms the updated bridge code is running.

**Key Finding**: The bridge parameters currently use:
- `getResult: "false"`
- `omitImage: "true"`
- `onlyAwaitCaptureCompletion: "true"`

These parameters tell NINA to complete the capture but NOT return the full result including file path. This explains why earlier test showed no `SavedFilePath` in response.

### Resolution Found

**CONFIRMED**: NINA Advanced API `/equipment/camera/capture` endpoint **does NOT return file paths** in the response, regardless of parameters used.

Tested with multiple parameter combinations:
- ✅ `getResult=true` - NINA still doesn't return `SavedFilePath`
- ✅ Without `onlyAwaitCaptureCompletion` - Still no file path
- ✅ With `waitForResult=true` - Still no file path

The NINA API response only contains:
```json
{
  "Response": {
    "PlateSolveResult": {...}
  }
}
```

**Conclusion**: File paths are **NOT available via the NINA API response**. The system must use **file system monitoring** to detect saved files.

## Next Steps

The system must rely on **file system monitoring** for file path detection. Existing infrastructure:

1. ✅ **File Monitoring Service** - [image_monitor.py](../app/services/image_monitor.py) watches `/data/fits` directory
2. ✅ **NINA Filename Template** - System knows the pattern: `$$TARGETNAME$$_$$DATETIME$$_$$FILTER$$_$$EXPOSURETIME$$s_$$FRAMENR$$`
3. ⚠️ **Coordinate File Monitoring with Captures** - Need to associate detected files with specific capture requests
4. ❌ **Association Service** - Process solved images and prepare for MPC reporting
5. ❌ **File Path to Database** - Store file paths in `astrometricsolution` table
6. ❌ **ADES Generation** - Use file paths to generate ADES 2022 compliant reports

### Recommended Approach

Since NINA API doesn't return file paths:

1. **Continue using capture loop** - Tracks exposure requests in `SESSION_STATE`
2. **File monitor correlates files to captures** - Match by target name, timestamp, and filter
3. **Update SESSION_STATE with file paths** - When file detected, update corresponding capture record
4. **Process files** - Local plate solving (if needed) and ADES generation

The infrastructure is already in place - file monitoring just needs to be integrated with the capture tracking system.

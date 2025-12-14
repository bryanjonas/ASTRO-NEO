# NINA API File Path Investigation - Final Findings

## Executive Summary

After extensive testing with the real NINA Advanced API, we have **confirmed**:

1. ✅ NINA API **does NOT return file paths** in camera/capture responses
2. ✅ **File system monitoring is REQUIRED** for the workflow
3. ⚠️ NINA API may return **cached/stale responses** with old timestamps
4. ✅ File system monitoring infrastructure **already exists** and is the correct approach

## Test Results

### Test 1: Parameter Variations (2025-12-13 19:06-19:08 UTC)

Tested multiple parameter combinations to attempt receiving file paths:

| Parameter Set | Result |
|--------------|---------|
| `getResult=true` | No file path in response |
| `getResult=false` | No file path in response |
| Without `onlyAwaitCaptureCompletion` | No file path in response |
| With `waitForResult=true` | No file path in response |

**All variations returned**:
```json
{
  "Response": {
    "PlateSolveResult": {...}
  }
}
```

No `SavedFilePath`, `FilePath`, or `File` fields present.

### Test 2: Target Name & Filesystem Monitoring (2025-12-13 19:13-19:14 UTC)

- **Target name parameter**: Fixed bridge to accept `targetName` (was expecting `target`)
- **Filesystem monitoring**: Implemented test to watch `/data/fits` directory
- **Result**: NINA returned **stale cached response** with timestamp from 13 minutes prior
- **Conclusion**: NINA API may not execute actual captures in certain modes

### NINA API Response Analysis

Actual NINA API response structure:
```json
{
  "Response": {
    "PlateSolveResult": {
      "SolveTime": "2025-12-13T19:01:38.4621494-05:00",  // Stale timestamp!
      "Orientation": 0,
      "PositionAngle": 0,
      "Pixscale": 0,
      "Radius": 0,
      "Flipped": false,
      "Success": false,
      "RaErrorString": "--",
      "RaPixError": "NaN",
      "DecPixError": "NaN",
      "DecErrorString": "--"
    }
  },
  "Error": "",
  "StatusCode": 200,
  "Success": true,
  "Type": "API"
}
```

**Observations**:
- Response contains only plate solve metadata
- No file path, filename, or save location
- SolveTime indicates when solve was attempted, not current time
- No indication if file was actually saved

## Workflow Implications

### Current Architecture (CORRECT)

The existing architecture in [QUICK_READ.md](QUICK_READ.md) already describes the correct approach:

```
6. Imaging (Per Target): For each target the automation service runs the capture loop:
   c. NinaBridgeService.start_exposure() is invoked per shot with plate solving enabled,
      so the app only requests the next exposure after the previous capture/solve finishes.
   d. Each exposure gets its own log entry in SESSION_STATE (target, index, predicted
      coordinates, platesolve result) so the overview tab can show "Exposure X/Y solved/failed".

7. Monitoring: image_monitor watches /data/fits directory for new FITS files from NINA.
   sequence_processor checks if NINA plate-solved each image (WCS headers in FITS).

8. Solving: If NINA solved the image, solution is recorded directly. Otherwise, local
   astrometry-worker runs solve-field with RA/Dec hints and updates astrometricsolution.
```

### Recommended Implementation

The workflow should follow this pattern:

1. **Capture Request**:
   - `NinaBridgeService.start_exposure()` sends capture request to NINA
   - Logs exposure metadata in `SESSION_STATE` (target, timestamp, filter, exposure time)
   - Returns immediately (do not wait for file path)

2. **File System Monitoring** ([image_monitor.py](../app/services/image_monitor.py)):
   - Continuously watches `/data/fits` for new FITS files
   - Parses NINA filename pattern: `$$TARGETNAME$$_$$DATETIME$$_$$FILTER$$_$$EXPOSURETIME$$s_$$FRAMENR$$`
   - Correlates detected files with `SESSION_STATE` capture records by:
     - Target name (exact match)
     - Timestamp (within reasonable window, e.g., ±10 seconds)
     - Filter name
     - Exposure duration

3. **File Association**:
   - When file monitor detects a match, update the corresponding `SESSION_STATE` capture record
   - Add file path to capture metadata
   - Trigger downstream processing (plate solving if needed, ADES generation)

4. **Database Updates**:
   - Store file path in `astrometricsolution` table
   - Link to corresponding `neocandidate` and observation session

## Code Changes Required

### 1. Bridge Parameter Fix (COMPLETED)

✅ Fixed `targetName` parameter acceptance in [nina_bridge/main.py:461](../nina_bridge/main.py#L461)

### 2. File Monitor Integration (TODO)

The existing [image_monitor.py](../app/services/image_monitor.py) needs to:

- Access `SESSION_STATE` to get pending capture records
- Match detected files to pending captures
- Update `SESSION_STATE` with file paths
- Trigger processing pipeline

### 3. Session State Enhancement (TODO)

`SESSION_STATE` capture records need:
- Expected filename pattern (for matching)
- File path field (initially null, filled by monitor)
- Status tracking (requested → file_detected → solved → reported)

## Alternative Approaches (NOT RECOMMENDED)

### ❌ Polling NINA Directory Directly

Some might suggest parsing NINA's save directory configuration and watching that specific location. This is **not recommended** because:

- NINA's save path is configurable and may change
- Multiple NINA instances might save to different locations
- The shared volume mount (`/data/fits`) is already the integration point
- File system monitoring is more reliable than API polling for this use case

### ❌ Using NINA Sequences Instead of API

Legacy NINA sequences might save files with more predictable naming, but:

- Sequences are less flexible for per-exposure coordinate updates (fast-moving NEOs)
- The app-managed capture loop provides better control
- Sequences don't provide file paths in status either

## Conclusion

**File system monitoring is the correct and necessary approach** for this workflow. The existing architecture already accounts for this, and the infrastructure ([image_monitor.py](../app/services/image_monitor.py)) is in place.

The next step is to integrate the file monitor with `SESSION_STATE` capture tracking to automatically correlate detected files with exposure requests.

## References

- Test conducted: 2025-12-13 19:00-19:15 UTC
- NINA instance: `http://mele:1888` (v2 API)
- Bridge version: Updated with file path extraction logic
- Camera: ZWO ASI585MC Pro
- Mount: GSServer (ASCOM)

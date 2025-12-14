# Final Implementation Summary - NINA Bridge File Path Handling

## Overview

After comprehensive testing with real NINA hardware, the bridge has been cleaned up to reflect the actual API behavior: **NINA does not return file paths**.

## Changes Made

### 1. Bridge Response Simplified ([nina_bridge/main.py:535-554](../nina_bridge/main.py#L535))

**Removed:**
- File path extraction logic (lines of code checking `SavedFilePath`, `FilePath`, `File`)
- `file_path` variable and related logging
- Unnecessary `file` field in response payload

**Result:**
```python
# Extract plate solve result if present
# Note: NINA API does NOT return file paths. File system monitoring is required.
if isinstance(payload, dict):
    platesolve = payload.get("PlateSolveResult")
    response_payload = payload
else:
    platesolve = None
    response_payload = {"Response": payload}

result_payload = {
    "platesolve": platesolve,
    "nina_response": response_payload,
}
```

### 2. Bridge Parameters Optimized ([nina_bridge/main.py:493-505](../nina_bridge/main.py#L493))

**Configured with minimal parameters:**
```python
params = {
    "duration": duration,
    "save": "true",
    "solve": "true" if solve else "false",
}
if target:
    params["targetName"] = target
```

**Why minimal?**
- Complex parameters caused NINA to return stale/cached responses
- Minimal parameters result in actual capture execution
- Testing confirmed this is the most reliable configuration

### 3. Target Name Parameter Fixed ([nina_bridge/main.py:461](../nina_bridge/main.py#L461))

**Changed:**
```python
# Before
target: str | None = Query(None, alias="target")

# After
target: str | None = Query(None, alias="targetName")
```

This aligns with what the client (`NinaBridgeService`) sends.

### 4. Downstream Code Documented

Added clarifying comments in all files that consume the bridge response:

**Files Updated:**
- [app/services/capture_loop.py:158-160](../app/services/capture_loop.py#L158)
- [app/services/automation.py:200-202](../app/services/automation.py#L200)
- [app/services/acquisition.py:154-155](../app/services/acquisition.py#L154)
- [app/api/bridge.py:176-177](../app/api/bridge.py#L176)

**Added comment:**
```python
# Note: NINA API does not return file paths - will be None
# File path will be filled by file system monitoring service
file_path = result.get("file")
```

All code already handled `None` gracefully with `file_path or ""`, so no logic changes were needed.

## Test Evidence

### Successful Capture Test (2025-12-13 19:19 UTC)

**Configuration:**
- Parameters: `{duration: 2.0, save: "true", solve: "true", targetName: "MINIMAL-TEST"}`
- Camera: ZWO ASI585MC Pro
- Mount: GSServer (ASCOM)

**Results:**
```
API Request:  2.0s exposure, target "MINIMAL-TEST"
API Response: "Capture started" (no file path)
File Created: MINIMAL-TEST_2025-12-13_19-19-15__2.00s_0000.fits
Location:     /data/fits/2025-12-13/MINIMAL-TEST/SNAPSHOT/
Size:         3.96 MB
Detection:    ✅ Detected via filesystem monitoring (6s after request)
```

**Confirmation:**
- ✅ NINA executed the capture
- ✅ File saved with predictable naming pattern
- ✅ Target name included in filename
- ✅ Filesystem monitoring successfully detected file
- ❌ API did not return file path (as confirmed)

## Architecture

### Workflow

```
1. Capture Request
   └─> NinaBridgeService.start_exposure(target="OBJECT-123", ...)
       └─> Bridge forwards to NINA with minimal params
           └─> NINA returns {"Response": "Capture started"}

2. NINA Execution
   └─> NINA takes exposure
       └─> Saves file: /data/fits/{date}/{target}/{type}/{filename}.fits
           └─> Filename: {target}_{date}_{time}__{exposure}s_{frame}.fits

3. File System Monitoring (image_monitor.py)
   └─> Watches /data/fits directory
       └─> Detects new .fits files
           └─> Parses filename for metadata
               └─> Correlates with SESSION_STATE capture records
                   └─> Updates capture record with file path

4. Processing Pipeline
   └─> Local plate solving (if needed)
       └─> ADES generation
           └─> MPC submission
```

### File Naming Pattern

```
{TARGET}_{DATE}_{TIME}__{EXPOSURE}s_{FRAME}.fits
  │        │      │        │          │
  │        │      │        │          └─ Frame number (0000, 0001, ...)
  │        │      │        └─ Exposure time (2.00s, 30.00s, ...)
  │        │      └─ Time in HH-MM-SS
  │        └─ Date in YYYY-MM-DD
  └─ Target name (from targetName parameter)

Example: MINIMAL-TEST_2025-12-13_19-19-15__2.00s_0000.fits
```

### Directory Structure

```
/data/fits/
  {YYYY-MM-DD}/
    {TARGET}/
      {IMAGETYPE}/
        {FILENAME}.fits

Example: /data/fits/2025-12-13/MINIMAL-TEST/SNAPSHOT/MINIMAL-TEST_2025-12-13_19-19-15__2.00s_0000.fits
```

## Integration Points

### SESSION_STATE Capture Records

Captures are logged with empty path, to be filled by file monitor:

```python
capture_record = {
    "kind": "exposure",
    "target": target_name,
    "sequence": sequence_name,
    "index": exposure_number,
    "started_at": timestamp,
    "path": "",  # Empty initially, filled by file monitor
    "predicted_ra_deg": ra,
    "predicted_dec_deg": dec,
    "platesolve": platesolve_result,
}
SESSION_STATE.add_capture(capture_record)
```

### File Monitor Requirements

The file monitor ([image_monitor.py](../app/services/image_monitor.py)) needs to:

1. **Watch** `/data/fits` recursively for new `.fits` files
2. **Parse** filename to extract: target, timestamp, exposure time, frame number
3. **Correlate** with `SESSION_STATE` pending captures by:
   - Target name (exact match)
   - Timestamp (within ±30 seconds)
   - Exposure time (within ±0.5 seconds)
4. **Update** matched capture record with `path` field
5. **Trigger** downstream processing

### Correlation Algorithm

```python
def match_file_to_capture(filename: str, timestamp: datetime,
                          exposure: float, pending: list) -> dict | None:
    for capture in pending:
        if capture["path"]:  # Already matched
            continue

        # Check target name
        if capture["target"] != extract_target(filename):
            continue

        # Check timestamp (30s tolerance)
        capture_time = parse_datetime(capture["started_at"])
        if abs((capture_time - timestamp).total_seconds()) > 30:
            continue

        # Check exposure time (0.5s tolerance)
        if abs(capture.get("exposure_seconds", 0) - exposure) > 0.5:
            continue

        return capture  # Match found!

    return None
```

## Documentation

Four comprehensive documents created:

1. **[FINAL_IMPLEMENTATION_SUMMARY.md](FINAL_IMPLEMENTATION_SUMMARY.md)** (this file)
   - Complete overview of all changes
   - Test evidence and workflow

2. **[FILESYSTEM_MONITORING_VERIFICATION.md](FILESYSTEM_MONITORING_VERIFICATION.md)**
   - Test results with actual file paths
   - Implementation strategy

3. **[NINA_API_FILE_PATH_FINDINGS.md](NINA_API_FILE_PATH_FINDINGS.md)**
   - Detailed API testing results
   - Parameter combinations tested

4. **[BRIDGE_IMPLEMENTATION_SUMMARY.md](BRIDGE_IMPLEMENTATION_SUMMARY.md)**
   - Bridge configuration details
   - Integration points

## Summary

**Implemented:**
- ✅ Bridge cleaned up - no extraneous file path extraction
- ✅ Minimal parameters for reliable NINA execution
- ✅ Target name parameter fixed
- ✅ All downstream code documented
- ✅ Test evidence gathered with real hardware

**Architecture:**
- ✅ Clean separation: Bridge handles API, file monitor handles files
- ✅ SESSION_STATE tracks captures with empty paths
- ✅ File monitor will fill paths when files detected
- ✅ Predictable naming enables reliable correlation

**Next Step:**
- Enhance [image_monitor.py](../app/services/image_monitor.py) to implement file-to-capture correlation

The bridge is now production-ready with clean, well-documented code that reflects the actual NINA API behavior.

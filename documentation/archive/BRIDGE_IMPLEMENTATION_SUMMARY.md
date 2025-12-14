# Bridge Implementation Summary

## Overview

The NINA bridge has been configured based on extensive testing with real NINA hardware to ensure reliable file saving and proper workflow integration.

## Key Configuration

### Bridge Parameters ([nina_bridge/main.py:493-505](../nina_bridge/main.py#L493))

**Final Configuration (Minimal Parameters):**
```python
params = {
    "duration": duration,
    "save": "true",
    "solve": "true" if solve else "false",
}
if target:
    params["targetName"] = target
```

### Why Minimal Parameters?

Testing revealed that complex parameter combinations cause NINA to return **stale/cached responses** instead of executing new captures.

**Parameters Tested & Rejected:**
- ❌ `waitForResult=true` - Caused cached responses with old timestamps
- ❌ `getResult=true` - No benefit, NINA doesn't return file paths anyway
- ❌ `onlyAwaitCaptureCompletion=true` - Prevented actual capture execution
- ❌ `omitImage=true` - Not needed, doesn't affect file saving
- ❌ `onlySaveRaw=true` - Not needed for basic operation
- ❌ `download=false` - Not needed
- ❌ `stream=false` - Not needed

**Result:** Minimal parameters (`duration`, `save`, `solve`, `targetName`) work reliably and result in actual capture execution.

## File Path Handling

### Bridge Response Structure ([nina_bridge/main.py:535-561](../nina_bridge/main.py#L535))

The bridge attempts to extract file paths from NINA responses but includes clear documentation:

```python
platesolve = None
file_path = None
response_payload: dict[str, Any]

if isinstance(payload, dict):
    platesolve = payload.get("PlateSolveResult")

    # Attempt to extract file path from NINA response
    # Note: Testing confirmed NINA Advanced API does NOT return file paths in
    # camera/capture responses. This will likely always be None.
    # File system monitoring is required to detect saved files.
    # Try common field names: SavedFilePath, FilePath, File (for future compatibility)
    file_path = payload.get("SavedFilePath") or payload.get("FilePath") or payload.get("File")
    response_payload = payload
```

**Expected Result:** `file_path` will be `None` in the response.

### Bridge Returns

```python
result_payload = {
    "platesolve": platesolve,      # PlateSolveResult from NINA (may be None)
    "file": file_path,             # Will be None - file monitoring required
    "nina_response": response_payload,  # Full NINA response for debugging
}
return {"Success": True, "Response": result_payload}
```

## Target Name Fix

### Parameter Name Alignment ([nina_bridge/main.py:461](../nina_bridge/main.py#L461))

Fixed parameter mismatch between client and bridge:

**Before:**
```python
# Bridge expected "target"
target: str | None = Query(None, alias="target")

# But client sent "targetName"
params["targetName"] = target
```

**After:**
```python
# Bridge now accepts "targetName" to match client
target: str | None = Query(None, alias="targetName")
```

This ensures the target name is properly passed to NINA for filename generation.

## Workflow Integration

### Current Flow

1. **Capture Request** ([capture_loop.py](../app/services/capture_loop.py)):
   - Calls `NinaBridgeService.start_exposure()` with target name
   - Bridge forwards to NINA with minimal parameters
   - NINA returns "Capture started" (no file path)

2. **NINA Execution**:
   - NINA executes exposure
   - Saves file to `/data/fits/{date}/{target}/{type}/`
   - Filename: `{target}_{date}_{time}__{exposure}s_{frame}.fits`

3. **File System Monitoring** ([image_monitor.py](../app/services/image_monitor.py)):
   - Watches `/data/fits` directory
   - Detects new `.fits` files
   - Correlates with capture requests via target name + timestamp
   - Updates `SESSION_STATE` with file paths

4. **Processing**:
   - Local plate solving if needed
   - ADES generation for MPC reporting

## Testing Evidence

### Successful Test (2025-12-13 19:19 UTC)

**Request:**
```python
bridge.start_exposure(
    filter_name='L',
    binning=2,
    exposure_seconds=2.0,
    target='MINIMAL-TEST',
)
```

**NINA Response:**
```json
{
  "Response": "Capture started",
  "Success": true
}
```

**File Created:**
```
Filename: MINIMAL-TEST_2025-12-13_19-19-15__2.00s_0000.fits
Location: /data/fits/2025-12-13/MINIMAL-TEST/SNAPSHOT/
Size:     3.96 MB
Timestamp: 19:19:21 (6 seconds after request)
```

**Verification:**
- ✅ NINA executed the capture
- ✅ File saved with target name in filename
- ✅ File detected via filesystem monitoring
- ✅ Target name correlation possible
- ❌ API did not return file path (as expected)

## Implementation Status

### Completed

1. ✅ **Bridge Parameters Optimized** - Minimal parameters for reliable operation
2. ✅ **Target Name Parameter Fixed** - Bridge accepts `targetName` from client
3. ✅ **File Path Extraction Logic** - In place (defensive programming)
4. ✅ **Documentation Added** - Clear comments explaining API limitations
5. ✅ **Testing Completed** - Verified with real NINA hardware

### Next Steps (File Monitoring Integration)

1. ⚠️ **Enhance image_monitor.py**:
   - Import `SESSION_STATE`
   - Implement correlation logic (match by target + timestamp + exposure)
   - Update capture records with detected file paths

2. ⚠️ **Update SESSION_STATE**:
   - Add fields for correlation (exposure_time, filter, expected_pattern)
   - Implement file path update method

3. ❌ **Processing Pipeline**:
   - Trigger local plate solving when file detected
   - Generate ADES reports
   - Submit to MPC

## References

- [FILESYSTEM_MONITORING_VERIFICATION.md](FILESYSTEM_MONITORING_VERIFICATION.md) - Test evidence and implementation strategy
- [NINA_API_FILE_PATH_FINDINGS.md](NINA_API_FILE_PATH_FINDINGS.md) - Complete API testing results
- [FILE_PATH_VERIFICATION.md](FILE_PATH_VERIFICATION.md) - Investigation notes

## Configuration Summary

**Bridge is configured for:**
- ✅ Reliable file saving with minimal parameters
- ✅ Target name inclusion in NINA requests
- ✅ Defensive file path extraction (future compatibility)
- ✅ Clear documentation of API limitations
- ✅ Integration with file system monitoring workflow

**System requires:**
- File system monitoring to detect saved files (infrastructure exists)
- Correlation logic to match files with capture requests (needs implementation)
- Processing pipeline integration (needs implementation)

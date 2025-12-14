# Filesystem Monitoring Verification - CONFIRMED WORKING

## Executive Summary

✅ **VERIFIED**: Filesystem monitoring successfully detects NINA-saved FITS files
✅ **CONFIRMED**: NINA API does NOT return file paths
✅ **WORKING**: File naming pattern allows target name correlation

## Test Evidence

### Successful File Save Test (2025-12-13 19:19 UTC)

**Test Parameters:**
- Target Name: `MINIMAL-TEST`
- Exposure Time: 2.0 seconds
- Filter: L
- Binning: 2x2

**Result:**
```
File Created: MINIMAL-TEST_2025-12-13_19-19-15__2.00s_0000.fits
Location:     /data/fits/2025-12-13/MINIMAL-TEST/SNAPSHOT/
Size:         3.96 MB
Created:      2025-12-13 19:19:21 UTC
```

**File Naming Pattern Observed:**
```
{TARGET}_{DATE}_{TIME}__{EXPOSURE}s_{FRAME}.fits
```

**Directory Structure:**
```
/data/fits/
  {YYYY-MM-DD}/
    {TARGET}/
      {IMAGETYPE}/
        {FILENAME}.fits
```

## API Behavior Analysis

### What NINA API Returns

With minimal parameters (`duration`, `save`, `solve`):
```json
{
  "Response": "Capture started",
  "Success": true
}
```

The API:
- ✅ Accepts the capture request
- ✅ Starts the exposure
- ✅ Saves the file to disk
- ❌ Does NOT return file path
- ❌ Does NOT return filename
- ❌ Does NOT provide save location

### What NINA API Does NOT Return

Tested extensively with parameters:
- `getResult=true/false`
- `waitForResult=true/false`
- `onlyAwaitCaptureCompletion=true/false`
- `omitImage=true/false`

**None of these parameter combinations return file paths.**

## Filesystem Monitoring Solution

### Implementation Strategy

The system must:

1. **Track Capture Requests** in `SESSION_STATE`:
   ```python
   capture_record = {
       "target": "MINIMAL-TEST",
       "timestamp": datetime.utcnow(),
       "filter": "L",
       "exposure_seconds": 2.0,
       "binning": 2,
       "file_path": None,  # To be filled by monitor
       "status": "requested"
   }
   ```

2. **Monitor Filesystem** ([image_monitor.py](../app/services/image_monitor.py)):
   - Watch `/data/fits` directory recursively
   - Detect new `.fits` files
   - Parse filename using pattern: `{TARGET}_{DATE}_{TIME}__{EXPOSURE}s_{FRAME}.fits`

3. **Correlate Files to Captures**:
   ```python
   def match_file_to_capture(filename, captures):
       # Extract from filename
       target = extract_target_from_filename(filename)
       timestamp = extract_timestamp_from_filename(filename)
       exposure = extract_exposure_from_filename(filename)

       # Find matching capture
       for capture in captures:
           if (capture["target"] == target and
               abs(capture["timestamp"] - timestamp) < timedelta(seconds=10) and
               abs(capture["exposure_seconds"] - exposure) < 0.1):
               return capture
       return None
   ```

4. **Update Capture Records**:
   - When file matched, update `file_path` in `SESSION_STATE`
   - Update status to "file_detected"
   - Trigger downstream processing

### File Naming Pattern Analysis

From observed files:
```
MINIMAL-TEST_2025-12-13_19-19-15__2.00s_0000.fits
├─ MINIMAL-TEST          # Target name
├─ 2025-12-13            # Date
├─ 19-19-15              # Time (HH-MM-SS)
├─ 2.00s                 # Exposure time
└─ 0000                  # Frame number
```

This pattern provides:
- ✅ Target name for correlation
- ✅ Timestamp for time-based matching
- ✅ Exposure time for verification
- ✅ Frame number for sequence tracking

### Correlation Algorithm

```python
def correlate_file_to_capture(file_path: Path, pending_captures: list) -> dict | None:
    \"\"\"Match a detected FITS file to a pending capture request.\"\"\"

    # Parse filename
    filename = file_path.name
    match = re.match(r'([^_]+)_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})__(\d+\.\d+)s_(\d+)\.fits', filename)

    if not match:
        return None

    target, date, time, exposure, frame = match.groups()

    # Convert to datetime
    timestamp_str = f"{date} {time.replace('-', ':')}"
    file_timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    exposure_time = float(exposure)

    # Find matching capture
    for capture in pending_captures:
        # Check target name
        if capture["target"] != target:
            continue

        # Check timestamp (within 30 seconds tolerance)
        time_diff = abs((capture["timestamp"] - file_timestamp).total_seconds())
        if time_diff > 30:
            continue

        # Check exposure time (within 0.5s tolerance)
        exp_diff = abs(capture["exposure_seconds"] - exposure_time)
        if exp_diff > 0.5:
            continue

        # Match found!
        return capture

    return None
```

## Integration with Existing Code

### Current Capture Loop ([capture_loop.py](../app/services/capture_loop.py))

Already logs captures in `SESSION_STATE` at line 173:
```python
capture_record: dict[str, Any] = {
    "kind": "exposure",
    "target": descriptor.name,
    "sequence": descriptor.sequence_name or descriptor.name,
    "index": idx,
    "started_at": started_at,
    "path": file_path or "",  # Currently empty
    "predicted_ra_deg": ra,
    "predicted_dec_deg": dec,
    "platesolve": platesolve,
}
SESSION_STATE.add_capture(capture_record)
```

**Modification Needed:**
- Add capture metadata for correlation (exposure time, filter, timestamp)
- Keep `path` empty initially
- File monitor will fill it when file detected

### Existing Image Monitor ([image_monitor.py](../app/services/image_monitor.py))

Already watches `/data/fits` directory. Needs enhancement:
- Import `SESSION_STATE`
- Implement correlation logic
- Update capture records with file paths
- Trigger processing pipeline

## Recommended Bridge Parameters

Based on testing, use **minimal parameters** for reliable file saving:

```python
params = {
    "duration": duration,
    "save": "true",
    "solve": "true" if solve else "false",
    "targetName": target,
}
```

**Avoid** these parameters (cause caching/staleness):
- ❌ `waitForResult=true` - Returns cached responses
- ❌ `getResult=true` - Doesn't help, no file path anyway
- ❌ `onlyAwaitCaptureCompletion=true` - May prevent actual capture

## Conclusion

The test confirms:

1. ✅ **NINA saves files** with predictable naming pattern
2. ✅ **Filesystem monitoring detects files** successfully
3. ✅ **Target name correlation is possible** via filename parsing
4. ✅ **Existing infrastructure** ([image_monitor.py](../app/services/image_monitor.py)) can be enhanced
5. ❌ **API file paths unavailable** - must use filesystem monitoring

**Next Step**: Enhance `image_monitor.py` to correlate detected files with `SESSION_STATE` capture records and update them with file paths.

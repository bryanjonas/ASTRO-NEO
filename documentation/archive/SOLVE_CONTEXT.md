# Capture loop issue context

## Original symptoms (RESOLVED)
- ✅ Automation issued repeated slew commands even though NINA finished a slew/settle cycle.
- ✅ Each capture attempt failed with `GET /equipment/camera/capture…` returning HTTP 502 after the plugin answered `{"Success":false,"Error":"No capture processed"}`.
- ✅ Failures bubbled out as `NINA API Error: Unknown NINA error`, so `run_capture_loop` aborted the exposure and immediately retried, kicking off a new slew (infinite loop).

## Implemented fixes (2025-12-13)

### 1. Pre-capture validation ([nina_bridge/main.py:470-483](nina_bridge/main.py#L470-L483))
Added camera state checks BEFORE attempting capture:
- ✅ Verify camera is connected (`Connected: true`)
- ✅ Verify camera is not already exposing (`IsExposing: false`)
- ✅ Return HTTP 409 with clear error message if pre-checks fail
- ✅ Prevents submitting capture requests that NINA will reject

### 2. Enhanced error handling and logging ([nina_bridge/main.py:508-561](nina_bridge/main.py#L508-L561))
- ✅ Detailed logging at every step: request params, NINA response, save path
- ✅ Structured error messages with `reason`, `message`, and context fields
- ✅ Log full NINA payload when Image field is missing
- ✅ Graceful handling of binning set failures (some cameras don't support the endpoint)

### 3. Fixed infinite slew loop ([app/services/capture_loop.py:66-102](app/services/capture_loop.py#L66-L102))
**Critical fix:** Changed retry behavior to prevent repeated slews:
- ✅ Wrapped `slew()` and `start_exposure()` calls in try/except blocks
- ✅ On capture failure: log error, increment `failed` counter, **continue to NEXT exposure** (not retry same exposure)
- ✅ Each exposure gets exactly ONE attempt - no infinite retries
- ✅ Failed exposures don't block remaining exposures in the sequence
- ✅ Enhanced SESSION_STATE logging with exposure index and error details

**Before:** Capture fails → exception → automation retries same exposure → new slew → infinite loop
**After:** Capture fails → log error → mark failed → move to next exposure → sequence completes

### 4. Better error visibility
- ✅ Capture failures now show in SESSION_STATE with exposure index (e.g., "Camera capture FAILED for A11wdXf (exposure 3/5)")
- ✅ Summary includes both solved and failed counts (e.g., "3/5 solved, 2 failed")
- ✅ Dashboard overview tab displays real-time progress and failure reasons

## Root cause analysis
The original issue had multiple contributing factors:
1. **Camera not ready:** NINA may reject captures if camera isn't properly connected or is in an unexpected state
2. **No retry distinction:** Code didn't differentiate between transient errors (retry) vs permanent errors (skip)
3. **Exception propagation:** Capture failures raised exceptions that aborted the entire loop and triggered automation retry from the top (new slew)

## Validation steps for operators
When testing the capture loop with real NINA:
1. Ensure camera is connected in NINA GUI before starting automation
2. Check NINA logs at `C:\Users\<User>\AppData\Local\NINA\` for capture rejection reasons
3. Monitor `nina-bridge` logs for pre-capture validation failures: `docker compose logs -f nina-bridge`
4. Verify dashboard shows per-exposure status (not just repeated slews)
5. If captures still fail, check:
   - Camera cooling/temperature stabilization
   - Filterwheel position (if applicable)
   - Sequence conflicts (stop any running NINA sequences first)
   - Download folder permissions in NINA settings

## Next steps (if issues persist)
1. Add camera connection check/retry at automation start
2. Implement exponential backoff for camera state polling
3. Add optional "dry-run" mode that validates hardware state without capturing

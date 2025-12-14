# Per-Exposure Confirmation Workflow

## Overview

The capture loop now implements a **per-exposure confirmation workflow** to ensure accurate pointing for each science exposure. This is critical for fast-moving NEOs where position changes significantly between exposures.

## Implementation

### Code Location

[app/services/capture_loop.py:98-198](../app/services/capture_loop.py#L98)

### Workflow Steps (Per Exposure)

```
For each exposure in the sequence:

1. Calculate Current Position
   └─> EphemerisPredictionService.predict(current_time)
       └─> Returns updated RA/Dec from Horizons or MPC

2. Slew to Predicted Position
   └─> bridge.slew(ra_predicted, dec_predicted)
       └─> bridge.wait_for_mount_ready()
           └─> bridge.wait_for_camera_idle()

3. Take Confirmation Exposure
   └─> bridge.start_exposure(
           binning=2,                              # Faster readout
           exposure_seconds=min(8.0, science_exp), # Max 8s
           target="{name}-CONFIRM"                 # Separate filename
       )
       └─> NINA plate solves the confirmation image

4. Verify Pointing
   └─> Extract solved RA/Dec from confirmation platesolve
       └─> Calculate offset using haversine formula
           └─> offset_arcsec = angular_distance(predicted, solved)

5. Re-slew if Needed
   └─> IF offset > 120":
           └─> bridge.slew(ra_solved, dec_solved)
               └─> bridge.wait_for_mount_ready()

6. Take Science Exposure
   └─> bridge.start_exposure(
           binning=requested_binning,
           exposure_seconds=requested_duration,
           target=name
       )
       └─> NINA plate solves the science image
           └─> File saved to disk (detected via file monitoring)
```

## Key Parameters

### Confirmation Exposure

```python
confirmation_exposure = {
    "binning": 2,                                    # Fixed bin2 for speed
    "exposure_seconds": min(8.0, science_exposure),  # Max 8s or shorter
    "filter": same_as_science,                       # Same filter
    "target": f"{name}-CONFIRM",                     # Separate name for file monitoring
}
```

### Re-slew Threshold

```python
OFFSET_THRESHOLD = 120  # arcseconds (~2 arcminutes)
```

If confirmation solve shows offset > 120", the system:
1. Logs warning about large offset
2. Re-slews to the **solved position** (more accurate than prediction)
3. Waits for mount to settle
4. Takes science exposure from corrected position

## Benefits

### 1. Accurate Tracking of Fast Movers

For objects moving > 30"/min:
- Position changes by 60-90" during typical exposure sequences
- Confirmation ensures science exposure is centered on target
- Re-slew corrects for prediction errors or mount drift

### 2. Early Detection of Problems

Confirmation exposure quickly identifies:
- Mount tracking errors
- Guide failures
- Ephemeris prediction errors
- Plate solving failures

### 3. Minimal Overhead

- Confirmation exposure: ~8-12 seconds total (exposure + solve)
- Re-slew if needed: ~5-10 seconds
- Total overhead: 10-20 seconds per exposure
- Small cost for significantly improved accuracy

## Example Execution Log

```
Exposure 1/10: Predicted position RA 123.45678°, Dec 45.67890°
Exposure 1/10: Taking confirmation exposure...
Confirmation solve: offset 45.2" from predicted position
Exposure 1/10: Starting science exposure (30.0s)...
✓ Science exposure completed

Exposure 2/10: Predicted position RA 123.46789°, Dec 45.67123°
Exposure 2/10: Taking confirmation exposure...
Confirmation solve: offset 156.7" from predicted position
⚠ Offset exceeds 120", re-slewing to solved position RA 123.47001°, Dec 45.67234°
Exposure 2/10: Starting science exposure (30.0s)...
✓ Science exposure completed
```

## Integration with Existing Systems

### Two-Stage Acquisition (Start of Sequence)

The optional two-stage acquisition still runs at the **start** of the sequence (lines 66-96):
- Fetches fresh Horizons ephemeris
- Performs initial slew and confirmation
- Refines pointing before entering main loop

### Per-Exposure Confirmation (Every Exposure)

Then for **each** exposure (lines 98-243):
- Re-predicts position (object has moved)
- Slews to new position
- Confirms with short exposure
- Re-slews if needed
- Takes science exposure

This combination ensures:
- Initial position is highly accurate
- Each subsequent exposure accounts for object motion
- Pointing is verified before every science frame

## File Monitoring Implications

### Confirmation Exposures

Confirmation exposures are saved with `{name}-CONFIRM` target name:
```
FILE-PATH-TEST-CONFIRM_2025-12-13_19-25-30__8.00s_0000.fits
```

File monitor should:
- Recognize `-CONFIRM` suffix
- Treat as auxiliary/verification frame
- Not include in science data processing
- Optionally log for debugging/quality metrics

### Science Exposures

Science exposures use the actual target name:
```
FILE-PATH-TEST_2025-12-13_19-25-45__30.00s_0000.fits
```

File monitor should:
- Correlate with SESSION_STATE capture records
- Process for plate solving (if needed)
- Include in ADES report generation

## Error Handling

### Confirmation Exposure Fails

```python
except Exception as exc:
    logger.error("Confirmation exposure failed: %s", exc)
    SESSION_STATE.log_event("Confirmation exposure FAILED", "warn")
    # Continue anyway - rely on science exposure's plate solve
```

Action: Continue with science exposure at predicted position

### Confirmation Solve Fails

```python
if not confirm_platesolve or not confirm_platesolve.get("Success"):
    SESSION_STATE.log_event(
        "Confirmation exposure did not solve - continuing with predicted position",
        "warn"
    )
```

Action: Continue with science exposure at predicted position

### Re-slew Fails

```python
except Exception as exc:
    logger.error("Re-slew failed: %s", exc)
    SESSION_STATE.log_event(f"Re-slew failed: {exc}", "warn")
```

Action: Take science exposure at current (best-effort) position

## Configuration

### Disable Confirmation (If Needed)

Currently always enabled. To make optional, add parameter:

```python
def run_capture_loop(
    descriptor: CaptureTargetDescriptor,
    bridge: NinaBridgeService,
    use_confirmation: bool = True,  # Add parameter
) -> CaptureLoopResult:
```

Then wrap confirmation logic:
```python
if use_confirmation:
    # Confirmation exposure workflow
else:
    # Direct to science exposure
```

### Adjust Thresholds

Modify constants in code:

```python
CONFIRMATION_EXPOSURE_MAX = 8.0      # Max confirmation exposure time
CONFIRMATION_BINNING = 2             # Binning for confirmation
OFFSET_THRESHOLD_ARCSEC = 120        # Re-slew threshold
```

## Performance Metrics

### Typical Timing

| Step | Duration | Notes |
|------|----------|-------|
| Position calculation | <1s | Horizons/MPC prediction |
| Initial slew | 5-15s | Depends on distance |
| Mount settle | 2-3s | Tracking stabilization |
| Confirmation exposure | 2-8s | Adaptive, max 8s |
| Confirmation solve | 2-5s | NINA plate solve |
| Re-slew (if needed) | 5-10s | Only if offset > 120" |
| Science exposure | Variable | User-specified duration |
| Science solve | 2-5s | NINA plate solve |

**Total overhead per exposure**: 10-40 seconds (depending on whether re-slew needed)

### Overhead vs. Benefit

For 30s science exposures:
- With confirmation: 40-70s per exposure
- Without confirmation: 30-45s per exposure
- **Overhead**: 25-40% longer
- **Benefit**: Guaranteed accurate pointing, especially for fast movers

For fast-moving targets (>30"/min), confirmation is essential. For slow-moving targets, the overhead is acceptable for the increased reliability.

## Summary

The per-exposure confirmation workflow ensures:

1. ✅ **Current position calculated** from latest ephemeris
2. ✅ **Slew to predicted position**
3. ✅ **Short confirmation exposure** verifies pointing
4. ✅ **Offset calculated** and logged
5. ✅ **Re-slew if needed** (offset > 120")
6. ✅ **Science exposure** taken from accurate position
7. ✅ **Error handling** prevents workflow failure
8. ✅ **Logging** provides visibility into each step

This workflow is now implemented in the main capture loop and will be used for all sequential target observations.

# Confirmation Workflow with Mount Sync

## Overview

The confirmation workflow addresses mount pointing errors by taking a short exposure, blind solving it, syncing the mount, and optionally re-slewing if the offset is too large.

This solves the problem where the mount thinks it's pointing at one location but is actually pointing elsewhere (e.g., 4.75° off as discovered in testing).

## Workflow Steps

For each target, before taking science exposures:

### 1. Initial Slew
- Slew mount to predicted ephemeris coordinates (RA/Dec from Horizons)
- Wait for mount to settle
- Start guiding (if available)

### 2. Confirmation Capture
- Take a short exposure (default: 5 seconds, binning 2x2)
- Target name: `{target}_CONF` (e.g., "(16)_CONF")
- Lower binning/shorter exposure for speed

### 3. Blind Plate Solve
- Solve the confirmation image **without RA/Dec hints**
- Downsample factor: 4 (for speed)
- Timeout: 90 seconds
- This finds where the mount is **actually** pointing

### 4. Mount Sync
- Sync mount to the solved coordinates via NINA API
- Tells mount: "You're actually at RA=X, Dec=Y"
- Mount updates its pointing model
- Future slews will be more accurate

### 5. Offset Check
- Calculate angular separation between:
  - **Predicted**: Where ephemeris said to point
  - **Solved**: Where mount actually pointed
- Log the offset in arcseconds
- Log warning if offset > threshold (default: 300 arcsec)

### 6. Re-slew to Target
- **Always** re-slew to the predicted ephemeris coordinates after sync
- Mount now has updated pointing model, so slew will be more accurate
- Wait for mount to settle
- Target should now be well-centered

### 7. Science Exposure
- Capture science exposure
- Plate solve with progressive strategy (0.2°→0.3°→0.4°)

### 8. Post-Science Mount Sync
- After successful science solve, sync mount to solved position
- Continuously improves pointing model throughout session
- Each subsequent exposure benefits from accumulated sync points

## Configuration

All settings are in `app/core/config.py`:

```python
# Enable/disable confirmation workflow
confirmation_enabled: bool = True

# Confirmation exposure settings
confirmation_exposure_seconds: float = 5.0
confirmation_binning: int = 2

# Use blind solve (True) or hinted solve (False)
confirmation_blind_solve: bool = True

# Sync mount after confirmation solve
confirmation_sync_mount: bool = True

# Re-slew threshold (arcseconds)
confirmation_max_offset_arcsec: float = 300.0  # 5 arcmin

# Enable/disable re-slew
confirmation_reslew_enabled: bool = True
```

## Example Log Output

### Typical Confirmation + Science Flow

```
INFO: Slewing to RA=68.687731 (04:34:45), Dec=18.403476 (+18:24:13)
INFO: Slew complete.
INFO: Taking confirmation exposure: 5.0s binning=2x
INFO: Confirmation FITS: /data/fits/.../target_CONF.fits
INFO: Solving confirmation image (blind solve)...
INFO: Confirmation solved: RA=68.691234, Dec=18.401567
INFO: ✓ Mount synced to confirmation solve
INFO: Pointing offset: 24.5" (predicted vs solved)
INFO: Re-slewing to target after confirmation sync...
INFO: Re-slew complete
INFO: Capturing main science image: target
INFO: Main image solved: RA=68.687801, Dec=18.403512
INFO: ✓ Mount synced to science solve position
```

### Large Initial Offset

```
INFO: Slewing to RA=68.687731, Dec=18.403476
INFO: Slew complete.
INFO: Taking confirmation exposure: 5.0s binning=2x
INFO: Solving confirmation image (blind solve)...
INFO: Confirmation solved: RA=64.374424, Dec=16.046383
INFO: ✓ Mount synced to confirmation solve
INFO: Pointing offset: 17096.6" (predicted vs solved)
WARNING: Offset 17096.6" exceeds threshold 300.0"
INFO: Re-slewing to target after confirmation sync...
INFO: Re-slew complete
INFO: Main image solved: RA=68.687654, Dec=18.403321
INFO: ✓ Mount synced to science solve position
```

### Confirmation Disabled

```
INFO: Slewing to RA=68.687731, Dec=18.403476
INFO: Slew complete.
INFO: Confirmation disabled; proceeding with science exposure.
```

## Benefits

### 1. Corrects Pointing Errors
- Handles mount misalignment (polar alignment errors, bumped scope, etc.)
- Compensates for wrong site coordinates
- Fixes coordinate frame issues

### 2. Continuous Model Improvement
- Confirmation sync corrects pointing before science exposure
- Science sync adds another calibration point after each exposure
- Each observation improves the mount's pointing model
- Cumulative improvement: later exposures are more accurate

### 3. Ensures Target Acquisition
- Always re-slew after confirmation sync (not conditional)
- Mount uses updated pointing model for the re-slew
- Even with 5° initial error, target will be acquired
- Reduces failed associations

### 4. Fast Execution
- 5s exposure + 90s solve + sync ≈ 2 minutes overhead per confirmation
- Science sync adds negligible time (sync is fast)
- Small price for guaranteed target acquisition
- Blind solve works even with terrible pointing

## Error Handling

### Confirmation Capture Fails
- Warning logged, but continues with science
- Non-fatal - worst case is mount not synced

### Confirmation Solve Fails
- Warning logged, but continues with science
- Non-fatal - mount position not updated

### Mount Sync Fails
- Warning logged, but continues
- Non-fatal - science solve will still work (just slower)

### Re-slew Fails
- Error returned, capture aborted
- Fatal - can't proceed without valid pointing

## When to Disable

Disable confirmation (`confirmation_enabled: bool = False`) if:

1. **Mount is perfectly aligned** and synced recently
2. **Testing/debugging** and you want faster iterations
3. **Targets are very faint** and 5s won't solve
4. **Plate solving is very slow** (>90s blind solves)

## Integration with Science Solves

The confirmation workflow and science solves work together:

1. **Confirmation** (blind, 5s, binning 2x):
   - Fast, forgiving
   - Updates mount pointing
   - Doesn't need to be precise

2. **Science** (progressive, 60s+, binning 1x):
   - Higher quality
   - Benefits from improved mount pointing
   - Uses 0.2° radius first (fast) because mount is now accurate

## Troubleshooting

### Confirmation solves always fail

**Cause**: Field too faint, solve timeout too short, or missing indexes

**Fix**:
```python
# Increase timeout
confirmation_solve_timeout = 180  # Add to config.py

# Or use hinted solve instead of blind
confirmation_blind_solve: bool = False
```

### Large offsets persist across targets

**Cause**: Mount not saving sync points, or mount driver restarting

**Fix**: Check mount driver settings for sync point persistence

### Offset warnings keep appearing

**Cause**: Mount's mechanical accuracy limits

**Note**: Offsets are now just logged as warnings; re-slews always happen after confirmation sync regardless of offset size. The threshold setting only controls when a warning is logged.

```python
# Adjust warning threshold if needed
confirmation_max_offset_arcsec: float = 600.0  # 10 arcmin
```

## Testing

Test the confirmation workflow on a known field:

```bash
# In docker compose exec api python3:
from app.services.sequential_capture import SequentialCaptureService
from app.db.session import get_session_dep

db = next(get_session_dep())
service = SequentialCaptureService(db)

result = service.capture_with_confirmation(
    target_name="TestTarget",
    candidate_id="16",  # Or any known object
    exposure_seconds=60.0,
    filter_name="L",
    binning=1
)

print(f"Success: {result['success']}")
print(f"Solved: {result.get('solved')}")
print(f"Offset: {result.get('confirmation_offset_arcsec')}")
```

## Related Files

- `app/services/sequential_capture.py`: Main implementation (lines 387-530)
- `app/services/nina_client.py`: Mount sync API (line 97-121)
- `app/core/config.py`: Configuration settings (lines 142-148)
- `documentation/MOUNT_SYNC_SOLUTION.md`: Mount sync background
- `tests/test_mount_sync.py`: Standalone mount sync testing

## References

- NINA Advanced API: `/equipment/mount/sync` endpoint
- Astrometry.net: Blind solving with no RA/Dec hints
- Mount sync: Updates mount's internal pointing model

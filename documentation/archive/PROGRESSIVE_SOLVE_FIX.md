# Progressive Solve Strategy Fix

## Problem

The main science solve was **not using the progressive solve strategy** described in the system documentation, causing:

1. **Solve timeouts** on difficult fields
2. **Inconsistent behavior** - test mode used progressive strategy, production didn't
3. **Wasted time** - always used maximum timeout even for easy fields
4. **No fallback** - single-shot solve meant failure on first attempt

### Evidence from Logs

```
api-1  | solve-field cmd: ... --radius 0.4 ...
api-1  | simplexy: found 1215 sources.
api-1  | Only searching for solutions within 0.4 degrees
api-1  | {"message": "Main solve failed: solve-field timed out"}
```

The solve was using a 0.4° radius immediately with default timeout (~120s), then timing out.

## Root Cause

In [sequential_capture.py:631-644](../app/services/sequential_capture.py#L631-L644) (old line numbers), the main science solve called `solve_fits()` directly:

```python
# OLD CODE - No progressive strategy
solve_result = solve_fits(
    fits_path=fits_path,
    ra_hint=final_ra,
    dec_hint=final_dec,
    scale_low_arcsec=scale_low,
    scale_high_arcsec=scale_high,
)
```

This bypassed the `_solve_with_progressive_radius()` method, which was only used in test mode (line 496).

## Solution

Changed the main solve to use the progressive strategy that was already implemented but not used:

```python
# NEW CODE - Progressive strategy
solve_result, used_radius = self._solve_with_progressive_radius(
    fits_path=fits_path,
    ra_hint=final_ra,
    dec_hint=final_dec,
    base_radius_deg=0.2,  # Start tight
    downsample=settings.astrometry_downsample,
    sigma=None,  # Auto-detect
    scale_low_arcsec=scale_low,
    scale_high_arcsec=scale_high,
    max_radius_deg=0.4,
    timeout_seconds=settings.astrometry_solve_timeout,
    radius_steps=[0.2, 0.3, 0.4],
    timeout_steps=[45, 60, 90],
)
logger.info(f"Solved with radius={used_radius:.2f}°")
```

### How Progressive Strategy Works

1. **First attempt**: 0.2° radius, 45s timeout
   - Most well-centered fields solve here
   - Fast failure if target not in field

2. **Second attempt**: 0.3° radius, 60s timeout
   - Catches moderate centering errors
   - More thorough search

3. **Third attempt**: 0.4° radius, 90s timeout
   - Maximum search area
   - Handles large slew errors

The strategy **stops on first success** - if 0.2° solves in 10 seconds, it doesn't waste time trying wider radii.

## Benefits

1. **Faster solves**: Most fields solve in <45s instead of timing out at 120s
2. **Better success rate**: Progressive fallback catches edge cases
3. **Consistent behavior**: Production now matches test mode
4. **Diagnostic info**: Logs which radius succeeded (`Solved with radius=0.2°`)
5. **Resource efficiency**: Tight radius uses less CPU/memory than wide radius

## Expected Log Output

### Successful solve (typical case):
```
INFO: Attempting plate solve with radius 0.20 deg (timeout=45s)
INFO: Solved with radius=0.20°
INFO: Main image solved: RA=68.687731, Dec=18.403476
```

### Fallback to wider radius:
```
INFO: Attempting plate solve with radius 0.20 deg (timeout=45s)
WARNING: Plate solve failed with radius 0.20 deg: solve-field timed out
INFO: Attempting plate solve with radius 0.30 deg (timeout=60s)
INFO: Solved with radius=0.30°
INFO: Main image solved: RA=68.687731, Dec=18.403476
```

### Complete failure (all radii exhausted):
```
INFO: Attempting plate solve with radius 0.20 deg (timeout=45s)
WARNING: Plate solve failed with radius 0.20 deg: solve-field timed out
INFO: Attempting plate solve with radius 0.30 deg (timeout=60s)
WARNING: Plate solve failed with radius 0.30 deg: solve-field timed out
INFO: Attempting plate solve with radius 0.40 deg (timeout=90s)
WARNING: Plate solve failed with radius 0.40 deg: solve-field timed out
ERROR: Main solve failed: solve-field timed out
```

## Configuration

The progressive strategy uses these settings from `config.py`:

- `astrometry_solve_timeout` (default: 120.0s) - base timeout, scaled per attempt
- `astrometry_downsample` (default: None) - downsample factor for speed
- `astrometry_pixel_scale_arcsec` - used for scale bounds (±10%)

Radius steps and timeout steps are hardcoded in the solve call but could be made configurable if needed.

## Files Modified

- [app/services/sequential_capture.py:631-659](../app/services/sequential_capture.py#L631-L659) - Main solve uses progressive strategy
- [documentation/LLM_SYSTEM_DESCRIPTION.md](./LLM_SYSTEM_DESCRIPTION.md) - Updated to clarify progressive strategy is used

## Related Issues

This fixes the solve timeout issue reported in production where well-centered fields were timing out instead of solving quickly.

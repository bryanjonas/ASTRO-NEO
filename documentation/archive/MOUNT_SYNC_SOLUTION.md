# Mount Sync Solution for Pointing Errors

## Problem Discovery

Blind plate solving revealed a **4.75° pointing error**:

```
Expected (from Horizons ephemeris for target "16"):
  RA:  68.687731° (04h 34m 45s)
  Dec: 18.403476° (+18° 24' 13")

Actual (from plate solve):
  RA:  64.374424° (04h 18m 51s)
  Dec: 16.046383° (+16° 02' 47")

Offset: 4.75° = 285 arcminutes
```

This explains why all hinted solves were timing out - the system was searching in the wrong part of the sky!

## Root Causes

The 4.75° pointing error could be from:

### 1. **Wrong Target Designation**
- Target "(16)" might not be the correct Horizons designation
- Check if it's a numbered asteroid that needs different format
- Verify the designation matches what Horizons expects

### 2. **Mount Pointing Model Drift**
- Mount alignment is off by ~5 degrees
- Could happen from:
  - Bumping the telescope
  - Mount not properly aligned to celestial pole
  - Wrong site coordinates in mount firmware
  - Mount sync points have drifted

### 3. **Site Coordinates Wrong**
- Lat/Lon in `site_local.yml` might be incorrect
- Horizons uses topocentric corrections based on site location
- Wrong location = wrong predicted position

### 4. **Coordinate Frame Mismatch**
- Mount using JNow, ephemeris using J2000
- This is handled in the code, but could have bugs

## Solution: Mount Sync After Plate Solve

NINA provides a `/equipment/mount/sync` API endpoint that updates the mount's pointing model.

### How Mount Sync Works

1. **Plate solve** finds where telescope is **actually** pointed
2. **Sync** tells the mount "you're actually pointing at RA=X, Dec=Y"
3. Mount updates its pointing model based on this correction
4. Future slews use the corrected model and are more accurate

### Implementation

A `sync_mount()` method has been added to `NinaBridgeService`:

```python
from app.services.nina_client import NinaBridgeService

nina = NinaBridgeService()

# After successful plate solve:
nina.sync_mount(ra_deg=solved_ra, dec_deg=solved_dec)
```

The method supports two modes:

**1. Manual sync (provide coordinates):**
```python
# Sync to known coordinates
nina.sync_mount(ra_deg=64.374424, dec_deg=16.046383)
```

**2. Plate solve sync (NINA does the solving):**
```python
# NINA will plate solve current image and sync automatically
nina.sync_mount()  # No coordinates = NINA solves first
```

## Testing Mount Sync

Use the test script to verify sync works:

```bash
# Dry run (doesn't actually sync):
docker compose exec api python3 /app/tests/test_mount_sync.py \
  '/data/fits/2026-01-29/(16)/SNAPSHOT/(16)_2026-01-29_19-42-25__60.00s_0000.fits'

# Actually sync the mount:
docker compose exec api python3 /app/tests/test_mount_sync.py \
  '/data/fits/2026-01-29/(16)/SNAPSHOT/(16)_2026-01-29_19-42-25__60.00s_0000.fits' \
  --sync
```

Expected output:
```
Step 1: Plate solving image (blind solve)...
✓ Solved successfully
  RA:  64.374424°
  Dec: 16.046383°

Step 2: Getting current mount position from NINA...
  Mount thinks it's at:
    RA:  68.674928°
    Dec: 18.396491°
  Offset: 4.7490° = 17096.6"

Step 3: Syncing mount to solved coordinates...
✓ Mount synced successfully: Synced
```

## Integration into Capture Flow

### Option 1: Sync After Every Successful Solve (Recommended)

Add sync to `sequential_capture.py` after plate solve succeeds:

```python
# After successful solve (line ~700):
capture.has_wcs = True
capture.solved_ra_deg = solved_ra
capture.solved_dec_deg = solved_dec

# NEW: Sync mount to improve pointing model
try:
    self.nina.sync_mount(ra_deg=solved_ra, dec_deg=solved_dec)
    logger.info("Mount synced to solved coordinates")
except Exception as e:
    logger.warning("Mount sync failed (non-fatal): %s", e)
```

This continuously improves the pointing model throughout the session.

### Option 2: Sync Once at Session Start

Add a calibration step to session startup:

```python
# In automation.py or session.py, before capturing:
def calibrate_mount(nina: NinaBridgeService, db: Session):
    """Take a calibration image, solve, and sync mount."""
    logger.info("Calibrating mount pointing...")

    # Take short calibration exposure
    nina.start_exposure(filter_name="L", binning=2, exposure_seconds=5.0)
    nina.wait_for_camera_idle()

    # Find the FITS file, solve it
    fits_path = find_latest_fits()
    result = solve_fits(fits_path, ra_hint=None, dec_hint=None)

    # Sync mount
    nina.sync_mount(ra_deg=result['solution']['ra_deg'],
                    dec_deg=result['solution']['dec_deg'])

    logger.info("Mount calibrated successfully")
```

### Option 3: Only Sync on Large Errors

Sync only when offset exceeds threshold:

```python
# After solve, check offset:
sep_arcsec = calculate_separation(predicted_ra, predicted_dec, solved_ra, solved_dec)

if sep_arcsec > 300:  # More than 5 arcminutes off
    logger.warning(f"Large pointing error: {sep_arcsec:.1f}\", syncing mount")
    nina.sync_mount(ra_deg=solved_ra, dec_deg=solved_dec)
```

## Investigating the Root Cause

### Check Target Designation

```bash
# Query Horizons directly to verify target exists:
docker compose exec api python3 -c "
from app.services.horizons_client import HorizonsClient
from datetime import datetime
from app.core.site_config import load_site_config

site = load_site_config()
horizons = HorizonsClient(site.latitude, site.longitude, site.altitude_m)

# Try different designation formats:
for designation in ['16', '(16)', '16 Psyche']:
    try:
        pos = horizons.get_current_position(designation)
        print(f'{designation}: RA={pos[\"ra_deg\"]:.6f}, Dec={pos[\"dec_deg\"]:.6f}')
    except Exception as e:
        print(f'{designation}: FAILED - {e}')
"
```

### Check Site Coordinates

```bash
# Verify site config:
docker compose exec api python3 -c "
from app.core.site_config import load_site_config
site = load_site_config()
print(f'Latitude:  {site.latitude:.6f}°')
print(f'Longitude: {site.longitude:.6f}°')
print(f'Altitude:  {site.altitude_m}m')
print(f'Timezone:  {site.timezone}')
print(f'Station:   {site.station_code}')
"
```

### Verify Mount Coordinates Frame

Check if mount is reporting J2000 or JNow:

```bash
docker compose exec api python3 -c "
from app.services.nina_client import NinaBridgeService
nina = NinaBridgeService()
info = nina.mount_info_raw()
print('Mount info:', info)
"
```

## Expected Results After Sync

1. **Immediate**: Future slews will be within ~30 arcseconds of target
2. **Over time**: Multiple syncs build a better pointing model
3. **Persistent**: NINA may save sync points across sessions (depends on mount driver)

## Caveats

- **Mount parked**: Sync will fail if mount is parked
- **Sync points**: Some mounts limit number of sync points (typically 50-200)
- **Clear sync**: Restarting mount driver may clear sync points
- **Meridian flip**: Sync points may not apply across meridian

## Files Modified

- `app/services/nina_client.py`: Added `sync_mount()` method
- `tests/test_mount_sync.py`: Test script for mount sync

## Next Steps

1. **Immediate**: Run `test_mount_sync.py --sync` to fix current pointing
2. **Short-term**: Integrate sync into capture flow (Option 1 recommended)
3. **Long-term**: Investigate why mount is 5° off (could be hardware issue)

## Related Issues

This addresses the plate solve timeout problem discovered during testing. The progressive solve strategy and index files were correct - the issue was simply that the mount was pointed at the wrong coordinates.

# Astrometry.net Index Diagnosis

## Problem Summary

Plate solves are timing out even with:
- Progressive solve strategy (0.2° → 0.3° → 0.4°)
- Reduced source count (downsample=4 gives 617 sources instead of 1215)
- Wide scale bounds (0.5 - 3.0 arcsec/px)

## System Configuration

**Telescope/Camera:**
- Pixel scale: **2.39 arcsec/px**
- Computed from: 2.9 µm pixels / 250 mm focal length
- Expected scale bounds: 2.15 - 2.63 arcsec/px (±10%)

**Current Indexes:**
```
/data/indexes/:
- index-4107.fits (158 MB) - covers ~1.0-1.4 arcsec/px
- index-4108.fits (91 MB)  - covers ~1.4-2.0 arcsec/px [CORRUPTED?]
- index-4109.fits (48 MB)  - covers ~2.0-2.8 arcsec/px ← SHOULD WORK
- index-4204-*.fits (27 MB × 47 files) - covers 8.0-11.3 arcsec/px
- index-4205-4212 series - covers 11.3-44+ arcsec/px
```

## Observed Errors

From solve attempts:
```
solve-field: Failed to add index "/data/indexes/index-4108.fits".
fitsbin.c:448:read_chunk: Couldn't find table "kdtree_data_codes" in file "/data/indexes/index-4108.fits"
index.c:329:index_reload: Failed to read star kdtree from file /data/indexes/index-4108.fits
engine.c:199:engine_add_index: Failed to load index from path /data/indexes/index-4108.fits
```

This indicates **index-4108.fits is corrupted** or incomplete.

## Test Results

| Downsample | Scale Bounds | Sources | Radius | Time  | Result   |
|------------|--------------|---------|--------|-------|----------|
| 2          | 2.15-2.63    | 1215    | 0.2°   | 47s   | Timeout  |
| 2          | 2.15-2.63    | 1215    | 0.3°   | 64s   | Timeout  |
| 2          | 2.15-2.63    | 1215    | 0.4°   | 96s   | Timeout  |
| 4          | 2.15-2.63    | 617     | 0.2°   | 47s   | Timeout  |
| 4          | 2.15-2.63    | 617     | 0.3°   | 64s   | Timeout  |
| 4          | 2.15-2.63    | 617     | 0.4°   | 95s   | Timeout  |
| 4          | 0.5-3.0      | 617     | 0.2°   | 47s   | Timeout  |
| 4          | 0.5-3.0      | 617     | 0.3°   | 64s   | Timeout  |
| 4          | 0.5-3.0      | 617     | 0.4°   | 94s   | Timeout  |

**Conclusion**: Neither downsampling nor relaxing scale bounds helps. The issue is likely:
1. Missing/corrupted index files for the actual pixel scale
2. RA/Dec coordinates are significantly wrong (>0.4° off)

## Recommended Solution

### Option 1: Download Missing Index Files (RECOMMENDED)

Download index series **4200** which covers 2.0-2.8 arcsec/px:

```bash
# Download index-4200 series (covers 2.0-2.8 arcsec/px)
cd /data/indexes/
wget http://data.astrometry.net/4200/index-4200-{00..47}.fits
```

This will download ~1.3 GB (48 files × 27 MB each).

### Option 2: Fix/Replace Corrupted index-4108.fits

The index-4108.fits file appears corrupted. Re-download:

```bash
cd /data/indexes/
rm index-4108.fits
wget http://data.astrometry.net/4100/index-4108.fits
```

### Option 3: Verify Ephemeris Coordinates

If downloading indexes doesn't help, the ephemeris coordinates might be wrong. Test with a blind solve:

```bash
docker compose exec api python3 -c "
from app.services.solver import solve_fits
result = solve_fits(
    fits_path='/data/fits/2026-01-29/(16)/SNAPSHOT/(16)_2026-01-29_19-42-25__60.00s_0000.fits',
    ra_hint=None,  # Blind solve
    dec_hint=None,
    radius_deg=None,
    downsample=4,
    timeout=300
)
print(f\"Solved: RA={result['solution']['ra_deg']:.6f}, Dec={result['solution']['dec_deg']:.6f}\")
"
```

## Astrometry.net Index Series Reference

| Series | Pixel Scale Range (arcsec/px) | File Size | Files | Total Size |
|--------|-------------------------------|-----------|-------|------------|
| 4200   | 2.0 - 2.8                     | 27 MB     | 48    | ~1.3 GB    |
| 4201   | 2.8 - 4.0                     | 27 MB     | 48    | ~1.3 GB    |
| 4202   | 4.0 - 5.6                     | 27 MB     | 48    | ~1.3 GB    |
| 4203   | 5.6 - 8.0                     | 27 MB     | 48    | ~1.3 GB    |
| 4204   | 8.0 - 11.3                    | 27 MB     | 48    | ~1.3 GB    |

For 2.39 arcsec/px, you need **index-4200**.

## Quick Fix Commands

```bash
# 1. Download index-4200 series
docker compose exec api bash -c 'cd /data/indexes && wget -q --show-progress http://data.astrometry.net/4200/index-4200-{00..47}.fits'

# 2. Test solve after download
docker compose exec api python3 /app/tests/test_progressive_solve.py \
  '/data/fits/2026-01-29/(16)/SNAPSHOT/(16)_2026-01-29_19-42-25__60.00s_0000.fits' \
  --ra 68.687730582 \
  --dec 18.403475683 \
  --scale-low 2.153 \
  --scale-high 2.632 \
  --downsample 2
```

## Expected Outcome

After downloading index-4200, solves should succeed in **<10 seconds** at 0.2° radius for well-centered fields.

# Association Workflow Fix: Eliminating Circular Detection Logic

**Date**: 2025-01-30
**Branch**: main
**Commit**: 63abd6e9b2fb1d3ac94e77dc02eaf985fdf723ea

## Problem Statement

The association workflow was using ephemeris predictions BEFORE detection, creating circular logic that compromised scientific validity:

### Previous (Incorrect) Workflow
```
1. Query ephemeris → Get predicted RA/Dec
2. Pass predicted position to star subtraction
3. Create 20″ exclusion zone around predicted position
4. Detect sources (with catalog stars NOT subtracted near prediction)
5. Match detection to prediction
6. Calculate residual
```

### Issues with Previous Approach

1. **Circular Logic**: Ephemeris predictions influenced what was detected
2. **Biased Detection**: 20″ exclusion zone prevented subtraction of catalog stars near predicted position
3. **False Positives**: Could match field stars that weren't subtracted due to exclusion zone
4. **Invalid Residuals**: Residuals didn't represent true measurement accuracy

### Correct (Scientific) Workflow

The proper methodology for finding moving objects follows this order:

```
A. Detect sources blindly
   - Threshold/PSF fitting
   - NO ephemerides involved
   - Output: (pixel_x, pixel_y, flux, time)

B. Solve astrometry
   - Plate solve using star catalog (Gaia DR3)
   - Convert pixel coordinates → sky coordinates
   - Output: (RA, Dec, time, uncertainty)

C. Identify known objects
   - Query ephemerides (JPL Horizons, MPC)
   - Compare measured positions to predicted positions
   - Associate based on angular proximity
   - Output: residuals (measured - predicted)
```

**Key Principle**: The ephemeris predicts where the object SHOULD be. Your data measures where something ACTUALLY was. The residuals are the scientific product.

## Workflow Separation

The system has TWO distinct workflows that use ephemeris data differently:

### 1. Slew/Expose Workflow (Pre-Image Capture)
**Purpose**: Point telescope and capture images
**Ephemeris Use**: **LEGITIMATE** - ephemeris is REQUIRED to point the telescope

**Files**:
- `app/services/acquisition.py`
- `app/services/capture_loop.py`
- `app/services/prediction.py`

**Process**:
```python
# 1. Predict target position (needed to aim telescope)
ra_pred, dec_pred = EphemerisPredictionService.predict(candidate_id, time)

# 2. Slew telescope to predicted position
bridge.slew(ra_pred, dec_pred)

# 3. Take confirmation exposure
# 4. Take science exposure → FITS file written to disk
```

This is correct and necessary - you cannot aim a telescope without knowing where to point.

### 2. Association Workflow (Post-Image Analysis)
**Purpose**: Detect objects and compare measurements to predictions
**Ephemeris Use**: Only for comparison AFTER independent detection

**Files**:
- `app/services/analysis.py`
- `app/services/star_subtraction.py`

**Process**:
```python
# A. Detect sources blindly (NO ephemeris)
detections = detect_sources_with_star_subtraction(
    fits_path, wcs,
    target_ra=None,           # ← No predicted position
    target_dec=None,          # ← No exclusion zone
    exclusion_radius_arcsec=0.0  # ← Subtract ALL stars
)

# B. Astrometry already solved (WCS converts pixels → RA/Dec)
# All detections now have independent (RA, Dec) measurements

# C. NOW query ephemeris (after blind detection)
ephemeris = query_ephemeris(target, time)

# D. Compare independent measurements to predictions
match = find_best_match(detections, ephemeris.ra, ephemeris.dec, tolerance=10.0)
residual = calculate_residual(match.ra, match.dec, ephemeris.ra, ephemeris.dec)
```

## Code Changes Required

### 1. `app/services/star_subtraction.py`

Make target coordinates optional to support blind detection:

```python
class CatalogStarSubtractor:
    def subtract_stars(
        self,
        data: np.ndarray,
        target_ra: float | None = None,        # ← Changed from required float
        target_dec: float | None = None,       # ← Changed from required float
        exclusion_radius_arcsec: float = 0.0,  # ← Changed default from 20.0 to 0.0
        star_fwhm_px: float = 4.0
    ) -> tuple[np.ndarray, int]:
        """
        Subtract catalog stars from image.

        Args:
            data: Image data array
            target_ra: Optional target RA in degrees (for exclusion zone)
            target_dec: Optional target Dec in degrees (for exclusion zone)
            exclusion_radius_arcsec: Don't subtract within this radius of target (default 0 = no exclusion)
            star_fwhm_px: FWHM of stars in pixels (for Gaussian model)

        Returns:
            Tuple of (cleaned image data, number of stars subtracted)
        """
        # Load WCS and catalog stars...

        # Convert target to pixels and calculate exclusion zone (if target provided)
        target_x, target_y = None, None
        exclusion_radius_px = 0.0

        if target_ra is not None and target_dec is not None and exclusion_radius_arcsec > 0:
            try:
                target_x, target_y = wcs.world_to_pixel_values(target_ra, target_dec)
                pixel_scale = self._get_pixel_scale(wcs)
                if pixel_scale == 0:
                    logger.warning("Could not determine pixel scale, using default exclusion")
                    exclusion_radius_px = 50
                else:
                    exclusion_radius_px = exclusion_radius_arcsec / pixel_scale
                logger.debug(f"Using exclusion zone: {exclusion_radius_arcsec}\" around ({target_ra:.5f}, {target_dec:.5f})")
            except Exception as e:
                logger.warning(f"Failed to convert target position: {e}. Proceeding without exclusion zone.")
                target_x, target_y = None, None
                exclusion_radius_px = 0.0

        # Subtract each star
        for star in catalog_stars:
            x, y = star['x'], star['y']

            # Skip if near target (only if exclusion zone is active)
            if target_x is not None and target_y is not None and exclusion_radius_px > 0:
                dist = np.sqrt((x - target_x)**2 + (y - target_y)**2)
                if dist < exclusion_radius_px:
                    logger.debug(f"Skipping star at ({x:.1f}, {y:.1f}) - too close to target")
                    continue

            # Subtract star...
```

**Key Changes**:
- `target_ra` and `target_dec`: `float` → `float | None = None`
- `exclusion_radius_arcsec`: default `20.0` → `0.0`
- Added conditional logic to only apply exclusion zone when target is provided
- Enhanced logging to show when exclusion zones are active

### 2. `app/services/analysis.py`

#### 2a. Update `detect_sources_with_star_subtraction()` signature

```python
def detect_sources_with_star_subtraction(
    self,
    path: Path,
    wcs: WCS,
    target_ra: float | None = None,        # ← Made optional
    target_dec: float | None = None,       # ← Made optional
    exclusion_radius_arcsec: float = 0.0   # ← Changed default from 20.0 to 0.0
) -> Tuple[List[dict[str, Any]], int]:
    """
    Detect sources after subtracting field stars.

    Args:
        path: Path to FITS file
        wcs: WCS solution
        target_ra: Optional target RA in degrees (for exclusion zone)
        target_dec: Optional target Dec in degrees (for exclusion zone)
        exclusion_radius_arcsec: Don't subtract within this radius of target (default 0 = no exclusion)

    Returns:
        Tuple of (detected sources, number of stars subtracted)
    """
    # Load FITS data...

    # Subtract catalog stars
    subtractor = CatalogStarSubtractor(path)
    cleaned_data, stars_subtracted = subtractor.subtract_stars(
        data, target_ra, target_dec, exclusion_radius_arcsec
    )

    # Detect sources in cleaned image...
```

#### 2b. Restructure `auto_associate()` to follow A→B→C workflow

**BEFORE** (incorrect - ephemeris first):
```python
def auto_associate(self, db, capture, wcs, use_star_subtraction=True):
    # 1. Find Ephemeris FIRST ❌
    ephems = db.exec(select(NeoEphemeris).where(...)).all()
    best_eph = find_nearest(ephems, capture.started_at)

    # 2. Detect with ephemeris bias ❌
    detections, stars_subtracted = self.detect_sources_with_star_subtraction(
        Path(capture.path), wcs,
        best_eph.ra_deg,              # ← Using prediction!
        best_eph.dec_deg,             # ← Using prediction!
        exclusion_radius_arcsec=20.0  # ← Creating exclusion zone!
    )

    # 3. Match and calculate residual
    match = self.find_best_match(detections, best_eph.ra_deg, best_eph.dec_deg, 10.0)
    residual = self._calculate_residual(...)
```

**AFTER** (correct - detection first):
```python
def auto_associate(self, db, capture, wcs, use_star_subtraction=True):
    """
    Attempt to automatically associate a capture with its target ephemeris.

    Follows proper workflow separation:
    A. Detect sources blindly (no ephemeris)
    B. Astrometry already solved (WCS provided)
    C. Query ephemeris and compare predictions to measurements
    """

    # STEP A: Detect sources blindly (NO ephemeris data used) ✅
    stars_subtracted = 0
    if use_star_subtraction:
        # Subtract ALL catalog stars (no exclusion zone)
        detections, stars_subtracted = self.detect_sources_with_star_subtraction(
            Path(capture.path), wcs,
            target_ra=None,           # ← No target position
            target_dec=None,          # ← No exclusion zone
            exclusion_radius_arcsec=0.0  # ← Subtract everything
        )
    else:
        detections = self.detect_sources(Path(capture.path), wcs)

    if not detections:
        logger.warning(f"No sources detected in {capture.path}")
        return None

    logger.info(f"Detected {len(detections)} sources blindly (no ephemeris bias)")

    # STEP B: Astrometry already solved ✅
    # All detections now have independent (RA, Dec) measurements from WCS

    # STEP C: NOW query ephemeris (after blind detection is complete) ✅
    if not capture.target or capture.target == "unknown":
        logger.debug("No target specified, cannot compare to ephemeris")
        return None

    ephems = db.exec(select(NeoEphemeris).where(NeoEphemeris.trksub == capture.target)).all()
    if not ephems:
        logger.warning(f"No ephemeris found for target {capture.target}")
        return None

    # Find ephemeris point nearest to capture time
    best_eph = None
    min_diff = float("inf")
    for eph in ephems:
        diff = abs((eph.epoch - capture.started_at).total_seconds())
        if diff < min_diff:
            min_diff = diff
            best_eph = eph

    if not best_eph or min_diff > 300:  # > 5 mins
        logger.warning(f"Nearest ephemeris is {min_diff:.0f}s away (> 300s limit)")
        return None

    logger.info(f"Comparing to ephemeris from {best_eph.epoch} (Δt={min_diff:.1f}s)")

    # STEP C continued: Compare independent measurements to predictions ✅
    tolerance_arcsec = 10.0
    match = self.find_best_match(
        detections,
        best_eph.ra_deg,
        best_eph.dec_deg,
        tolerance_arcsec=tolerance_arcsec
    )

    if not match:
        logger.warning(
            f"No match within {tolerance_arcsec}\" of predicted position "
            f"({best_eph.ra_deg:.5f}, {best_eph.dec_deg:.5f})"
        )
        return None

    # Calculate residual (the scientific product: measured - predicted)
    residual_arcsec = self._calculate_residual(
        match["ra_deg"], match["dec_deg"],
        best_eph.ra_deg, best_eph.dec_deg
    )

    logger.info(
        f"Matched source at ({match['ra_deg']:.5f}, {match['dec_deg']:.5f}) "
        f"with residual {residual_arcsec:.2f}\" (SNR={match.get('snr', 0):.1f})"
    )

    # Create association record
    assoc = CandidateAssociation(
        capture_id=capture.id,
        ra_deg=match["ra_deg"],
        dec_deg=match["dec_deg"],
        predicted_ra_deg=best_eph.ra_deg,
        predicted_dec_deg=best_eph.dec_deg,
        residual_arcsec=residual_arcsec,
        snr=match.get("snr"),
        peak_counts=match.get("peak"),
        method="auto",
        stars_subtracted=stars_subtracted if use_star_subtraction else None,
        created_at=datetime.utcnow(),
    )
    db.add(assoc)
    db.commit()
    db.refresh(assoc)

    return assoc
```

**Key Changes**:
- Moved ephemeris query from step 1 to step 3 (after detection)
- Pass `None` for target coordinates to star subtraction
- Added detailed comments documenting A→B→C workflow
- Enhanced logging to indicate blind detection

## Testing the Changes

After applying these changes, verify:

1. **Star subtraction works without target coordinates**:
   ```python
   subtractor = CatalogStarSubtractor(fits_path)
   cleaned, count = subtractor.subtract_stars(data)  # No target args
   # Should subtract ALL catalog stars
   ```

2. **Association still finds objects**:
   ```python
   assoc = analysis.auto_associate(db, capture, wcs, use_star_subtraction=True)
   # Should detect blindly, then match to ephemeris
   ```

3. **Residuals are scientifically valid**:
   - Check that field stars near predicted positions ARE subtracted
   - Verify residuals represent true measurement - prediction difference
   - Confirm no bias toward matching at predicted position

4. **Logging shows correct order**:
   ```
   INFO: Detected 47 sources blindly (no ephemeris bias)
   INFO: Comparing to ephemeris from 2026-01-30 12:30:15 (Δt=2.3s)
   INFO: Matched source at (123.45678, 12.34567) with residual 0.42" (SNR=18.2)
   ```

## Migration Guide for Other Branches

To apply this fix to another branch:

### Step 1: Identify Your Association Code

Find the equivalent of `auto_associate()` in your branch. Look for:
- Functions that query ephemeris data
- Functions that call star subtraction with target coordinates
- Functions that match detections to predicted positions

### Step 2: Check for Circular Logic

Identify if your code has the same issue:
```python
# ❌ PROBLEMATIC PATTERN:
ephemeris = get_ephemeris(target, time)  # Queried FIRST
detections = detect_with_subtraction(
    image,
    target_ra=ephemeris.ra,  # Using prediction to guide detection!
    exclusion_radius=20.0    # Creating exclusion zone!
)
```

### Step 3: Apply the Fix

Restructure to follow A→B→C:
```python
# ✅ CORRECT PATTERN:
# A. Detect blindly
detections = detect_with_subtraction(
    image,
    target_ra=None,           # No prediction
    exclusion_radius=0.0      # No exclusion
)

# B. Astrometry (already done via WCS)

# C. Compare to ephemeris
ephemeris = get_ephemeris(target, time)  # Queried LAST
match = find_best_match(detections, ephemeris.ra, ephemeris.dec)
residual = match.position - ephemeris.position
```

### Step 4: Update Function Signatures

Make target coordinates optional throughout your codebase:
- Star subtraction functions
- Detection functions that use star subtraction
- Any function that creates exclusion zones

### Step 5: Update Documentation

Document the workflow separation in your branch's documentation:
- Slew/expose workflow (legitimate ephemeris use)
- Association workflow (ephemeris only for comparison)
- Critical design principles

## Scientific Impact

### Benefits of This Fix

1. **Non-circular Detection**: Detections are independent of predictions
2. **Unbiased Measurements**: Field stars everywhere are subtracted equally
3. **Valid Residuals**: Residuals represent true measurement accuracy
4. **Scientific Integrity**: Can publish results with confidence
5. **Fail-safe**: If ephemeris unavailable, still get blind detections

### Before vs. After

**Before** (with circular logic):
- Residual could be small because detection was biased toward prediction
- Field star near prediction might not be subtracted → false match
- Cannot distinguish measurement quality from circular bias

**After** (with proper workflow):
- Residual represents true measurement - prediction difference
- All field stars subtracted → only real moving objects remain
- Residuals are scientifically valid astrometric measurements

## References

### Proper Methodology for Moving Object Detection

From professional asteroid detection pipelines:

1. **Detect all point sources** blindly (stars + moving objects)
   - Thresholding / PSF fitting
   - Difference imaging
   - Track-and-stack for faint objects
   - Output: (x, y, flux, time) - nothing else

2. **Solve astrometry** using star catalog
   - Plate solve with Gaia DR3
   - Convert pixels → RA/Dec
   - Output: (RA, Dec, time, uncertainty)

3. **Identify known objects** using ephemerides
   - Query MPC or JPL Horizons
   - Compare measured to predicted positions
   - Associate based on proximity
   - Output: residuals (the scientific product)

**Key Principle**: This is NOT circular because:
- The ephemeris predicts where the object SHOULD be
- Your data measures where something ACTUALLY was
- The residuals tell you how accurate your measurement is

## Summary Checklist

When applying this fix, ensure:

- [ ] Star subtraction functions accept optional target coordinates
- [ ] Default exclusion radius is 0.0 (no exclusion)
- [ ] Association workflow detects BEFORE querying ephemeris
- [ ] Detection uses `target_ra=None`, `target_dec=None`, `exclusion_radius=0.0`
- [ ] Ephemeris query happens AFTER blind detection
- [ ] Logging shows correct order (detect → compare to ephemeris)
- [ ] Documentation clearly separates slew/expose from association workflows
- [ ] Tests verify all catalog stars are subtracted (no exclusion zones)
- [ ] Residuals represent true measurement - prediction difference

## Contact

For questions about applying this fix to other branches, refer to:
- This document (`documentation/ASSOCIATION_FIX.md`)
- System description (`documentation/LLM_SYSTEM_DESCRIPTION.md` Section 8)
- Commit 63abd6e9b2fb1d3ac94e77dc02eaf985fdf723ea on main branch

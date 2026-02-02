Below is an implementation plan that keeps **centroiding ephemeris-independent**, does **multi-exposure confirmation (rate + direction)**, uses a **data-driven match radius**, hardens **star-subtraction false-positive control**, and validates **timing + topocentric geometry**.

---

## 0) Design principle and data model

**Rule:** ephemeris can be used for (a) *scheduling/pointing*, (b) *post-measurement association*, and (c) *expected motion gating*—but never to *seed* centroiding or to *force-measure* at the predicted location.

### Per-exposure products

For each exposure *i*:

* `t_mid_i` (UTC, mid-exposure)
* WCS solution + fit diagnostics (`wcs_rms_arcsec`, #ref stars, outlier count, distortion model)
* detection list `D_i = {d_ij}` with:

  * pixel centroid (x,y) from image-only
  * astrometric (RA,Dec) via WCS
  * centroid covariance or at least σ_RA, σ_Dec
  * shape features: FWHM, elongation, sharpness, residual dipole metrics
  * SNR, flux, local background
  * flags: near saturated star, near masked region, near subtraction artifact risk zones

Keep **pixel-space** centroids and their errors; convert to sky later.

---

## 1) Timing + topocentric geometry validation (do first)

### 1.1 Timestamp integrity

* Use **mid-exposure time**: `t_mid = t_start + 0.5 * exposure_duration`
* Confirm:

  * FITS `DATE-OBS` meaning (start vs mid) and `EXPTIME`
  * shutter travel/rolling shutter correction if applicable
  * time source is disciplined (NTP/PTP/GPS). Log offset/uncertainty.

### 1.2 Observer location

* Enforce one of:

  * MPC observatory code → convert to topocentric position at `t_mid`
  * explicit geodetic lat/lon/alt
* Store as ECEF/ITRF or lat/lon/alt with datum.

### 1.3 Ephemeris mode

When you later compute predicted positions:

* request **topocentric** predictions at `t_mid_i` for your observer location
* use consistent time scale (UTC vs TT) and document it

**Acceptance checks**

* If timing uncertainty > X seconds (pick X based on fastest expected rates), mark the night as “timing suspect.”
* If observer location missing → do not output MPC-grade residuals.

---

## 2) Astrometric solution and WCS quality gate (per exposure)

### 2.1 Solve WCS (independent of target)

* Use Gaia-based reference with proper motions if possible.
* Record:

  * `wcs_rms_arcsec` (overall and robust)
  * per-axis residual stats
  * #stars used, #rejected
  * distortion degree

### 2.2 WCS gating

Set thresholds (tune empirically):

* `wcs_rms_arcsec <= 0.3–0.7"` typical depending on optics/seeing
* minimum reference stars (e.g., ≥ 20)
* if `wcs_rms_arcsec` is high, you can still detect but your match radius must inflate accordingly (see §5).

---

## 3) Detection pipeline with star-subtraction false-positive hardening

### 3.1 Run detection in two branches

**Branch A: star-subtracted image** (good for faint movers)
**Branch B: original image** (truth-check and artifact veto)

You detect candidates in A, but validate morphology/photometry in B.

### 3.2 Star-subtraction controls

* Build a **mask** for:

  * saturated cores + bleed trails
  * diffraction spikes
  * halos around bright stars
  * bad pixels / columns
* For each candidate, compute “subtraction risk features”:

  * distance to nearest bright star / saturated pixel
  * local background gradient
  * dipole statistic (positive/negative residual adjacency)
  * correlation with PSF residual template

### 3.3 Candidate feature gating

Reject likely non-asteroids:

* **Cosmic rays/hot pixels**:

  * too sharp: FWHM << stellar FWHM
  * single-pixel / extreme sharpness
* **Subtraction residuals**:

  * dipole-like structure
  * aligned with bright star diffraction spike direction
  * appears only in subtraction branch, absent in original branch
* **Stationary sources**:

  * matches a catalog star/galaxy within tight radius (e.g., 1–2σ of astrometry)

Keep a “maybe” bucket but don’t let it win association unless tracklet consistency is strong.

---

## 4) Centroiding: enforce ephemeris independence

### 4.1 Centroiding method

For each detection, centroid using image-only methods:

* weighted centroid in a window around the detection peak found by detection algorithm, OR
* PSF fit initialized from the detection peak (not ephemeris), OR
* 2D Gaussian fit seeded from detection peak

### 4.2 Prevent “forced” measurement

Hard rules:

* never create a detection at the predicted RA/Dec
* never recenter the window using ephemeris
* never drop detections because they are “too far” until after centroiding

### 4.3 Record measurement uncertainty

Estimate centroid uncertainty (pixel):

* σ_x, σ_y from fit residuals and SNR
  Convert to sky using WCS Jacobian, yielding σ_RA, σ_Dec (and optionally covariance).

This is necessary for data-driven radii and tracklet scoring.

---

## 5) Data-driven match radius (per exposure)

You want a match gate that reflects:

* WCS error,
* centroiding error,
* ephemeris uncertainty (if available),
* timing uncertainty mapped into angle via rate.

### 5.1 Build a combined uncertainty

For exposure i:

* `σ_ast_i` ≈ sqrt(σ_wcs_i² + σ_cent_i²)  (arcsec)
* `σ_time_i` = timing uncertainty in seconds
* `v_pred` = predicted angular rate (arcsec/sec) from ephemeris (used only for gating after detection)
* time-induced positional uncertainty ≈ `σ_time_pos_i = v_pred * σ_time_i`
* optional ephemeris uncertainty `σ_eph_i` (if you can obtain it; often not directly available)

Then:

* `σ_total_i = sqrt(σ_ast_i² + σ_time_pos_i² + σ_eph_i²)`

### 5.2 Convert to radius

Use a multiplier k (e.g., k=3 for ~99.7% if Gaussian-ish):

* `r_match_i = max(r_min, k * σ_total_i)`
  Where `r_min` maybe 1–2 arcsec to avoid over-tightening when stats are optimistic.

Log `r_match_i` per exposure so you can audit why a candidate matched.

---

## 6) Multi-exposure confirmation: build tracklets using rate + direction

### 6.1 Candidate association without ephemeris (primary)

Build tracklets purely from detections across exposures by motion consistency:

1. For each detection in first exposure, predict a **motion corridor** to exposure 2 using a broad allowed rate range (or inferred from typical NEO/MBAs if you must).
2. Link detections that yield plausible velocity vectors.
3. Extend to ≥3 exposures and fit linear motion in tangent plane (ξ,η) vs time.

This gives “blind tracklets.”

### 6.2 Ephemeris-informed gating (secondary, after tracklets exist)

For each blind tracklet:

* compute observed angular velocity vector `v_obs` (rate + PA)
* compute predicted `v_pred` from ephemeris at mid-times
* require consistency:

  * direction difference |ΔPA| < threshold (e.g., < 10–20° depending on errors)
  * rate ratio within bounds (e.g., |v_obs - v_pred| < Nσ)

And also require observed positions be within the per-exposure `r_match_i` of predicted.

### 6.3 Scoring instead of hard “closest wins”

For association to a specific object (MPC target), compute a likelihood score:

For each exposure i in a tracklet:

* residual vector `Δ_i = (obs_i - pred_i)` in arcsec
* normalized residual `z_i = |Δ_i| / σ_total_i`

Tracklet score:

* `S = Σ z_i²` (lower is better), plus penalties for bad morphology / high subtraction-risk.

Pick the candidate tracklet with minimum S, but also require:

* no single z_i wildly dominates (outlier check)
* morphology flags acceptable in most frames

---

## 7) Final validation outputs (what you log/export)

For the winning tracklet:

* table per exposure:

  * t_mid (UTC)
  * RA, Dec (J2000)
  * σ_RA, σ_Dec
  * WCS RMS
  * residual O–C in arcsec (RA*cosDec, Dec)
  * z-score, r_match_i
  * morphology + subtraction-risk flags
* tracklet-level:

  * fitted `v_obs` (rate, PA) with uncertainty
  * Δrate, ΔPA vs ephemeris
  * quality grade (A/B/C)

This makes the pipeline auditable and “MPC defensible.”

---

## 8) Practical build sequence (implementation order)

1. **Timing + observer config module** (strict validation, fail fast)
2. **WCS solve + diagnostics** (store RMS, outliers)
3. **Dual-branch detection** (subtracted + original), candidate feature extraction
4. **Ephemeris-independent centroiding** with error estimates
5. **Blind tracklet builder** (linear motion fit in tangent plane)
6. **Ephemeris association + scoring** (data-driven radii, rate/PA gating)
7. **Reporting/export** (tracklet table + QC logs)

---

If you share:

* your exposure cadence (Δt between frames),
* typical seeing/FWHM and plate scale,
* current WCS RMS,
* and the fastest movers you expect to confirm,

I can give concrete default thresholds for `r_min`, k, PA/rate tolerances, and morphology cuts that won’t bias toward the ephemeris.

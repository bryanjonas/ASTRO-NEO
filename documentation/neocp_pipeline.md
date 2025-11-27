# 🚀 NEOCP Automation Pipeline  
## End-to-End Logic for Solving, Detection, Association, & MPC Reporting

This document defines the processing stages, data structures, and decision logic required to automate NEOCP astrometry and photometry reporting.

---

# 1. Overview of Pipeline Stages

The full automation flow is:

```
Calibrated FITS → Solve (WCS) → Source Detection → NEOCP Association
→ Centroid & Photometry → QA → ADES/OBS80 Construction → MPC Submission
```

Each stage produces a structured output consumed by the next stage.

---

# 2. Input Requirements

Each exposure must provide:

- **FITS image** (bias/dark/flat corrected)
- **Exposure start time** (UTC)
- **Exposure duration**
- **Observation setup** (station code, telescope, camera)
- **NEOCP ephemeris for image mid-time**, including:
  - `ra_pred`, `dec_pred`
  - `dra_dt`, `ddec_dt`
  - Predicted magnitude (optional)
  - Predicted uncertainty (if available)

---

# 3. Solver Step (Astrometric Plate Solution)

## 3.1 Inputs
- Calibrated FITS
- Astrometry.net index directory
- Optional: approximate scale and RA/Dec hints

## 3.2 Process
1. Run astrometric solver (e.g., `solve-field`, astrometry.net API, or library bindings).
2. Extract WCS:
   - CD matrix
   - CRVAL / CRPIX
   - Distortion terms
3. Extract quality metrics:
   - Plate solution RMS (arcsec)
   - Catalog used (Gaia DR3 recommended)
   - Number of stars matched

## 3.3 Outputs
```json
{
  "wcs": "<astropy.wcs object>",
  "solver_rms_arcsec": 0.3,
  "catalog": "Gaia DR3",
  "success": true
}
```

If solving fails, the frame is rejected or retried.

---

# 4. Detection Step (Find All Candidate Sources)

## 4.1 Inputs
- FITS image
- WCS solution
- Image statistics (background, noise)

## 4.2 Process
1. Run source extractor (SEP/SExtractor/photutils).
2. Measure for each detection:
   - RA/Dec (via WCS)
   - Pixel centroid
   - Flux, instrumental magnitude
   - SNR
   - FWHM
   - Elongation, orientation angle
   - Flags (saturated, edge proximity)

## 4.3 Output example
```json
{
  "detections": [
    {
      "x": 1234.5,
      "y": 812.3,
      "ra": 150.123456,
      "dec": 2.345678,
      "snr": 24.5,
      "fwhm": 3.1,
      "ellipticity": 0.05,
      "theta": 92.3,
      "mag_inst": 17.81,
      "flags": []
    }
  ]
}
```

---

# 5. NEOCP Association Step

This identifies **which detection is the actual NEOCP target** in each frame and links detections across frames into a tracklet.

## 5.1 Inputs
- List of detections per frame
- Ephemeris per frame

## 5.2 Per-Frame Spatial Gating
Compute angular distance for each detection and keep only detections within a computed search radius.

## 5.3 Multi-Frame Tracklet Assembly
Test all combinations across frames, fit linear motion, compute residuals, score combinations, and select the lowest-scoring valid tracklet.

## 5.4 Acceptance Criteria
- RMS position < 1.0″  
- SNR ≥ 5  
- Rate-fit within 50% of predicted  
- Magnitude variation < 1 mag  
- No severe flags  

## 5.5 Output
```json
{
  "status": "ok",
  "tracklet": [
    {"frame": 1, "ra": "...", "dec": "...", "snr": "..."},
    {"frame": 2, "..."}
  ],
  "fitted_motion": {
    "dra_dt": "...",
    "ddec_dt": "...",
    "rms_arcsec": 0.42
  }
}
```

---

# 6. Measurement Step (Accurate Centroid, Photometry, Uncertainty)

### 6.1 Centroid Refinement
Recompute centroid using a 2D Gaussian or PSF model; estimate uncertainty.

### 6.2 Positional Uncertainty
Combine solver RMS and centroid error.

### 6.3 Photometry
Calibrate magnitudes and compute magnitude uncertainty using SNR and a systematic floor.

---

# 7. ADES Record Construction

One ADES record per exposure:

```xml
<obs>
  <tmpDesig>P11abcd</tmpDesig>
  <obsTime>2025-11-23T03:14:15.123Z</obsTime>
  <ra>150.123456</ra>
  <dec>+02.345678</dec>
  <sigRa>0.42</sigRa>
  <sigDec>0.42</sigDec>
  <mag>19.23</mag>
  <magSigma>0.08</magSigma>
  <band>R</band>
  <stn>XXX</stn>
  <cat>Gaia DR3</cat>
  <mode>CCD</mode>
  <remarks>none</remarks>
</obs>
```

# 8. Quality Assurance & Rejection Logic
Reject detections with low SNR, high uncertainty, edge proximity, or inconsistent motion. Reject whole submissions if too few good detections remain or tracklet RMS is excessive.

---

# 9. MPC Submission Packaging
Submit one ADES file per target per night. Archive logs, ADES file, and QA metrics.

---

# 10. Data Flow Diagram

```
FITS
  ↓
Solve (WCS)
  ↓
Source Detection
  ↓
NEOCP Association
  ↓
Centroid Refinement
  ↓
Photometry
  ↓
Compute Uncertainties
  ↓
Build ADES Records
  ↓
Submit to MPC
```

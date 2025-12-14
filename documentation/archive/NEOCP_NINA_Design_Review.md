
# Automated NEOCP Tracking with N.I.N.A
## Design Review & Recommendations

## 1. System Overview
This document describes recommendations for an automated Near-Earth Object Confirmation Page (NEOCP)
follow-up system integrated with **N.I.N.A** for telescope control, **JPL Horizons** for ephemerides,
and a custom astrometric and MPC reporting pipeline.

The proposed architecture is fundamentally sound and closely aligned with professional small-body
follow-up practices. The guidance below focuses on improving accuracy, robustness, and scientific return.

---

## 2. Ephemeris Strategy (Critical)
**Recommendation:** Always use authoritative ephemerides (JPL Horizons or MPC) during operations.

Avoid locally propagating orbital elements unless absolutely necessary. Horizons automatically applies:
- Light-time correction
- Aberration
- Topocentric parallax
- Precession and nutation
- Planetary perturbations

Re-query ephemerides:
- Before slewing
- After each exposure block
- Whenever uncertainty is large or time has elapsed

This is essential for short-arc NEOCP objects.

---

## 3. Target Prioritization
MPC priority alone is insufficient.

Use a dynamic scoring model incorporating:
- Altitude / airmass
- Time-to-set
- Apparent motion rate
- Positional uncertainty
- Lunar separation
- Arc-extension value

Example conceptual score:
```
Score =
  w1 * MPC_priority
+ w2 * altitude
+ w3 * time_remaining
+ w4 * (1 / motion_rate)
+ w5 * uncertainty_penalty
+ w6 * arc_extension_value
```

This ensures the system observes the *best* target at the *right time*.

---

## 4. Slew and Acquisition Strategy
Use a two-stage acquisition approach:

1. Slew to predicted position
2. Take a short confirmation exposure (5–10 s)
3. Plate solve
4. Verify offset vs prediction
5. Refine pointing if necessary

This avoids wasting time on diverged ephemerides or pointing errors.

---

## 5. Exposure Strategy for Fast Movers
For fast-moving NEOs:
- Use motion-compensated tracking when supported
- Otherwise, use short exposures
- Fit streaks when trailing is present

Astrometry remains valid if streak midpoints are measured at mid-exposure time.

---

## 6. Plate Solving and Astrometry
##DO NOT IMPLEMENT##
Do not rely solely on N.I.N.A plate-solve metadata for science.

Use a dedicated astrometric solver:
- ASTAP (THIS SYSTEM ALREADY USES ASTAP THROUGH NINA)
- Astrometry.net
- Siril

Ensure a full WCS solution (including distortion terms) before centroiding.

---

## 7. Object Detection and Centroiding
Recommended pipeline:
1. Solve WCS
2. Detect and subtract stars
3. Search near predicted position
4. Fit Gaussian (slow movers) or streak model (fast movers)

Star subtraction significantly improves centroid accuracy and reliability.

---

## 8. MPC Report Generation
Requirements:
- Strict 80-column formatting
- Mid-exposure UTC timestamps
- Correct observatory code

Automate:
- Formatting validation
- Timestamp checks
- Residual filtering (reject >2–3σ)

Optional: sanity-check using a local orbit fit (e.g., Find_Orb).

---

## 9. Automation Robustness
##DO NOT IMPLEMENT##
Common failure modes:
- Plate-solve hangs
- Autofocus interruptions
- Meridian flips
- Device disconnects

Mitigations:
- Watchdog timers
- API timeouts and retries
- Explicit state machine (IDLE, SLEWING, IMAGING, ERROR)

---

## 10. Observation Scheduling
##DO NOT IMPLEMENT##
Prefer interleaved observations:
```
A → B → A → C → A → B
```
instead of completing one target at a time.

This improves orbit quality and scientific value.

---

## 11. Horizons API Fit
Horizons directly supports this system by accepting:
- Object ID
- Observatory location (lat/lon/elev)
- Time or time range

It returns:
- Topocentric RA/Dec
- RA/Dec rates
- Uncertainty
- Altitude / airmass
- Solar elongation
- Predicted magnitude

Horizons should be treated as a **core dependency**.

---

## 12. Final Assessment
With dynamic ephemerides, robust prioritization, science-grade astrometry,
and defensive automation, this system approaches professional robotic NEO
follow-up capability.

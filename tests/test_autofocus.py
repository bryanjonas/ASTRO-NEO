"""Test-driven validation of the autofocus math: HFR measurement scales
sensibly with injected blur, and the V-curve fit recovers a known minimum."""

import sys

sys.path.insert(0, "/app")

import numpy as np

from app.services.autofocus import FocusSample, fit_v_curve_minimum, measure_hfr

failures = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


def make_synthetic_frame(sigma_px, n_stars=6, size=200, seed=0):
    rng = np.random.default_rng(seed)
    frame = rng.normal(loc=100.0, scale=5.0, size=(size, size))  # sky background + noise
    margin = int(sigma_px * 6) + 5
    for _ in range(n_stars):
        x0 = rng.uniform(margin, size - margin)
        y0 = rng.uniform(margin, size - margin)
        amplitude = rng.uniform(3000, 6000)
        yy, xx = np.mgrid[0:size, 0:size]
        frame += amplitude * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma_px**2))
    return frame


# --- Test 1: HFR increases monotonically with injected blur ---
sigmas = [1.5, 3.0, 5.0, 8.0]
measured = []
for sigma in sigmas:
    frame = make_synthetic_frame(sigma, seed=42)
    hfr, count = measure_hfr(frame, fwhm_estimate=4.0)
    measured.append(hfr)
    check(f"sigma={sigma}: stars detected (count={count})", count >= 3)
    check(f"sigma={sigma}: HFR measured (got {hfr})", hfr is not None and hfr > 0)

check(
    f"HFR increases monotonically with injected sigma ({measured})",
    all(measured[i] < measured[i + 1] for i in range(len(measured) - 1) if measured[i] is not None and measured[i+1] is not None),
)

# --- Test 2: V-curve fit recovers a known minimum ---
true_min_position = 3700
a_coeff = 0.002
positions = [3500, 3600, 3650, 3700, 3750, 3800, 3900]
samples = [
    FocusSample(position=p, median_hfr=a_coeff * (p - true_min_position) ** 2 + 1.2, star_count=10)
    for p in positions
]
recovered = fit_v_curve_minimum(samples)
check(
    f"V-curve minimum recovered (true={true_min_position}, got={recovered})",
    recovered is not None and abs(recovered - true_min_position) <= 2,
)

# --- Test 3: fit correctly refuses data with no real minimum (monotonic decrease) ---
bad_samples = [FocusSample(position=p, median_hfr=10.0 - 0.001 * p, star_count=10) for p in positions]
bad_result = fit_v_curve_minimum(bad_samples)
check(f"Fit returns None for concave-down/monotonic data (got {bad_result})", bad_result is None)

# --- Test 4: fit refuses too few valid samples ---
few_samples = [FocusSample(position=3700, median_hfr=1.0, star_count=10)]
check("Fit returns None with <3 valid samples", fit_v_curve_minimum(few_samples) is None)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):", failures)
    sys.exit(1)
else:
    print("All checks passed.")

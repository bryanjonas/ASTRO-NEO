#!/usr/bin/env python3

"""
Test progressive plate solving with a real FITS file from tonight.

This script verifies that the progressive solve strategy works correctly:
- Attempts 0.2° → 0.3° → 0.4° search radius
- With timeouts 45s → 60s → 90s
- Stops on first successful solve
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.solver import solve_fits, SolveError


def solve_with_progressive_radius(
    *,
    fits_path: str | Path,
    ra_hint: float,
    dec_hint: float,
    base_radius_deg: float,
    downsample: int | None,
    sigma: float | None,
    scale_low_arcsec: float | None,
    scale_high_arcsec: float | None,
    max_radius_deg: float = 2.0,
    timeout_seconds: float | None = None,
    radius_steps: list[float] | None = None,
    timeout_steps: list[int | None] | None = None,
) -> tuple[dict[str, Any], float]:
    """
    Progressive solve strategy (copied from SequentialCaptureService).
    """
    if radius_steps:
        radii = [float(step) for step in radius_steps]
    else:
        radii = []
        radius = max(0.1, float(base_radius_deg))
        cap = max(radius, float(max_radius_deg))
        radii.append(radius)
        for factor in (1.5, 2.0):
            next_radius = radius * factor
            if next_radius < cap:
                radii.append(next_radius)
        while radii[-1] < cap:
            next_radius = min(cap, radii[-1] * 2.0)
            if next_radius <= radii[-1]:
                break
            radii.append(next_radius)
    if timeout_steps:
        timeouts = list(timeout_steps)
    else:
        base_timeout = timeout_seconds or 120.0
        timeouts = []
        if base_timeout:
            factors = [0.5, 0.75, 1.0]
            for idx in range(len(radii)):
                factor = factors[idx] if idx < len(factors) else 1.0
                timeouts.append(max(30, int(base_timeout * factor)))
        else:
            timeouts = [None for _ in radii]

    last_exc: Exception | None = None
    for attempt_radius, attempt_timeout in zip(radii, timeouts, strict=False):
        try:
            print(f"\n▶ Attempting solve: radius={attempt_radius:.2f}°, timeout={attempt_timeout}s")
            start_time = time.time()
            result = solve_fits(
                fits_path=fits_path,
                ra_hint=ra_hint,
                dec_hint=dec_hint,
                radius_deg=attempt_radius,
                downsample=downsample,
                sigma=sigma,
                scale_low_arcsec=scale_low_arcsec,
                scale_high_arcsec=scale_high_arcsec,
                timeout=attempt_timeout,
            )
            elapsed = time.time() - start_time
            print(f"✓ SOLVED in {elapsed:.1f}s with radius={attempt_radius:.2f}°")
            return result, attempt_radius
        except SolveError as exc:
            elapsed = time.time() - start_time
            last_exc = exc
            print(f"✗ Failed with radius={attempt_radius:.2f}° after {elapsed:.1f}s: {exc}")

    if last_exc:
        raise last_exc
    raise SolveError("Plate solve failed with no attempts")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Test progressive solve with a FITS file")
    parser.add_argument("fits_path", help="Path to FITS file to solve")
    parser.add_argument("--ra", type=float, required=True, help="RA hint in degrees")
    parser.add_argument("--dec", type=float, required=True, help="Dec hint in degrees")
    parser.add_argument("--scale-low", type=float, help="Scale low bound (arcsec/px)")
    parser.add_argument("--scale-high", type=float, help="Scale high bound (arcsec/px)")
    parser.add_argument("--downsample", type=int, help="Downsample factor")
    args = parser.parse_args()

    fits_path = Path(args.fits_path)
    if not fits_path.exists():
        print(f"Error: FITS file not found: {fits_path}")
        return 1

    print("=" * 70)
    print(f"Testing Progressive Solve Strategy")
    print("=" * 70)
    print(f"FITS file: {fits_path}")
    print(f"RA hint:   {args.ra:.6f}°")
    print(f"Dec hint:  {args.dec:.6f}°")
    print(f"Strategy:  0.2° (45s) → 0.3° (60s) → 0.4° (90s)")
    if args.scale_low and args.scale_high:
        print(f"Scale:     {args.scale_low:.3f} - {args.scale_high:.3f} arcsec/px")
    if args.downsample:
        print(f"Downsample: {args.downsample}x")
    print("=" * 70)

    try:
        result, used_radius = solve_with_progressive_radius(
            fits_path=fits_path,
            ra_hint=args.ra,
            dec_hint=args.dec,
            base_radius_deg=0.2,
            downsample=args.downsample,
            sigma=None,
            scale_low_arcsec=args.scale_low,
            scale_high_arcsec=args.scale_high,
            max_radius_deg=0.4,
            timeout_seconds=120.0,
            radius_steps=[0.2, 0.3, 0.4],
            timeout_steps=[45, 60, 90],
        )

        print("\n" + "=" * 70)
        print("SOLVE SUCCESSFUL")
        print("=" * 70)
        sol = result["solution"]
        print(f"Solved RA:     {sol['ra_deg']:.6f}°")
        print(f"Solved Dec:    {sol['dec_deg']:.6f}°")
        print(f"Used radius:   {used_radius:.2f}°")
        if sol.get("pixscale"):
            print(f"Pixel scale:   {sol['pixscale']:.3f} arcsec/px")

        # Calculate offset from hint
        import math
        ra1, dec1 = math.radians(args.ra), math.radians(args.dec)
        ra2, dec2 = math.radians(sol['ra_deg']), math.radians(sol['dec_deg'])
        dra = ra2 - ra1
        ddec = dec2 - dec1
        a = math.sin(ddec / 2) ** 2 + math.cos(dec1) * math.cos(dec2) * math.sin(dra / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        sep_arcsec = math.degrees(c) * 3600.0
        print(f"Offset:        {sep_arcsec:.1f}\"")
        print("=" * 70)

        return 0

    except SolveError as e:
        print("\n" + "=" * 70)
        print("SOLVE FAILED")
        print("=" * 70)
        print(f"Error: {e}")
        print("=" * 70)
        return 1
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

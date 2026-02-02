#!/usr/bin/env python3

"""
Test mount sync functionality to fix pointing errors.

This script demonstrates how to:
1. Plate solve an image to find actual pointing
2. Sync the mount to the solved coordinates
3. Improve pointing accuracy for future slews
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.nina_client import NinaBridgeService
from app.services.solver import solve_fits


def main() -> int:
    parser = argparse.ArgumentParser(description="Plate solve and sync mount")
    parser.add_argument("fits_path", help="Path to FITS file to solve")
    parser.add_argument("--downsample", type=int, default=4, help="Downsample factor")
    parser.add_argument("--sync", action="store_true", help="Actually sync the mount (default: dry run)")
    args = parser.parse_args()

    fits_path = Path(args.fits_path)
    if not fits_path.exists():
        print(f"Error: FITS file not found: {fits_path}")
        return 1

    print("=" * 70)
    print("Mount Sync Test - Plate Solve + Sync")
    print("=" * 70)
    print(f"FITS: {fits_path}")
    print(f"Mode: {'SYNC (will update mount)' if args.sync else 'DRY RUN (no changes)'}")
    print("=" * 70)
    print("")

    # Step 1: Plate solve to find actual pointing
    print("Step 1: Plate solving image (blind solve)...")
    try:
        result = solve_fits(
            fits_path=fits_path,
            ra_hint=None,  # Blind solve
            dec_hint=None,
            radius_deg=None,
            downsample=args.downsample,
            scale_low_arcsec=2.0,
            scale_high_arcsec=3.0,
            timeout=180
        )
        sol = result['solution']
        print(f"✓ Solved successfully")
        print(f"  RA:  {sol['ra_deg']:.6f}° ({sol['ra_deg']/15:.6f}h)")
        print(f"  Dec: {sol['dec_deg']:.6f}°")
        print(f"  Pixel scale: {sol.get('pixscale', 'N/A')} arcsec/px")
        print("")
    except Exception as e:
        print(f"✗ Plate solve failed: {e}")
        return 1

    # Step 2: Get current mount position
    print("Step 2: Getting current mount position from NINA...")
    try:
        nina = NinaBridgeService()
        mount_info = nina.mount_info()
        mount_ra = mount_info.get('ra_deg')
        mount_dec = mount_info.get('dec_deg')

        if mount_ra is not None and mount_dec is not None:
            print(f"  Mount thinks it's at:")
            print(f"    RA:  {mount_ra:.6f}°")
            print(f"    Dec: {mount_dec:.6f}°")

            # Calculate offset
            import math
            ra1, dec1 = math.radians(mount_ra), math.radians(mount_dec)
            ra2, dec2 = math.radians(sol['ra_deg']), math.radians(sol['dec_deg'])
            dra = ra2 - ra1
            ddec = dec2 - dec1
            a = math.sin(ddec/2)**2 + math.cos(dec1)*math.cos(dec2)*math.sin(dra/2)**2
            c = 2*math.asin(math.sqrt(a))
            sep_deg = math.degrees(c)
            sep_arcsec = sep_deg * 3600

            print(f"  Offset: {sep_deg:.4f}° = {sep_arcsec:.1f}\"")
            print("")
        else:
            print("  Warning: Could not read mount position")
            print("")
    except Exception as e:
        print(f"  Warning: Could not connect to NINA: {e}")
        print("")

    # Step 3: Sync mount (if requested)
    if args.sync:
        print("Step 3: Syncing mount to solved coordinates...")
        try:
            response = nina.sync_mount(ra_deg=sol['ra_deg'], dec_deg=sol['dec_deg'])
            print(f"✓ Mount synced successfully: {response}")
            print("")
            print("The mount's pointing model has been updated.")
            print("Future slews should be more accurate.")
        except Exception as e:
            print(f"✗ Sync failed: {e}")
            return 1
    else:
        print("Step 3: SKIPPED (dry run mode)")
        print("")
        print("To actually sync the mount, run with --sync flag:")
        print(f"  python3 {Path(__file__).name} {fits_path} --sync")

    print("=" * 70)
    print("Done")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

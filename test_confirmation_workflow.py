#!/usr/bin/env python3
"""Test the per-exposure confirmation workflow with real NINA."""

import sys
import time
from datetime import datetime

# Add app to path
sys.path.insert(0, "/home/bryan/ASTRO-NEO")

from app.services.nina_client import NinaBridgeService

def test_confirmation_workflow():
    """Test a single confirmation + science exposure sequence."""

    bridge = NinaBridgeService(base_url="http://localhost:1889/api")

    print("=" * 80)
    print("TESTING PER-EXPOSURE CONFIRMATION WORKFLOW")
    print("=" * 80)
    print()

    # Test parameters
    target_name = "WORKFLOW-TEST"
    filter_name = "L"
    confirmation_exp = 2.0  # Short confirmation
    science_exp = 3.0  # Science exposure

    print(f"Target: {target_name}")
    print(f"Confirmation exposure: {confirmation_exp}s")
    print(f"Science exposure: {science_exp}s")
    print()

    # Step 1: Take confirmation exposure
    print("-" * 80)
    print("STEP 1: Confirmation Exposure")
    print("-" * 80)
    start_time = datetime.utcnow()
    print(f"Start time: {start_time.strftime('%H:%M:%S')}")

    try:
        print(f"Requesting {confirmation_exp}s confirmation exposure...")
        confirm_result = bridge.start_exposure(
            filter_name=filter_name,
            binning=2,
            exposure_seconds=confirmation_exp,
            target=f"{target_name}-CONFIRM",
        )
        end_time = datetime.utcnow()
        elapsed = (end_time - start_time).total_seconds()

        print(f"End time: {end_time.strftime('%H:%M:%S')}")
        print(f"Elapsed: {elapsed:.1f}s")
        print()

        # Check platesolve
        platesolve = confirm_result.get("platesolve")
        if platesolve:
            success = platesolve.get("Success", False)
            solve_time = platesolve.get("SolveTime", "N/A")
            print(f"Plate solve: {'SUCCESS' if success else 'FAILED'}")
            print(f"Solve time: {solve_time}")

            if success:
                coords = platesolve.get("Coordinates", {})
                ra = coords.get("RADegrees")
                dec = coords.get("DECDegrees")
                if ra and dec:
                    print(f"Solved position: RA {ra:.5f}°, Dec {dec:.5f}°")
        else:
            print("No plate solve result returned")

        print()

    except Exception as exc:
        print(f"ERROR: Confirmation exposure failed: {exc}")
        return False

    # Wait a moment before science exposure
    time.sleep(1)

    # Step 2: Take science exposure
    print("-" * 80)
    print("STEP 2: Science Exposure")
    print("-" * 80)
    start_time = datetime.utcnow()
    print(f"Start time: {start_time.strftime('%H:%M:%S')}")

    try:
        print(f"Requesting {science_exp}s science exposure...")
        science_result = bridge.start_exposure(
            filter_name=filter_name,
            binning=1,
            exposure_seconds=science_exp,
            target=target_name,
        )
        end_time = datetime.utcnow()
        elapsed = (end_time - start_time).total_seconds()

        print(f"End time: {end_time.strftime('%H:%M:%S')}")
        print(f"Elapsed: {elapsed:.1f}s")
        print()

        # Check platesolve
        platesolve = science_result.get("platesolve")
        if platesolve:
            success = platesolve.get("Success", False)
            solve_time = platesolve.get("SolveTime", "N/A")
            print(f"Plate solve: {'SUCCESS' if success else 'FAILED'}")
            print(f"Solve time: {solve_time}")

            if success:
                coords = platesolve.get("Coordinates", {})
                ra = coords.get("RADegrees")
                dec = coords.get("DECDegrees")
                if ra and dec:
                    print(f"Solved position: RA {ra:.5f}°, Dec {dec:.5f}°")
        else:
            print("No plate solve result returned")

        print()

    except Exception as exc:
        print(f"ERROR: Science exposure failed: {exc}")
        return False

    print("=" * 80)
    print("WORKFLOW TEST COMPLETE")
    print("=" * 80)
    return True

if __name__ == "__main__":
    success = test_confirmation_workflow()
    sys.exit(0 if success else 1)

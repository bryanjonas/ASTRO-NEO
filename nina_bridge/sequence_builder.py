from typing import Any, TypedDict


class TargetSpec(TypedDict, total=False):
    """Target specification for multi-target sequences."""
    name: str
    ra_deg: float
    dec_deg: float
    filter_name: str
    binning: int
    exposure_seconds: float
    count: int
    gain: int | None
    offset: int | None


def build_nina_sequence(
    name: str,
    target: str | None,
    count: int,
    filter_name: str,
    binning: int,
    exposure_seconds: float,
    tracking_mode: str | None = None,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
) -> dict[str, Any]:
    """
    Build the Advanced Sequencer payload expected by NINA for a SINGLE target.

    Returns a SequenceRootContainer object (not an array) that contains
    Start, Target, and End containers following NINA's actual format.

    For multi-target sequences, use build_multi_target_sequence() instead.
    """
    target_spec: TargetSpec = {
        "name": target or name,
        "ra_deg": ra_deg or 0.0,
        "dec_deg": dec_deg or 0.0,
        "filter_name": filter_name,
        "binning": binning,
        "exposure_seconds": exposure_seconds,
        "count": count,
        "gain": -1,
        "offset": -1,
    }
    return build_multi_target_sequence(name, [target_spec])


def build_multi_target_sequence(
    name: str,
    targets: list[TargetSpec],
) -> dict[str, Any]:
    """
    Build the Advanced Sequencer payload for a SINGLE target with multiple exposures.

    NOTE: Despite the name, this function is designed to handle ONE target at a time.
    The 'targets' parameter accepts a list for API compatibility, but only the first
    target is processed. Use this in a loop to process multiple targets sequentially.

    Returns a SequenceRootContainer object that contains:
    - Start container
    - Multiple DeepSkyObjectContainer items (ONE PER EXPOSURE)
    - End container

    Each exposure gets its own container with:
    - Plate solve/center step
    - Optional filter switch
    - Single exposure instruction

    IMPORTANT: For a target that needs N exposures, we create N separate containers
    (one per exposure) to enable motion tracking by re-centering before each exposure.
    """
    # Only process the first target - targets are handled one at a time
    if not targets:
        raise ValueError("At least one target must be provided")

    target = targets[0]
    count = target.get("count", 1)

    # Build the items list for the root container
    items = []

    # Start Container
    items.append({
        "$type": "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
        "Name": "Start",
        "Strategy": {
            "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
        },
        "Conditions": [],
        "Items": [],
        "Triggers": [],
        "IsExpanded": True,
        "Parent": None,
        "ErrorBehavior": 0,
        "Attempts": 1
    })

    # Add a DeepSkyObjectContainer for each exposure of this target
    # If target needs N exposures, we create N separate containers
    for i in range(count):
        # Create a single-exposure version for this frame
        single_exposure_target = target.copy()
        single_exposure_target["count"] = 1
        single_exposure_target["exposure_index"] = i + 1
        items.append(_build_target_container(single_exposure_target))

    # End Container
    items.append({
        "$type": "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
        "Name": "End",
        "Strategy": {
            "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
        },
        "Conditions": [],
        "Items": [],
        "Triggers": [],
        "IsExpanded": True,
        "Parent": None,
        "ErrorBehavior": 0,
        "Attempts": 1
    })

    # Build the root container
    root = {
        "$type": "NINA.Sequencer.Container.SequenceRootContainer, NINA.Sequencer",
        "Name": name,
        "Strategy": {
            "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
        },
        "Conditions": [],
        "Items": items,
        "Triggers": [],
        "IsExpanded": True,
        "Parent": None,
        "ErrorBehavior": 0,
        "Attempts": 1
    }

    return root


def _build_target_container(target: TargetSpec) -> dict[str, Any]:
    """Build a DeepSkyObjectContainer for a single target exposure."""
    target_items = []

    # Get target name and exposure index
    target_name = target["name"]
    exposure_index = target.get("exposure_index", 1)

    # Include exposure index in the container name for tracking
    container_name = f"{target_name} #{exposure_index}" if exposure_index > 1 else target_name

    # Add centering step
    target_items.append({
        "$type": "NINA.Sequencer.SequenceItem.Platesolving.Center, NINA.Sequencer",
        "Name": f"Center {target_name}",
        "Inherited": True,
        "Coordinates": {
            "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry",
            "RAHours": 0,
            "RAMinutes": 0,
            "RASeconds": 0,
            "DecDegrees": 0,
            "DecMinutes": 0,
            "DecSeconds": 0
        },
        "Parent": None,
        "ErrorBehavior": 0,
        "Attempts": 1
    })

    # Add filter switch if specified
    filter_name = target.get("filter_name", "L")
    if filter_name:
        target_items.append({
            "$type": "NINA.Sequencer.SequenceItem.Utility.SwitchFilter, NINA.Sequencer",
            "Name": f"Switch Filter to {filter_name}",
            "Filter": {
                "Name": filter_name
            },
            "Parent": None,
            "ErrorBehavior": 0,
            "Attempts": 1
        })

    # Add exposure instruction - ALWAYS ONE EXPOSURE
    exposure_seconds = target.get("exposure_seconds", 60.0)
    binning = target.get("binning", 1)
    gain = target.get("gain", -1)
    offset = target.get("offset", -1)

    target_items.append({
        "$type": "NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer",
        "Name": f"Take 1x{exposure_seconds:.1f}s",
        "ExposureTime": exposure_seconds,
        "Gain": gain,
        "Offset": offset,
        "Binning": {
            "$type": "NINA.Core.Model.Equipment.BinningMode, NINA.Core",
            "X": binning,
            "Y": binning
        },
        "ImageType": "LIGHT",
        "ExposureCount": 0,  # NINA uses 0-based count (0 = 1 exposure)
        "Parent": None,
        "ErrorBehavior": 0,
        "Attempts": 1
    })

    # Convert coordinates
    ra_deg = target.get("ra_deg", 0.0)
    dec_deg = target.get("dec_deg", 0.0)

    # Convert RA from decimal degrees to hours/minutes/seconds
    ra_hours_decimal = ra_deg / 15.0  # Convert degrees to hours
    ra_hours = int(ra_hours_decimal)
    ra_minutes_decimal = (ra_hours_decimal - ra_hours) * 60
    ra_minutes = int(ra_minutes_decimal)
    ra_seconds = (ra_minutes_decimal - ra_minutes) * 60

    # Convert Dec from decimal degrees to degrees/minutes/seconds
    dec_sign = 1 if dec_deg >= 0 else -1
    dec_abs = abs(dec_deg)
    dec_degrees = int(dec_abs) * dec_sign
    dec_minutes_decimal = (dec_abs - int(dec_abs)) * 60
    dec_minutes = int(dec_minutes_decimal)
    dec_seconds = (dec_minutes_decimal - dec_minutes) * 60

    return {
        "$type": "NINA.Sequencer.Container.DeepSkyObjectContainer, NINA.Sequencer",
        "Name": container_name,
        "Strategy": {
            "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
        },
        "Target": {
            "$type": "NINA.Astrometry.InputTarget, NINA.Astrometry",
            "Expanded": True,
            "TargetName": target_name,
            "Rotation": 0.0,
            "InputCoordinates": {
                "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry",
                "RAHours": ra_hours,
                "RAMinutes": ra_minutes,
                "RASeconds": ra_seconds,
                "DecDegrees": dec_degrees,
                "DecMinutes": dec_minutes,
                "DecSeconds": dec_seconds
            }
        },
        "Conditions": [],
        "Items": target_items,
        "Triggers": [],
        "IsExpanded": True,
        "Parent": None,
        "ErrorBehavior": 0,
        "Attempts": 1
    }

from typing import Any

def build_nina_sequence(
    name: str,
    target: str | None,
    count: int,
    filter_name: str,
    binning: int,
    exposure_seconds: float,
    tracking_mode: str | None = None
) -> list[dict[str, Any]]:
    """Build a NINA Advanced Sequencer JSON structure."""
    
    # Basic structure of a NINA sequence
    sequence = [
        # {"GlobalTriggers": []},
        {
            "Name": "Start_Container",
            "Items": [],
            "Triggers": [],
            "Conditions": [],
            "$type": "NINA.Sequencer.Container.SequenceContainer, NINA.Sequencer"
        },
        {
            "Name": "Targets_Container",
            "Items": [],
            "Triggers": [],
            "Conditions": [],
            "$type": "NINA.Sequencer.Container.SequenceContainer, NINA.Sequencer"
        },
        {
            "Name": "End_Container",
            "Items": [],
            "Triggers": [],
            "Conditions": [],
            "$type": "NINA.Sequencer.Container.SequenceContainer, NINA.Sequencer"
        }
    ]

    # Create the Target Container (DeepSkyObjectContainer)
    target_container = {
        "$type": "NINA.Sequencer.Container.DeepSkyObjectContainer, NINA.Sequencer",
        "Name": target or name,
        "Enabled": True,
        "Items": [],
        "Triggers": [],
        "Conditions": [],
        "Target": {
            "Name": target or name,
            # We might need coordinates here if we want it to slew/center
            # For now, we assume the mount is already there or we just want to image
        }
    }

    # Add Filter Switch Instruction
    if filter_name:
        target_container["Items"].append({
            "$type": "NINA.Sequencer.SequenceItem.Utility.SwitchFilter, NINA.Sequencer",
            "Filter": {"Name": filter_name}, 
            "Name": f"Switch Filter to {filter_name}"
        })

    # Add Exposure Instruction (SmartExposure or TakeExposure)
    # SmartExposure is often safer as it handles looping
    target_container["Items"].append({
        "$type": "NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer",
        "ExposureTime": exposure_seconds or 1.0,
        "ImageCount": count,
        "Binning": binning,
        "Name": "Take Exposure"
    })

    # Add to Targets_Container (index 2 in the list)
    # sequence[2]["Items"].append(target_container)

    return sequence

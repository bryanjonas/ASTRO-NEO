# Building a NINA Advanced Sequencer Payload via API

## Overview

This document describes the **actual, tested format** for generating NINA Advanced Sequencer payloads via the `/v2/api/sequence/load` endpoint.

**IMPORTANT**: NINA expects a **single root object** (SequenceRootContainer), NOT an array of containers.

## Required NINA Capabilities

To run automated sequences, ensure:

* **Advanced Sequencer** is enabled (NINA ≥ 2.0)
* A working **plate solver** is configured
* The **active equipment profile** has all required devices connected:
  * Mount/Telescope (required for centering)
  * Camera (required)
  * Filter wheel (optional, only if using filter switching)
  * Focuser (optional)

## Root Structure

The `/sequence/load` endpoint expects a **SequenceRootContainer** object with the following structure:

```json
{
  "$type": "NINA.Sequencer.Container.SequenceRootContainer, NINA.Sequencer",
  "Name": "Sequence Name",
  "Strategy": {
    "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
  },
  "Conditions": [],
  "Items": [
    // Array of container items (Start, Targets, End)
  ],
  "Triggers": [],
  "IsExpanded": true,
  "Parent": null,
  "ErrorBehavior": 0,
  "Attempts": 1
}
```

## Container Types

### SequentialContainer (for Start/End)

```json
{
  "$type": "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
  "Name": "Start",
  "Strategy": {
    "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
  },
  "Conditions": [],
  "Items": [],
  "Triggers": [],
  "IsExpanded": true,
  "Parent": null,
  "ErrorBehavior": 0,
  "Attempts": 1
}
```

### DeepSkyObjectContainer (for Targets)

```json
{
  "$type": "NINA.Sequencer.Container.DeepSkyObjectContainer, NINA.Sequencer",
  "Name": "Target Name",
  "Strategy": {
    "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
  },
  "Target": {
    "$type": "NINA.Astrometry.InputTarget, NINA.Astrometry",
    "Expanded": true,
    "TargetName": "Target Name",
    "Rotation": 0.0,
    "InputCoordinates": {
      "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry",
      "RAHours": 21,
      "RAMinutes": 44,
      "RASeconds": 48.408,
      "DecDegrees": -11,
      "DecMinutes": 8,
      "DecSeconds": 52.08
    }
  },
  "Conditions": [],
  "Items": [
    // Array of sequence items (Center, SwitchFilter, TakeExposure, etc.)
  ],
  "Triggers": [],
  "IsExpanded": true,
  "Parent": null,
  "ErrorBehavior": 0,
  "Attempts": 1
}
```

## Sequence Items

### Center/Plate Solve

```json
{
  "$type": "NINA.Sequencer.SequenceItem.Platesolving.Center, NINA.Sequencer",
  "Name": "Center Target",
  "Inherited": true,
  "Coordinates": {
    "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry",
    "RAHours": 0,
    "RAMinutes": 0,
    "RASeconds": 0,
    "DecDegrees": 0,
    "DecMinutes": 0,
    "DecSeconds": 0
  },
  "Parent": null,
  "ErrorBehavior": 0,
  "Attempts": 1
}
```

**Note**: When `Inherited: true`, coordinates are inherited from the parent DeepSkyObjectContainer.

### Switch Filter

```json
{
  "$type": "NINA.Sequencer.SequenceItem.Utility.SwitchFilter, NINA.Sequencer",
  "Name": "Switch Filter to L",
  "Filter": {
    "Name": "L"
  },
  "Parent": null,
  "ErrorBehavior": 0,
  "Attempts": 1
}
```

### Take Exposure

```json
{
  "$type": "NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer",
  "Name": "Take 3x60.0s",
  "ExposureTime": 60.0,
  "Gain": -1,
  "Offset": -1,
  "Binning": {
    "$type": "NINA.Core.Model.Equipment.BinningMode, NINA.Core",
    "X": 1,
    "Y": 1
  },
  "ImageType": "LIGHT",
  "ExposureCount": 2,
  "Parent": null,
  "ErrorBehavior": 0,
  "Attempts": 1
}
```

**Important Notes**:
- `ExposureCount` is **0-based**: 0 = 1 exposure, 2 = 3 exposures, etc.
- `Gain: -1` and `Offset: -1` means use camera defaults
- `Binning` must be an object with `X` and `Y` properties, not a simple integer

## Required Fields

All containers and items MUST include:

| Field | Type | Description |
|-------|------|-------------|
| `$type` | string | Full .NET type name with assembly |
| `Strategy` | object | Execution strategy (containers only) |
| `Parent` | null | Parent reference (set to null for API) |
| `ErrorBehavior` | integer | Error handling: 0 = stop on error |
| `Attempts` | integer | Number of retry attempts |
| `IsExpanded` | boolean | UI expansion state (containers only) |

## Coordinate Conversion

NINA uses **Hours/Minutes/Seconds** format for coordinates, not decimal degrees.

### RA Conversion (Decimal Degrees → HMS)

```python
ra_hours_decimal = ra_deg / 15.0
ra_hours = int(ra_hours_decimal)
ra_minutes_decimal = (ra_hours_decimal - ra_hours) * 60
ra_minutes = int(ra_minutes_decimal)
ra_seconds = (ra_minutes_decimal - ra_minutes) * 60
```

### Dec Conversion (Decimal Degrees → DMS)

```python
dec_sign = 1 if dec_deg >= 0 else -1
dec_abs = abs(dec_deg)
dec_degrees = int(dec_abs) * dec_sign
dec_minutes_decimal = (dec_abs - int(dec_abs)) * 60
dec_minutes = int(dec_minutes_decimal)
dec_seconds = (dec_minutes_decimal - dec_minutes) * 60
```

## Complete Working Example

Here's a minimal working sequence that has been tested and confirmed working:

```json
{
  "$type": "NINA.Sequencer.Container.SequenceRootContainer, NINA.Sequencer",
  "Name": "API Test Sequence",
  "Strategy": {
    "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
  },
  "Conditions": [],
  "Items": [
    {
      "$type": "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
      "Name": "Start",
      "Strategy": {
        "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
      },
      "Conditions": [],
      "Items": [],
      "Triggers": [],
      "IsExpanded": true,
      "Parent": null,
      "ErrorBehavior": 0,
      "Attempts": 1
    },
    {
      "$type": "NINA.Sequencer.Container.DeepSkyObjectContainer, NINA.Sequencer",
      "Name": "FAKE-01",
      "Strategy": {
        "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
      },
      "Target": {
        "$type": "NINA.Astrometry.InputTarget, NINA.Astrometry",
        "Expanded": true,
        "TargetName": "FAKE-01",
        "Rotation": 0.0,
        "InputCoordinates": {
          "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry",
          "RAHours": 21,
          "RAMinutes": 44,
          "RASeconds": 48.408,
          "DecDegrees": -11,
          "DecMinutes": 8,
          "DecSeconds": 52.08
        }
      },
      "Conditions": [],
      "Items": [
        {
          "$type": "NINA.Sequencer.SequenceItem.Platesolving.Center, NINA.Sequencer",
          "Name": "Center FAKE-01",
          "Inherited": true,
          "Coordinates": {
            "$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry",
            "RAHours": 0,
            "RAMinutes": 0,
            "RASeconds": 0,
            "DecDegrees": 0,
            "DecMinutes": 0,
            "DecSeconds": 0
          },
          "Parent": null,
          "ErrorBehavior": 0,
          "Attempts": 1
        },
        {
          "$type": "NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer",
          "Name": "Take 3x1.0s",
          "ExposureTime": 1.0,
          "Gain": -1,
          "Offset": -1,
          "Binning": {
            "$type": "NINA.Core.Model.Equipment.BinningMode, NINA.Core",
            "X": 1,
            "Y": 1
          },
          "ImageType": "LIGHT",
          "ExposureCount": 2,
          "Parent": null,
          "ErrorBehavior": 0,
          "Attempts": 1
        }
      ],
      "Triggers": [],
      "IsExpanded": true,
      "Parent": null,
      "ErrorBehavior": 0,
      "Attempts": 1
    },
    {
      "$type": "NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer",
      "Name": "End",
      "Strategy": {
        "$type": "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"
      },
      "Conditions": [],
      "Items": [],
      "Triggers": [],
      "IsExpanded": true,
      "Parent": null,
      "ErrorBehavior": 0,
      "Attempts": 1
    }
  ],
  "Triggers": [],
  "IsExpanded": true,
  "Parent": null,
  "ErrorBehavior": 0,
  "Attempts": 1
}
```

## API Usage

```python
import httpx

sequence = build_nina_sequence(...)  # Your sequence dict

response = httpx.post(
    "http://localhost:1888/v2/api/sequence/load",
    json=sequence,
    timeout=30.0
)

result = response.json()
if result.get("Success"):
    print("Sequence loaded successfully!")
else:
    print(f"Error: {result.get('Error')}")
```

## Common Errors

### Error: "Current JsonReader item is not an object: StartArray"

**Cause**: Sending an array `[...]` instead of a single object `{...}`

**Fix**: Return a SequenceRootContainer object, not an array

### Error: "Unable to cast object to SequenceRootContainer"

**Cause**: Root container has wrong `$type`

**Fix**: Use `NINA.Sequencer.Container.SequenceRootContainer, NINA.Sequencer` as root type

## Implementation Reference

See `nina_bridge/sequence_builder.py` for a complete, working implementation that:
- Converts decimal degree coordinates to HMS/DMS format
- Builds proper SequenceRootContainer structure
- Handles optional filter switching
- Creates plate-solve/center instructions
- Configures exposure parameters correctly

## Testing

To test a sequence:

1. Load via API: `POST /v2/api/sequence/load`
2. Verify: `GET /v2/api/sequence/json`
3. Start: `GET /v2/api/sequence/start`
4. Monitor: Check NINA logs at `C:\Users\[User]\AppData\Local\NINA\Logs\`

# Target Scoring & Selection Logic

This document details the heuristics used by ASTRO-NEO to prioritize NEOCP candidates and assign imaging parameters.

## 1. Observability & Filtering

Before a target is scored, it must pass a series of "Go/No-Go" checks. If any check fails, the target is marked `is_observable=False` and will **not** be scheduled, regardless of its score.

### Blocking Criteria
A target is blocked if:
1.  **Weather**: The site's weather sensors (or remote API) report unsafe conditions (rain, high wind, humidity, cloud cover).
2.  **Horizon**: The target's altitude is below the configured minimum (`OBSERVABILITY_MIN_ALTITUDE_DEG`) or blocked by the local horizon mask.
3.  **Sun**: The sun's altitude is above the limit (e.g., -12° for nautical twilight).
4.  **Moon**: The target is too close to the moon (`< OBSERVABILITY_MIN_MOON_SEPARATION_DEG`).
5.  **Window**: The visible duration is shorter than `OBSERVABILITY_MIN_WINDOW_MINUTES`.
6.  **Magnitude**: The target is fainter than `OBSERVABILITY_MAX_VMAG`.
7.  **Stale**: The candidate data is older than `OBSERVABILITY_RECENT_HOURS` (default 24h).
8.  **Missing Data**: RA/Dec coordinates are missing.

## 2. Scoring Heuristic

Eligible targets are ranked by a composite score (0-100) calculated in `app.services.observability`. The score balances visibility quality with urgency.

### Formula
```python
Final Score = 100 * (0.5 * DurationScore + 0.3 * AltitudeScore + 0.2 * UrgencyScore)
```

### Components
1.  **Duration Score** (50% weight):
    -   Measures how long the target is visible tonight relative to the ideal window.
    -   `min(1.0, visible_minutes / OBSERVABILITY_TARGET_WINDOW_MINUTES)`
    -   *Goal: Prioritize targets we can image for a full session.*

2.  **Altitude Score** (30% weight):
    -   Measures the peak altitude of the target during the window.
    -   `min(1.0, max_altitude_deg / 90.0)`
    -   *Goal: Prioritize targets higher in the sky for better seeing/SNR.*

3.  **Urgency Score** (20% weight):
    -   Derived from the MPC's "Score" field (0-100).
    -   `(mpc_score / 100.0)`
    -   *Goal: Break ties using the MPC's assessment of interest/uncertainty.*

## 3. Exposure Strategy (Presets)

Once a target is selected, the system assigns an exposure preset based on the target's magnitude (`Vmag`) and urgency. This logic resides in `app.services.presets`.

### Default Presets
| Preset Name | Max Vmag | Exposure | Count | Binning | Spacing |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Bright** | 16.0 | 60s | 4 | 1x1 | 90s |
| **Medium** | 18.0 | 90s | 5 | 1x1 | 120s |
| **Faint** | >18.0 | 120s | 6 | 2x2 | 180s |

*Note: "Spacing" (`delay_seconds`) is added between exposures to allow the NEO to move sufficiently against the background stars for motion detection.*

### Selection Logic
1.  **Magnitude Match**: The system iterates through presets (sorted by brightness) and selects the first one where `Target Vmag <= Preset Max Vmag`.
2.  **Fallback**: If the target is fainter than all explicit limits (or Vmag is missing), the **Bright** preset is used by default to ensure we get *some* data without over-exposing, though `faint` is the fallback if Vmag is simply very high. (If Vmag is `None`, code defaults to "bright").

### Urgency Modifiers
If a target is highly urgent (`urgency >= 0.7` / MPC Score >= 70), the selected preset is modified to capture data faster:
-   **Exposure Time**: Reduced by 15% (0.85x) to freeze motion/reduce trailing.
-   **Count**: Increased by 2 frames (to ensure tracklet detection).
-   **Spacing**: Reduced by 20% (0.8x).

### Equipment Overrides
The active **Equipment Profile** (stored in `site.yml` or DB) can override preset defaults:
-   **Filters**: If the camera has a specific filter list, the first filter (usually Luminance/Clear) overrides the preset default.
-   **Gain/Offset**: Camera-specific gain/offset settings can be mapped to preset names (e.g., higher gain for "faint" preset).

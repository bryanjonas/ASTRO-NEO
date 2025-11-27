# Dashboard Redesign Recommendations

## 1. Navigation Structure
*   **Current State**: Horizontal tab bar with many items (Overview, Observatory, Equipment, Targets, Exposures, Solver, Association, Reports).
*   **Recommendation**: Switch to a **vertical sidebar navigation**.
    *   **Benefits**: Scales better, allows for grouping (e.g., "Configuration" group for Observatory/Equipment), and provides a more professional "console" aesthetic.
    *   **Implementation**: Use a fixed left sidebar with icon + label for each section.

## 2. Visual Hierarchy & Clutter
*   **Current State**: Heavy use of "glassy" transparent backgrounds (`rgba`), borders, and glow effects.
*   **Recommendation**:
    *   **Solid Backgrounds**: Use solid, distinct background colors (e.g., darker for the page, slightly lighter for panels) instead of transparency to improve contrast and readability.
    *   **Remove Excessive Borders**: Rely on background contrast and whitespace to separate sections rather than borders on every element.
    *   **Simplify "Status"**: Consolidate the multiple small telemetry cards into a unified "System Health" strip or a cleaner grid with less visual "chrome".

## 3. Typography & Spacing
*   **Current State**: Dense information packing with custom `.panel-title` classes.
*   **Recommendation**:
    *   **Increase Padding**: Add more breathing room inside panels.
    *   **Standardize Headings**: Use a standard HTML heading hierarchy (H1/H2/H3) for better semantic structure and consistency.

## 4. Color Palette
*   **Current State**: Multiple accent colors (cyan, purple) used for borders, shadows, and text.
*   **Recommendation**:
    *   **Primary Accent**: Stick to a **single primary accent color** for interactive elements (buttons, active states).
    *   **Semantic Colors**: Use standard semantic colors (green/red/orange) *only* for status indicators (e.g., "Connected", "Error", "Warning").

---

## Further Exploration Findings

### 1. Unnecessary Fields
Based on a review of the current forms, the following fields can be removed or simplified:

*   **Observatory Tab**:
    *   `Weather Sensors` (JSON Textarea): **Simplify**. Since no local sensors are planned, replace this generic JSON field with a specific "Weather API Configuration" form (e.g., API Key, Location) to make setup easier.
    *   `Horizon Mask` (JSON Textarea): **Retain but Organize**. The raw JSON input is necessary for pasting PVGIS data. Keep this field but consider moving it to an "Advanced" section or collapsible panel to avoid overwhelming the main view.
*   **Equipment Tab**:
    *   `Max Binning`: **Remove**. The camera driver reports this capability automatically; manual entry is redundant.
    *   `Focuser Min/Max`: **Auto-detect**. Query the ASCOM/ALPACA driver for limits instead of asking the user to type them.
    *   `Camera Type` (Text Input): **Change to Dropdown**. Restrict to `Mono` / `OSC` to prevent typos that break logic.

### 2. Streamlining the Imaging Session
The current workflow (Select Target → Configure Preset → Start) can be compressed:

*   **"One-Click" Imaging**: Since the system already calculates observability and scores targets, add a **"Start Auto-Pilot"** button to the Overview. This would:
    1.  Select the highest-ranked visible target.
    2.  Apply the appropriate preset (Bright/Medium/Faint) automatically.
    3.  Slew, center, and image without further prompts.
*   **Unified "Tonight's Plan"**: Instead of separate "Targets" and "Exposures" tabs, merge them. The "Targets" list should have a "Queue" action that adds them to a visible timeline.
*   **Automated Calibration**: Hide the "Run Calibrations" buttons. The system should automatically capture darks/flats at the end of the session (dawn) or when the rig is parked, based on a "Auto-Calibrate" toggle in settings.

### 3. Target-Driven Exposure Settings
**Yes, the intended target should absolutely decide the exposure setting.**

*   **Current Logic**: The backend (`app/services/presets.py`) already has logic to select a preset (`bright`, `medium`, `faint`) based on the target's V-magnitude.
*   **Recommendation**:
    *   **Remove Manual Preset Selection** from the primary workflow. The user should not have to guess if a mag 19 object needs 60s or 120s exposures.
    *   **Override Only**: Show the *calculated* preset (e.g., "Auto: Faint (120s x 6)") and allow the user to override it only if necessary.
    *   **Dynamic Spacing**: The system currently uses fixed delays (90s/120s/180s) for motion detection. This should be dynamic based on the target's calculated rate of motion (arcsec/min) to ensure enough pixel displacement occurs between frames.

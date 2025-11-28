# Streamlining Report

This document outlines components of the ASTRO-NEO project that are redundant, misplaced, or candidates for removal to improve maintainability.

## 1. Redundant Scripts

### `test_scripts/neocp_ingest.py`
**Status**: Redundant.
**Reason**: This script is a thin wrapper around `app.services.neocp.refresh_neocp_candidates`. The `neocp-fetcher` service (`app.services.neocp_fetcher`) provides a superset of this functionality, including observation fetching and metrics.
**Recommendation**:
-   Deprecate `test_scripts/neocp_ingest.py`.
-   Update documentation to use `docker compose run --rm neocp-fetcher python -m app.services.neocp_fetcher --oneshot` for manual ingestion.

### Root-Level Temporary Scripts
**Status**: Clutter.
**Reason**: Several scripts appear to be temporary debugging tools left in the project root.
-   `verify_dashboard_fix.py`: Likely a one-off fix verification.
-   `debug_horizon.py`: Debugging tool for horizon masks.
-   `astrometry_dl.sh`: Setup script for downloading index files.
**Recommendation**:
-   Delete `verify_dashboard_fix.py`.
-   Move `debug_horizon.py` to `test_scripts/` or delete if no longer needed.
-   Move `astrometry_dl.sh` to `ops/` or `docker/` to keep the root clean.

## 2. Directory Structure Anomalies

### Missing `scripts/` Directory
**Status**: Inconsistent.
**Reason**: `README.md` references a `scripts/` directory for "Container-only utilities", but this directory does not exist. `test_scripts/` exists but contains a mix of tests and utilities.
**Recommendation**:
-   Create `scripts/` and move operational utilities (like `neocp_ingest.py` if kept, or `astrometry_dl.sh`) there.
-   Or, update `README.md` to reflect that utilities are in `test_scripts/` (though separating tests from tools is better).

### `nina_bridge/` vs `app/services/nina_bridge.py`
**Status**: Confusing Naming.
**Reason**: `nina_bridge/` contains the source code for the standalone NINA Bridge FastAPI service. `app/services/nina_bridge.py` contains the *client* logic used by the main API to talk to that service.
**Recommendation**:
-   Rename `app/services/nina_bridge.py` to `app/services/nina_client.py` or similar to clearly distinguish the client integration from the service implementation.

## 3. Documentation Cleanup

### `documentation/nina_advanced_mcp.py`
**Status**: Misleading File Type.
**Reason**: This is a large (166KB) Python file inside the documentation folder. It appears to be a reference implementation or pasted code, not active project code.
**Recommendation**:
-   Move to `documentation/references/` and possibly rename to `.py.txt` to prevent it from being indexed as active source code by IDEs or tools.

### Reference Text Files
**Status**: Clutter.
**Reason**: `documentation/` contains several raw text dumps (`horizon_service.txt`, `open-mateo.txt`, `ades_text.txt`).
**Recommendation**:
-   Move these into a `documentation/references/` subdirectory.

## 4. Dormant Components

### `mock_nina` Service
**Status**: Commented Out.
**Reason**: The `mock-nina` service is defined in `docker-compose.yml` but commented out. It is valuable for testing but currently disabled.
**Recommendation**:
-   Keep the code (`mock_nina/` directory).
-   Add a `docker-compose.override.yml.example` or a specific profile (e.g., `docker compose --profile test up`) to enable it easily without editing the main compose file.

## 5. Design Reviews

### Imaging Preset Strategy
**Question**: Should we rely purely on target characteristics (Vmag, motion) for exposure settings, or keep user-defined presets?
**Analysis**:
-   **Purely Target-Driven**: Simpler config but less flexible for specific equipment quirks (e.g., poor tracking at long exposures).
-   **User-Defined Presets**: Allows users to define "strategies" (e.g., "Safe", "Deep") that map to their gear's capabilities.
**Recommendation**:
-   **Hybrid Approach**: Maintain user-defined presets in `site.yml` as the "strategy definitions".
-   **Auto-Selection**: Continue using target Vmag/Urgency to *select* the best preset automatically.
-   **User Override**: In the UI, present the auto-selected preset's values (Exposure, Count, Filter) as editable fields. This gives the user the "final say" to tweak the plan for a specific run without needing to redefine the global preset.
-   **Future**: Consider adding "Motion Rate" as a selection criteria (e.g., switch to a "Fast Mover" preset if rate > X arcsec/min).

## Summary of Actions
1.  **Move**: `astrometry_dl.sh` -> `ops/`.
2.  **Delete**: `verify_dashboard_fix.py`.
3.  **Deprecate**: `test_scripts/neocp_ingest.py` (update docs).
4.  **Organize**: Create `documentation/references/` and move raw text/code dumps there.
5.  **Rename**: Consider renaming `app/services/nina_bridge.py` -> `nina_client.py`.

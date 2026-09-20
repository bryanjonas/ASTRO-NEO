# Direct Hardware Control: Design Notes

_Branch: `direct_hardware`. Written 2026-09-19, before any implementation. Guiding is being dropped per your decision — the AM-3's harmonic drive makes unguided tracking at 60-120s exposures a reasonable bet, much more so than it would be on a worm-gear mount._

## What NINA actually does for this app today

Traced every real call site (only `sequential_capture.py` talks to hardware; `session.py` just calls `stop_sequence()` as a safety net on stop). The load-bearing surface is small:

- **Mount**: `slew(ra, dec)`, `wait_for_mount_ready()` (poll `Slewing`), `sync_mount(ra, dec)` (post-solve pointing correction), `mount_info()` (position/connected/parked state).
- **Camera**: `start_exposure(filter, binning, exposure_seconds)` — **fire-and-forget**: NINA triggers the exposure and writes the FITS file to disk itself (`/data/fits/{date}/{target}/SNAPSHOT/{TARGET}_{DATETIME}__{EXPOSURE}s_{FRAME}.fits`), and this app never receives pixel data over HTTP at all — it just polls the filesystem (`file_poller.py`) for a matching file to appear. `wait_for_camera_idle()` polls `IsExposing`. `abort_exposure()`.
- **Guiding**: `start_guiding_best_effort()` after every slew — being dropped.
- Dead/unused: `connect_dome`/`open_dome`/`close_dome`, `start_sequence`, `get_tracking` (already a stub), `set_tracking` (defined, never called — the whole reason this investigation started).

**The one surprisingly good finding**: of everything downstream (solver, association, PSV reporting), only `EXPTIME` is ever read back out of a FITS header. Filter, binning, target, RA/Dec are already tracked by this app itself in the database at capture time, not re-derived from the file. This matters a lot for scope — see below.

## What ASCOM Alpaca actually is, and one thing to be upfront about

Alpaca is ASCOM's own REST-native protocol — it's the right target, not a workaround. But **ZWO's ASCOM drivers for the AM3 and ASI cameras are native Windows COM drivers, not Alpaca servers themselves.** To reach them over HTTP from this WSL-based app, MELE still needs a small bridge running on the Windows side: the **ASCOM Remote Server** (or the newer "Alpaca Device Hub" from the ASCOM Initiative) — a lightweight, standard, actively-maintained tool whose only job is exposing a COM ASCOM driver as an Alpaca REST endpoint. This is genuinely much thinner than NINA (no sequencer, no plate-solving integration, no guiding orchestration, no GUI to speak of) — but "remove NINA" in practice means "replace NINA with ASCOM Remote Server," not "remove all Windows-side bridge software." Worth being precise about that distinction now, before it's a surprise later.

## The two pieces are not equal risk

**Mount control via Alpaca is small and low-risk**, and it's the piece that actually solves the problem that started this whole conversation: Alpaca's `ITelescopeV3` interface has a real `Tracking` boolean (readable and writable) and `TrackingRate`/rate-offset properties — exactly the state NINA's plugin never cleanly exposed. Connect, slew, poll slewing, sync, tracking on/off, park: all standard, all well-trodden ASCOM interface, all something a new `AlpacaMountClient` can implement as a drop-in replacement for the mount methods on `NinaBridgeService`, with no changes needed anywhere else in the codebase.

**Camera control via Alpaca is the bigger, genuinely new piece of work**: this app would go from "wait for a file to appear" to actually owning the exposure lifecycle — trigger the exposure, poll `ImageReady`, pull the pixel data over HTTP (Alpaca's `imagearray` endpoint; for a several-megapixel color sensor like the 585MC Pro, using its efficient binary transfer mode rather than plain JSON matters for performance and hasn't been tested), and **write the FITS file ourselves** (via `astropy.io.fits`, already a dependency — this part is mechanically straightforward) to the exact same path and filename convention NINA uses today, so `file_poller.py` and everything downstream keeps working completely unchanged. Given only `EXPTIME` is load-bearing for the rest of the pipeline, the header requirements are much lighter than "replicate everything NINA writes" — but a reasonably complete standard header set (DATE-OBS, FILTER, XBINNING/YBINNING, GAIN, CCD-TEMP, RA/DEC) is still worth including for archival quality and any future ADES-field mapping.

One thing that needs verification against real hardware, not something I can resolve by reading code: whether the 585MC Pro's raw Bayer (color) sensor data comes back from Alpaca in the same form NINA currently saves it in. If NINA is currently saving debayered or specially-processed frames, a raw Alpaca capture might look different to the solver/photometry pipeline than what it's tuned against today. Worth a direct comparison (same target, both paths) before trusting the new path for real data.

## Recommended phasing

**Phase 1 — mount only.** Build `AlpacaMountClient` (or extend `NinaBridgeService`'s mount methods behind a config flag), keep NINA in the loop for camera/exposure exactly as today. This alone fixes the tracking-state problem, is genuinely low-risk, and gives real, working experience with the ASCOM Remote Server bridge before committing to the bigger camera piece.

**Phase 2 — camera + FITS writing.** The larger, riskier piece: own the exposure trigger, image retrieval, and FITS construction. Should be validated by capturing the same real target both ways (NINA vs. direct) and comparing the resulting FITS files and solve/association results before switching over for real.

**Phase 3 — cutover.** Remove the NINA dependency: `mock_nina/`, `NINA_URL` config, the NINA-specific bits of `docker-compose.yml`, and the parts of `nina_client.py` (dome, sequence, guiding) that no longer apply. Update `README.md`/`LLM_SYSTEM_DESCRIPTION.md`, which still describe the NINA-based architecture.

## Phase 1 — real hardware verification (2026-09-19)

Both immediate-next-steps items below were completed and verified against the real AM-3, not just the mock server:

- **ASCOM Remote Server reachable and bridging correctly.** Took real troubleshooting to get there (Windows `http.sys` only accepts the literal `+`/`0.0.0.0`-as-"All Interfaces" distinction, not the literal string `0.0.0.0` itself — `HttpListener` throws "The request is not supported" on that), but once bound to the LAN interface, `configureddevices` correctly listed both `ASI Mount` (Telescope, device 0) and `ASI Camera` (Camera, device 0).
- **`AlpacaMountClient` confirmed working against the real mount**, from inside the actual api container (the real network path, not localhost): `mount_info()`, `connect`, and — the actual point of this branch — `ensure_sidereal_tracking()` all verified live. Tracking flipped `false → true` on command, `trackingrate: 0` (sidereal) confirmed.
- **`EquatorialSystem` confirmed = 1 (Topocentric/JNOW)** on the real AM-3 driver. The "NOT YET VERIFIED which epoch the driver reports" caveat in `alpaca_mount_client.py`'s docstrings is resolved: no J2000→JNOW conversion path has actually been exercised, and none is needed for this mount.
- **`park_telescope()` is NOT yet reliable and needs more work before Phase 3 can depend on it.** Live behavior: `AtPark` flickered `true` briefly mid-slew then reverted to `false`, the mount did a multi-stage slew (dipped toward Dec 0 before settling near Dec 90), and it never ended in a state where `AtPark` read `true`. Some of this session's confusion was later attributed to a human adjusting the mount via the ASCOM Remote Server GUI concurrently with the automated `Park` call — a clean, uncontested retest is needed before concluding anything definitive about whether `AtPark`'s reporting itself is unreliable on this driver, or whether it just needs a park position configured once in the driver's own setup.

Not yet done: wiring `AlpacaMountClient` into `sequential_capture.py` behind a config flag (`mount_backend: "nina" | "alpaca"`) — it exists as a standalone, tested class but nothing in the real capture pipeline uses it yet. That's the next real piece of Phase 1 before it can be called complete.

## Immediate next steps

1. Wire `AlpacaMountClient` into `sequential_capture.py` behind a `mount_backend` config flag so it can run side-by-side with NINA's mount path without disrupting `known_targets`-branch validation work.
2. Do a clean, uncontested `park_telescope()` retest (no concurrent manual GUI interaction) to determine whether `AtPark` reporting is genuinely unreliable on this driver or just needs a configured park position.
3. Start Phase 2 (camera + FITS writing) — see below.

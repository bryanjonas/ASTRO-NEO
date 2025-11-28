#!/usr/bin/env python3
"""Inject a synthetic moving NEO into the sample FITS frames and register them."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import httpx
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from sqlmodel import delete, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.session import get_session, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AstrometricSolution,
    CandidateAssociation,
    CaptureLog,
    Measurement,
    NeoCandidate,
    NeoEphemeris,
)
from app.services.imaging import build_fits_path  # noqa: E402

HOST_DATA_ROOT = REPO_ROOT / "data_local"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add a moving synthetic NEO into test_images and push results through the pipeline.",
    )
    parser.add_argument(
        "--target",
        default="SYNTH-NEO",
        help="Target name to stamp into FITS names and DB rows.",
    )
    parser.add_argument(
        "--trksub",
        default="SYNTH-NEO",
        help="Synthetic trksub to register in NeoCandidate/Ephemeris tables.",
    )
    parser.add_argument(
        "--api-base",
        default="http://localhost:18080/api",
        help="FastAPI base URL; set to '' to skip API calls (e.g., when calling worker directly).",
    )
    parser.add_argument(
        "--worker-base",
        default="http://astrometry-worker:8100",
        help="Astrometry worker base URL (used when api-base is empty).",
    )
    parser.add_argument(
        "--cadence",
        type=float,
        default=90.0,
        help="Seconds between synthetic frames.",
    )
    parser.add_argument(
        "--start-x",
        type=float,
        default=None,
        help="Starting X pixel (defaults to image center).",
    )
    parser.add_argument(
        "--start-y",
        type=float,
        default=None,
        help="Starting Y pixel (defaults to image center).",
    )
    parser.add_argument(
        "--delta-x",
        type=float,
        default=8.0,
        help="Per-frame X pixel offset for the moving object.",
    )
    parser.add_argument(
        "--delta-y",
        type=float,
        default=-3.5,
        help="Per-frame Y pixel offset for the moving object.",
    )
    parser.add_argument(
        "--predicted-ra-dec",
        type=str,
        default=None,
        help="Optional semicolon-separated list of RA,Dec pairs to inject as predicted ephemeris (e.g., '62.51,30.66;62.51,30.66').",
    )
    parser.add_argument(
        "--flux",
        type=float,
        default=12000.0,
        help="Peak flux for the synthetic PSF.",
    )
    parser.add_argument(
        "--fwhm",
        type=float,
        default=3.0,
        help="FWHM in pixels for the synthetic PSF.",
    )
    parser.add_argument(
        "--base-images",
        nargs="*",
        default=None,
        help="Paths to seed FITS files (defaults to test_images/*.fit).",
    )
    parser.add_argument(
        "--host-data-root",
        default=str(REPO_ROOT / "data_local"),
        help="Host path where synthetic FITS will be written (use this if ./data is not writable).",
    )
    return parser.parse_args()


def gaussian_stamp(shape: Tuple[int, int], x: float, y: float, flux: float, fwhm: float) -> np.ndarray:
    """Return a Gaussian spot with the requested position/flux."""
    sigma = fwhm / 2.355
    yy, xx = np.indices(shape)
    spot = flux * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))
    return spot


def inject_object(
    base_path: Path,
    out_host_path: Path,
    x: float,
    y: float,
    flux: float,
    fwhm: float,
) -> Path:
    """Write a FITS file with a synthetic moving object."""
    data, header = fits.getdata(base_path, header=True)
    data = np.asarray(data, dtype=float)
    data += gaussian_stamp(data.shape, x, y, flux=flux, fwhm=fwhm)
    header["HISTORY"] = f"SYNTHETIC_NEO injected at ({x:.2f},{y:.2f})"
    out_host_path.parent.mkdir(parents=True, exist_ok=True)
    fits.writeto(out_host_path, data, header, overwrite=True)
    return out_host_path


def build_output_paths(
    target: str,
    started_at: datetime,
    sequence: str,
    index: int,
) -> tuple[Path, Path]:
    """Return container-path (for DB) and host-path (for writing) for a capture."""
    container_path = build_fits_path(
        target_name=target,
        start_time=started_at,
        sequence_name=sequence,
        index=index,
    )
    try:
        rel = container_path.relative_to(Path(settings.data_root))
    except ValueError:
        rel = container_path
    host_path = Path(HOST_DATA_ROOT) / rel
    return container_path, host_path


def ensure_candidate_with_ephemeris(
    trksub: str,
    ra_dec_series: Sequence[tuple[datetime, tuple[float, float]]],
    vmag: float = 17.5,
) -> None:
    """Create/update a NeoCandidate and synthetic ephemeris rows."""
    if not ra_dec_series:
        print("No RA/Dec samples available; skipping ephemeris injection.")
        return
    init_db()
    with get_session() as session:
        first_time, (first_ra, first_dec) = ra_dec_series[0]
        candidate = session.exec(select(NeoCandidate).where(NeoCandidate.trksub == trksub)).first()
        now = datetime.utcnow()
        if not candidate:
            candidate = NeoCandidate(
                trksub=trksub,
                score=95,
                observations=len(ra_dec_series),
                observed_ut=first_time.isoformat(),
                ra_deg=first_ra,
                dec_deg=first_dec,
                vmag=vmag,
                status="Synthetic",
                status_ut=first_time.isoformat(),
                raw_entry="Synthetic test object",
                created_at=now,
                updated_at=now,
            )
            session.add(candidate)
            session.commit()
            session.refresh(candidate)
        else:
            candidate.ra_deg = first_ra
            candidate.dec_deg = first_dec
            candidate.vmag = candidate.vmag or vmag
            candidate.observations = len(ra_dec_series)
            candidate.updated_at = now
            session.add(candidate)
            session.commit()
            session.refresh(candidate)

        session.exec(delete(NeoEphemeris).where(NeoEphemeris.candidate_id == candidate.id))
        for epoch, (ra, dec) in ra_dec_series:
            session.add(
                NeoEphemeris(
                    candidate_id=candidate.id,
                    trksub=candidate.trksub,
                    epoch=epoch,
                    ra_deg=ra,
                    dec_deg=dec,
                    magnitude=vmag,
                    rate_arcsec_per_min=None,
                    position_angle_deg=None,
                )
            )
        session.commit()
        print(f"Ephemeris rows written for {candidate.trksub} ({len(ra_dec_series)} samples)")


def persist_captures(entries: Iterable[dict], target: str, sequence: str) -> list[CaptureLog]:
    init_db()
    models: list[CaptureLog] = []
    with get_session() as session:
        # Overwrite previous synthetic entries and dependents
        id_rows = session.exec(
            select(CaptureLog.id).where(CaptureLog.target == target, CaptureLog.sequence == sequence)
        ).all()
        prior_ids = [row[0] if isinstance(row, tuple) else row for row in id_rows]
        if prior_ids:
            session.exec(delete(CandidateAssociation).where(CandidateAssociation.capture_id.in_(prior_ids)))
            session.exec(delete(Measurement).where(Measurement.capture_id.in_(prior_ids)))
            session.exec(delete(AstrometricSolution).where(AstrometricSolution.capture_id.in_(prior_ids)))
            session.exec(delete(CaptureLog).where(CaptureLog.id.in_(prior_ids)))
            session.commit()

        for entry in entries:
            model = CaptureLog(
                kind=entry.get("kind", "sequence"),
                target=entry.get("target") or "synthetic",
                sequence=entry.get("sequence"),
                index=entry.get("index"),
                path=entry.get("path"),
                started_at=entry.get("started_at"),
            )
            session.add(model)
            models.append(model)
        session.commit()
        for model in models:
            session.refresh(model)
    return models


def solve_via_api(api_base: str, capture_ids: Sequence[int]) -> None:
    if not api_base:
        return
    with httpx.Client(timeout=120) as client:
        for cap_id in capture_ids:
            try:
                resp = client.post(f"{api_base}/astrometry/solve", json={"capture_id": cap_id})
                if resp.status_code >= 300:
                    print(f"api solve {cap_id} -> {resp.status_code} {resp.text}")
                else:
                    detail = resp.json()
                    flags = detail.get("flags")
                    msg = f"api solve {cap_id} -> success={detail.get('success')} path={detail.get('path')}"
                    if flags:
                        msg += f" flags={flags}"
                    solver_info = detail.get("solver_info")
                    if solver_info:
                        msg += f" solver_info={str(solver_info)[:200]}"
                    print(msg)
            except Exception as exc:
                print(f"api solve {cap_id} -> request failed: {exc}")
    time.sleep(0.5)


def solve_via_worker(worker_base: str, entries: Sequence[dict]) -> None:
    if not worker_base:
        return
    url = f"{worker_base.rstrip('/')}/solve"
    with httpx.Client(timeout=300) as client:
        for entry in entries:
            try:
                payload = {"path": entry["path"], "timeout": 300}
                resp = client.post(url, json=payload)
                if resp.status_code >= 300:
                    print(f"worker solve {entry['path']} -> {resp.status_code} {resp.text}")
                else:
                    msg = f"worker solve {entry['path']} -> ok"
                    info = resp.json()
                    solver_info = info.get("solver_info")
                    if solver_info:
                        msg += f" solver_info={str(solver_info)[:200]}"
                    print(msg)
            except Exception as exc:
                print(f"worker solve {entry['path']} -> request failed: {exc}")
    time.sleep(0.5)


def push_captures_to_session(
    api_base: str, entries: Sequence[dict], predictions: dict[str, tuple[float, float]] | None = None
) -> None:
    """Send captures into the in-memory session so Association UI can see them."""
    if not api_base:
        return
    url = f"{api_base.rstrip('/')}/session/ingest_captures"
    payload = []
    for entry in entries:
        item = {
            "kind": entry.get("kind", "synthetic"),
            "target": entry.get("target") or "synthetic",
            "sequence": entry.get("sequence"),
            "index": entry.get("index"),
            "path": entry.get("path"),
            "started_at": entry.get("started_at").isoformat()
            if hasattr(entry.get("started_at"), "isoformat")
            else datetime.utcnow().isoformat(),
        }
        if predictions:
            pred = predictions.get(entry["path"])
            if pred:
                item["predicted_ra_deg"] = pred[0]
                item["predicted_dec_deg"] = pred[1]
        payload.append(item)
    try:
        resp = httpx.post(url, json=payload, timeout=60)
        if resp.status_code >= 300:
            print(f"session ingest -> {resp.status_code} {resp.text}")
        else:
            print(f"session ingest -> ok ({len(payload)} captures)")
    except Exception as exc:
        print(f"session ingest -> request failed: {exc}")


def purge_ephemeris(trksub: str) -> None:
    """Remove prior ephemeris rows for this synthetic object."""
    init_db()
    with get_session() as session:
        candidate = session.exec(select(NeoCandidate).where(NeoCandidate.trksub == trksub)).first()
        if candidate and candidate.id:
            session.exec(delete(NeoEphemeris).where(NeoEphemeris.candidate_id == candidate.id))
            session.commit()


def _host_path_for_container(container_path: str | Path) -> Path:
    path = Path(container_path)
    try:
        rel = path.relative_to(Path(settings.data_root))
    except ValueError:
        return path
    return Path(HOST_DATA_ROOT) / rel


def extract_ra_dec_from_solved(entries: Sequence[dict]) -> list[tuple[datetime, tuple[float, float]]]:
    """Use solved WCS (prefer .new outputs) to derive RA/Dec for injected pixels."""
    samples: list[tuple[datetime, tuple[float, float]]] = []
    for entry in entries:
        host_path = Path(entry["host_path"])
        x, y = entry["pixel"]
        candidates = []
        base_no_ext = Path(str(host_path).rsplit(".", 1)[0])
        candidates.append(base_no_ext.with_suffix(".new"))
        candidates.append(base_no_ext.with_suffix(".new.fits"))
        candidates.append(host_path)
        wcs_path = next((p for p in candidates if p.exists()), None)
        if not wcs_path:
            print(f"WCS file not found for {host_path} (checked .new/.new.fits/original)")
            continue
        try:
            hdr = fits.getheader(wcs_path)
            wcs = WCS(hdr)
            try:
                sky = wcs.pixel_to_world(x, y)
                sky = sky[0] if isinstance(sky, (list, tuple)) else sky
                ra = _to_float(np.atleast_1d(sky.ra.deg)[0])
                dec = _to_float(np.atleast_1d(sky.dec.deg)[0])
            except Exception:
                ra, dec = wcs.pixel_to_world_values(x, y)
                ra, dec = _to_float(ra), _to_float(dec)
            samples.append((entry["started_at"], (ra, dec)))
        except Exception as exc:
            print(f"Could not derive RA/Dec for {wcs_path}: {exc}")
    return samples


def _to_float(val: float | int | np.ndarray | list | tuple) -> float:
    if isinstance(val, (list, tuple)):
        val = val[0]
    try:
        return float(val)
    except Exception:
        return float(np.asarray(val).flatten()[0])


def main() -> int:
    args = parse_args()
    global HOST_DATA_ROOT
    HOST_DATA_ROOT = Path(args.host_data_root)
    try:
        HOST_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        print(
            f"Host data root {HOST_DATA_ROOT} is not writable. "
            "Pick a writable path with --host-data-root, e.g. ./data_local",
        )
        raise SystemExit(1) from exc
    base_files = (
        [Path(p) for p in args.base_images]
        if args.base_images
        else sorted(Path(REPO_ROOT / "test_images").glob("*.fit*"))
    )
    if not base_files:
        print("No base FITS files found.")
        return 1

    base_time = datetime.now(timezone.utc).replace(microsecond=0)
    positions: list[tuple[float, float]] = []
    capture_entries: list[dict] = []

    # Remove stale ephemeris upfront so re-runs stay clean
    purge_ephemeris(args.trksub)

    for idx, base_path in enumerate(base_files, start=1):
        data = fits.getdata(base_path)
        height, width = data.shape
        start_x = args.start_x if args.start_x is not None else width / 2
        start_y = args.start_y if args.start_y is not None else height / 2
        x = start_x + (idx - 1) * args.delta_x
        y = start_y + (idx - 1) * args.delta_y
        positions.append((x, y))
        started_at = base_time + timedelta(seconds=args.cadence * (idx - 1))

        container_path, host_path = build_output_paths(
            target=args.target,
            started_at=started_at,
            sequence="synthetic",
            index=idx,
        )
        inject_object(
            base_path=base_path,
            out_host_path=host_path,
            x=x,
            y=y,
            flux=args.flux,
            fwhm=args.fwhm,
        )
        capture_entries.append(
            {
                "kind": "synthetic_sequence",
                "target": args.target,
                "sequence": "synthetic",
                "index": idx,
                "path": str(container_path),
                "host_path": str(host_path),
                "pixel": (x, y),
                "started_at": started_at.replace(tzinfo=None),
            }
        )
        print(f"Wrote {host_path} with synthetic spot at ({x:.1f}, {y:.1f})")

    captures = persist_captures(capture_entries, target=args.target, sequence="synthetic")
    print(f"Persisted {len(captures)} capture logs.")
    # Prefer API solves (which delegate to the worker); fall back to direct worker calls when api-base is empty
    if args.api_base:
        solve_via_api(args.api_base, [c.id for c in captures if c.id is not None])
    else:
        solve_via_worker(args.worker_base, capture_entries)
    # Recompute host paths in case caller pointed host_data_root somewhere else
    for entry in capture_entries:
        entry["host_path"] = str(_host_path_for_container(entry["path"]))
    ra_dec_series = extract_ra_dec_from_solved(capture_entries)
    # If user provided predicted RA/Dec, override the extracted series
    manual_predictions: dict[str, tuple[float, float]] = {}
    if args.predicted_ra_dec:
        parts = [p.strip() for p in args.predicted_ra_dec.split(";") if p.strip()]
        manual_vals: list[tuple[float, float]] = []
        for part in parts:
            try:
                ra_str, dec_str = part.split(",")
                manual_vals.append((float(ra_str), float(dec_str)))
            except Exception:
                print(f"Skipping invalid predicted RA/Dec pair: {part}")
        if manual_vals:
            # Map in order of captures
            for idx, entry in enumerate(capture_entries):
                if idx < len(manual_vals):
                    manual_predictions[entry["path"]] = manual_vals[idx]
            ra_dec_series = [
                (entry["started_at"], manual_predictions.get(entry["path"]) or (vals[0], vals[1]))
                for entry, vals in zip(capture_entries, ra_dec_series or [(None, (None, None))] * len(capture_entries))
            ]
    ensure_candidate_with_ephemeris(args.trksub, ra_dec_series)
    # Build predictions map from whichever RA/Dec source we have
    predictions: dict[str, tuple[float, float]] = {}
    if manual_predictions:
        predictions.update(manual_predictions)
    elif ra_dec_series:
        for entry, item in zip(capture_entries, ra_dec_series):
            _, (ra_val, dec_val) = item
            predictions[entry["path"]] = (ra_val, dec_val)
    push_captures_to_session(args.api_base, capture_entries, predictions=predictions or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

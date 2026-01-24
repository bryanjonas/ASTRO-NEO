"""Thin wrapper around astrometry.net solve-field."""

from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
import sys
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits

from app.core.config import settings


class SolveError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


def _emit_solver_output(result: subprocess.CompletedProcess[str], path: Path) -> None:
    """Write solver stdout/stderr to stderr so container logs capture it without UI buffer."""
    prefix = f"solve-field [{path.name}]"
    if result.stdout:
        sys.stderr.write(f"{prefix} stdout:\n{result.stdout}\n")
    if result.stderr:
        sys.stderr.write(f"{prefix} stderr:\n{result.stderr}\n")
    if result.stdout or result.stderr:
        sys.stderr.flush()


def solve_fits(
    fits_path: str | Path,
    radius_deg: float | None = None,
    ra_hint: float | None = None,
    dec_hint: float | None = None,
    downsample: int | None = None,
    sigma: float | None = None,
    scale_low_arcsec: float | None = None,
    scale_high_arcsec: float | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Run solve-field locally (synchronous subprocess)."""

    return _solve_local(
        fits_path,
        radius_deg=radius_deg,
        ra_hint=ra_hint,
        dec_hint=dec_hint,
        downsample=downsample,
        sigma=sigma,
        scale_low_arcsec=scale_low_arcsec,
        scale_high_arcsec=scale_high_arcsec,
        timeout=timeout or settings.astrometry_solve_timeout,
    )


def _solve_local(
    fits_path: str | Path,
    *,
    radius_deg: float | None,
    ra_hint: float | None,
    dec_hint: float | None,
    downsample: int | None,
    sigma: float | None,
    scale_low_arcsec: float | None,
    scale_high_arcsec: float | None,
    timeout: int,
) -> dict[str, Any]:
    path = Path(fits_path)
    if not path.exists():
        raise SolveError(f"FITS not found: {path}")

    def _stream_solver_output(pipe, prefix: str, bucket: list[str]) -> None:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            bucket.append(line)
            sys.stderr.write(f"{prefix} {line}")
        try:
            pipe.close()
        except Exception:
            return

    def _run(cmd: list[str], log_failure: bool = True) -> subprocess.CompletedProcess[str]:
        if settings.astrometry_debug_logs:
            logger.info("solve-field cmd: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(path.parent),
            )
        except Exception as exc:
            raise SolveError("solve-field failed to start") from exc

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        threads: list[threading.Thread] = []
        streaming = bool(settings.astrometry_debug_logs and proc.stdout and proc.stderr)
        if streaming:
            prefix = f"solve-field [{path.name}]"
            threads = [
                threading.Thread(
                    target=_stream_solver_output,
                    args=(proc.stdout, f"{prefix} stdout:", stdout_lines),
                    daemon=True,
                ),
                threading.Thread(
                    target=_stream_solver_output,
                    args=(proc.stderr, f"{prefix} stderr:", stderr_lines),
                    daemon=True,
                ),
            ]
            for thread in threads:
                thread.start()

            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                raise SolveError("solve-field timed out") from exc
        else:
            try:
                stdout_data, stderr_data = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                stdout_data, stderr_data = proc.communicate()
                raise SolveError("solve-field timed out") from exc
            if stdout_data:
                stdout_lines.append(stdout_data)
            if stderr_data:
                stderr_lines.append(stderr_data)

        for thread in threads:
            thread.join(timeout=0.5)

        result = subprocess.CompletedProcess(
            cmd,
            returncode=proc.returncode,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
        )

        if result.returncode != 0:
            if not log_failure:
                if "unrecognized option '--json'" in (result.stderr or ""):
                    raise SolveError("solve-field does not support --json")
                detail = result.stderr.strip() if result.stderr else "solve-field failed"
                raise SolveError(detail)
            detail = result.stderr or result.stdout or f"solve-field failed (code={result.returncode})"
            report = ""
            if log_failure:
                report = _build_solve_failure_report(
                    path=path,
                    cmd=cmd,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    config_path=config_path,
                )
                logger.error(report)
            if report:
                raise SolveError(f"{detail}\n{report}")
            raise SolveError(detail)
        return result

    base_cmd = [
        "solve-field",
        "--overwrite",
        "--no-plots",
        "--dir",
        str(path.parent),
    ]
    solved_marker = path.with_suffix(".solved")
    wcs_output = path.with_suffix(".wcs")
    base_cmd += ["--wcs", str(wcs_output)]
    base_cmd += ["--solved", str(solved_marker)]
    index_dir = os.getenv("ASTROMETRY_INDEX_DIR", "/data/indexes")
    if os.path.isdir(index_dir):
        base_cmd += ["--index-dir", index_dir]
    config_path = Path(settings.astrometry_config_path)
    if not config_path.exists():
        for fallback in ("/etc/astrometry.cfg", "/app/app/worker/astrometry.cfg", "/app/worker/astrometry.cfg"):
            fallback_path = Path(fallback)
            if fallback_path.exists():
                config_path = fallback_path
                break
    if config_path.exists():
        base_cmd += ["--config", str(config_path)]
    if radius_deg is not None:
        base_cmd += ["--radius", str(radius_deg)]
    elif settings.astrometry_search_radius_deg is not None:
        base_cmd += ["--radius", str(settings.astrometry_search_radius_deg)]
    elif ra_hint is not None and dec_hint is not None:
        base_cmd += ["--radius", "2.0"]
    if ra_hint is not None and dec_hint is not None:
        base_cmd += ["--ra", str(ra_hint), "--dec", str(dec_hint)]
    eff_downsample = downsample or settings.astrometry_downsample
    if eff_downsample:
        base_cmd += ["--downsample", str(eff_downsample)]
    eff_sigma = sigma if sigma is not None else None
    if eff_sigma is not None:
        base_cmd += ["--sigma", str(eff_sigma)]
    low = settings.astrometry_scale_low_arcsec
    high = settings.astrometry_scale_high_arcsec
    if scale_low_arcsec is not None:
        low = scale_low_arcsec
    if scale_high_arcsec is not None:
        high = scale_high_arcsec
    if (low is None or high is None) and settings.astrometry_pixel_scale_arcsec:
        scale = settings.astrometry_pixel_scale_arcsec
        low = low or max(0.1, scale * 0.5)
        high = high or scale * 2.0
    if low and high:
        base_cmd += ["--scale-units", "arcsecperpix", "--scale-low", str(low), "--scale-high", str(high)]

    # Run solve-field in legacy mode (no --json); parse .wcs instead
    res = _run(base_cmd + [str(path)])

    if not solved_marker.exists():
        extras = sorted(
            p.name for p in path.parent.glob(f"{path.stem}.*")
        )
        detail = f"Solve completed but {solved_marker} not found"
        if extras:
            detail += f" (found {', '.join(extras)})"
        if res.stdout:
            detail += f"\nsolve-field stdout: {res.stdout}"
        if res.stderr:
            detail += f"\nsolve-field stderr: {res.stderr}"
        raise SolveError(detail)

    # Log full output only when debug is enabled
    if settings.astrometry_debug_logs:
        _emit_solver_output(res, path)

    solution = _normalize_solution(_parse_wcs_solution(path))
    _log_astrometry_context(path, solution)

    # Copy WCS headers from .wcs file back to original FITS
    _copy_wcs_to_fits(path)

    # Try to extract RMS from stdout
    import re
    match = re.search(r"RMS:\s+([0-9]*\.?[0-9]+)\s+arcsec", res.stdout)
    if match:
        solution["solution"]["rms"] = float(match.group(1))
    else:
        # Try to calculate from .corr file
        rms = _calculate_rms_from_corr(path)
        if rms is not None:
            solution["solution"]["rms"] = rms
        else:
            logging.warning("Could not extract RMS from solve-field output or .corr file")

    return solution


def _calculate_rms_from_corr(fits_path: Path) -> float | None:
    """Calculate RMS error from the .corr file produced by solve-field."""
    corr_path = fits_path.with_suffix(".corr")
    if not corr_path.exists():
        # Sometimes it might be named differently?
        return None
        
    try:
        with fits.open(corr_path) as hdul:
            if len(hdul) < 2:
                return None
            data = hdul[1].data
            if data is None or len(data) == 0:
                return None
                
            # Check for RA/Dec columns
            if "field_ra" in data.names and "index_ra" in data.names:
                field_ra = data["field_ra"]
                field_dec = data["field_dec"]
                index_ra = data["index_ra"]
                index_dec = data["index_dec"]
                
                # Calculate angular separation
                # Simple approximation for small offsets
                d_dec = field_dec - index_dec
                d_ra = (field_ra - index_ra) * np.cos(np.radians(index_dec))
                
                dist_sq = d_ra**2 + d_dec**2
                rms_deg = np.sqrt(np.mean(dist_sq))
                return float(rms_deg * 3600.0)
                
    except Exception as exc:
        import logging
        logging.warning("Failed to calculate RMS from .corr: %s", exc)
        return None


def _copy_wcs_to_fits(fits_path: Path) -> None:
    """Copy WCS headers from .wcs or .new file into the original FITS file."""
    import logging
    wcs_path = fits_path.with_suffix(".wcs")
    new_path = fits_path.with_suffix(".new")
    header_path = wcs_path if wcs_path.exists() else new_path
    if not header_path.exists():
        logging.warning(f"No WCS header source found: {wcs_path} or {new_path}")
        return

    try:
        # Read WCS headers
        wcs_hdr = fits.getheader(header_path)

        # Open original FITS and update header
        with fits.open(fits_path, mode='update') as hdul:
            # WCS keywords to copy
            wcs_keywords = [
                'WCSAXES', 'CTYPE1', 'CTYPE2', 'EQUINOX', 'LONPOLE', 'LATPOLE',
                'CRVAL1', 'CRVAL2', 'CRPIX1', 'CRPIX2', 'CUNIT1', 'CUNIT2',
                'CD1_1', 'CD1_2', 'CD2_1', 'CD2_2',
                'CDELT1', 'CDELT2', 'CROTA1', 'CROTA2',
                'IMAGEW', 'IMAGEH', 'A_ORDER', 'B_ORDER', 'A_0_0', 'A_0_1',
                'A_0_2', 'A_1_0', 'A_1_1', 'A_2_0', 'B_0_0', 'B_0_1',
                'B_0_2', 'B_1_0', 'B_1_1', 'B_2_0', 'AP_ORDER', 'BP_ORDER',
                'AP_0_0', 'AP_0_1', 'AP_0_2', 'AP_1_0', 'AP_1_1', 'AP_2_0',
                'BP_0_0', 'BP_0_1', 'BP_0_2', 'BP_1_0', 'BP_1_1', 'BP_2_0',
            ]

            # Copy WCS keywords
            for keyword in wcs_keywords:
                if keyword in wcs_hdr:
                    hdul[0].header[keyword] = wcs_hdr[keyword]

            # Also copy COMMENT cards related to astrometry.net
            for card in wcs_hdr.cards:
                if card.keyword == 'COMMENT' and 'astrometry.net' in str(card.value).lower():
                    hdul[0].header.add_comment(card.value)

        logging.info(f"Copied WCS headers to {fits_path.name}")
    except Exception as exc:
        logging.error(f"Failed to copy WCS headers to {fits_path}: {exc}")


def _parse_wcs_solution(fits_path: Path) -> dict[str, Any]:
    """Parse the .wcs/.new header produced by solve-field when --json is unavailable."""
    wcs_path = fits_path.with_suffix(".wcs")
    new_path = fits_path.with_suffix(".new")
    if wcs_path.exists():
        hdr = fits.getheader(wcs_path)
    elif new_path.exists():
        hdr = fits.getheader(new_path)
    else:
        extras = sorted(
            p.name for p in fits_path.parent.glob(f"{fits_path.stem}.*")
        )
        detail = f"Solve completed but {wcs_path} not found"
        if extras:
            detail += f" (found {', '.join(extras)})"
        raise SolveError(detail)
    ra = hdr.get("CRVAL1")
    dec = hdr.get("CRVAL2")
    epoch = hdr.get("EQUINOX") or hdr.get("RADESYS")
    cd11 = hdr.get("CD1_1") or hdr.get("CDELT1")
    cd22 = hdr.get("CD2_2") or hdr.get("CDELT2")
    cd12 = hdr.get("CD1_2")
    cd21 = hdr.get("CD2_1")
    # Pixel scale (arcsec/pixel) from CD matrix
    scales = []
    if cd11 is not None and cd12 is not None:
        scales.append(math.hypot(cd11, cd12) * 3600.0)
    if cd21 is not None and cd22 is not None:
        scales.append(math.hypot(cd21, cd22) * 3600.0)
    if not scales:
        if cd11 is not None:
            scales.append(abs(cd11) * 3600.0)
        if cd22 is not None:
            scales.append(abs(cd22) * 3600.0)
    scale_arcsec = float(sum(scales) / len(scales)) if scales else None
    orientation_deg = None
    if cd11 is not None and cd12 is not None:
        orientation_deg = math.degrees(math.atan2(cd12, cd11))
    return {
        "solution": {
            "ra_deg": ra,
            "dec_deg": dec,
            "epoch": epoch,
            "pixscale": scale_arcsec,
            "orientation": orientation_deg,
        }
    }


def _normalize_solution(result: dict[str, Any]) -> dict[str, Any]:
    solution = result.get("solution")
    if not isinstance(solution, dict):
        return result
    if "ra_deg" not in solution and "ra" in solution:
        solution["ra_deg"] = solution["ra"]
    if "dec_deg" not in solution and "dec" in solution:
        solution["dec_deg"] = solution["dec"]
    return result


def _log_astrometry_context(path: Path, solution: dict[str, Any]) -> None:
    if not settings.astrometry_debug_logs:
        return
    header = {}
    try:
        with fits.open(path) as hdul:
            header = {
                "DATE-OBS": hdul[0].header.get("DATE-OBS"),
                "EXPTIME": hdul[0].header.get("EXPTIME"),
                "OBJECT": hdul[0].header.get("OBJECT"),
                "RA": hdul[0].header.get("RA"),
                "DEC": hdul[0].header.get("DEC"),
                "CRVAL1": hdul[0].header.get("CRVAL1"),
                "CRVAL2": hdul[0].header.get("CRVAL2"),
                "NAXIS1": hdul[0].header.get("NAXIS1"),
                "NAXIS2": hdul[0].header.get("NAXIS2"),
            }
    except Exception as exc:
        logger.warning("Astrometry context: failed to read FITS header for %s: %s", path, exc)
    logger.info(
        "Astrometry context: fits=%s header=%s solution=%s",
        path.name,
        header,
        solution.get("solution"),
    )


def _build_solve_failure_report(
    *,
    path: Path,
    cmd: list[str],
    stdout: str,
    stderr: str,
    config_path: Path,
) -> str:
    lines: list[str] = []
    lines.append(f"solve-field failed: cmd={' '.join(cmd)}")
    if stdout:
        lines.append(f"solve-field stdout: {stdout}")
    if stderr:
        lines.append(f"solve-field stderr: {stderr}")

    lines.append(f"solve-field cwd={path.parent}")
    lines.append(f"solve-field config={config_path} (exists={config_path.exists()})")

    index_dir = os.getenv("ASTROMETRY_INDEX_DIR", "/data/indexes")
    try:
        entries = sorted(os.listdir(index_dir))
        sample = entries[:10]
        lines.append(
            "astrometry indexes: dir=%s exists=%s count=%s sample=%s"
            % (index_dir, os.path.isdir(index_dir), len(entries), sample)
        )
    except Exception as exc:
        lines.append(f"astrometry index dir check failed for {index_dir}: {exc}")

    try:
        lines.append(f"fits path={path} exists={path.exists()} size={path.stat().st_size}")
    except Exception as exc:
        lines.append(f"fits stat failed: {exc}")

    try:
        header = fits.getheader(path)
        keys = [
            "DATE-OBS",
            "EXPTIME",
            "FILTER",
            "OBJECT",
            "RA",
            "DEC",
            "CRVAL1",
            "CRVAL2",
            "NAXIS1",
            "NAXIS2",
        ]
        snapshot = {key: header.get(key) for key in keys if key in header}
        lines.append(f"fits header snapshot: {snapshot}")
    except Exception as exc:
        lines.append(f"fits header read failed: {exc}")

    return "\n".join(lines)


__all__ = ["solve_fits", "SolveError"]

"""All-sky polar alignment -- direct_hardware branch, NINA-free.

Same family of technique as SharpCap's all-sky polar alignment (not the
older multi-point drift-alignment method): capture and plate-solve at one
pointing, command a known rotation of the mount's RA axis only (leaving
the Dec axis untouched), capture and solve again. A perfectly polar
aligned mount rotating purely about its RA axis sweeps along a fixed
line of true declination in the sky; any polar misalignment shows up as
a change in solved declination between the two shots, and the two
readings (together with the known site latitude and commanded rotation)
determine exactly how far off, and in which direction, the mount's
physical polar axis actually points.

Solved via direct vector geometry (find the rotation axis that carries
one measured pointing to the other given a known rotation angle) rather
than a memorized small-angle trig formula -- deliberately, to avoid the
real failure mode of a sign error silently telling someone to turn a
real adjustment knob the wrong way. Validated against a synthetic,
injected-error ground truth (see tests/test_polar_alignment.py) before
this ever touches real hardware.

Frame choice matters here: the computation is done in the site's local,
ground-fixed Alt/Az frame, not sky-fixed RA/Dec -- because the mount's
physical rotation axis is a fixed direction relative to the ground (a
tripod leg doesn't move with the stars), not the sky. Each solved RA/Dec
is converted to Alt/Az using its own observation time, which naturally
folds in Earth's rotation between the two captures.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time
import astropy.units as u


class PolarAlignmentError(Exception):
    """Could not solve for the polar axis error from the given inputs."""


def _altaz_unit_vector(alt_deg: float, az_deg: float) -> np.ndarray:
    """Ground-fixed Cartesian unit vector. Az measured North->East, matching
    standard astronomical convention (0=N, 90=E)."""
    alt = np.radians(alt_deg)
    az = np.radians(az_deg)
    return np.array(
        [np.cos(alt) * np.cos(az), np.cos(alt) * np.sin(az), np.sin(alt)]
    )


def _unit_vector_to_altaz(v: np.ndarray) -> tuple[float, float]:
    x, y, z = v
    alt = float(np.degrees(np.arcsin(np.clip(z, -1.0, 1.0))))
    az = float(np.degrees(np.arctan2(y, x)) % 360.0)
    return alt, az


def _wrap_deg_signed(deg: float) -> float:
    """Wrap into (-180, 180]."""
    return ((deg + 180.0) % 360.0) - 180.0


def _rotation_angle_about_axis(v1: np.ndarray, v2: np.ndarray, axis: np.ndarray) -> float:
    """Signed angle (radians, right-hand rule about `axis`) that rotates the
    component of v1 perpendicular to axis onto that of v2. Assumes v1, v2
    are equidistant from axis (true by construction for axis candidates on
    the perpendicular-bisector great circle used below)."""
    v1_perp = v1 - np.dot(v1, axis) * axis
    v2_perp = v2 - np.dot(v2, axis) * axis
    n1 = np.linalg.norm(v1_perp)
    n2 = np.linalg.norm(v2_perp)
    if n1 < 1e-9 or n2 < 1e-9:
        raise PolarAlignmentError("Degenerate geometry: a pointing lies on the candidate axis")
    cos_phi = float(np.dot(v1_perp, v2_perp) / (n1 * n2))
    sin_phi = float(np.dot(np.cross(v1_perp, v2_perp), axis) / (n1 * n2))
    return float(np.arctan2(sin_phi, cos_phi))


def solve_polar_axis_error(
    *,
    ra1_deg: float,
    dec1_deg: float,
    time1: Time,
    ra2_deg: float,
    dec2_deg: float,
    time2: Time,
    commanded_rotation_deg: float,
    site_lat_deg: float,
    site_lon_deg: float,
    site_height_m: float = 0.0,
    search_bracket_deg: float = 5.0,
) -> dict[str, Any]:
    """Solve for how far the mount's physical polar axis is from true
    north, given two solved pointings separated by a known, RA-axis-only
    commanded rotation.

    Returns a dict with az_error_arcmin (positive = axis points east of
    true north) and alt_error_arcmin (positive = axis points above the
    true pole's altitude, i.e. above site latitude), plus the raw
    mount_pole_alt_deg/mount_pole_az_deg for reference.
    """
    site = EarthLocation(
        lat=site_lat_deg * u.deg, lon=site_lon_deg * u.deg, height=site_height_m * u.m
    )

    c1 = SkyCoord(ra=ra1_deg * u.deg, dec=dec1_deg * u.deg, frame="icrs")
    c2 = SkyCoord(ra=ra2_deg * u.deg, dec=dec2_deg * u.deg, frame="icrs")
    aa1 = c1.transform_to(AltAz(obstime=time1, location=site))
    aa2 = c2.transform_to(AltAz(obstime=time2, location=site))

    v1 = _altaz_unit_vector(aa1.alt.deg, aa1.az.deg)
    v2 = _altaz_unit_vector(aa2.alt.deg, aa2.az.deg)

    # Candidate axes equidistant from v1 and v2 lie on the great circle
    # perpendicular to (v1 - v2). Build an orthonormal basis for that
    # circle's plane.
    n = v1 - v2
    n_norm = np.linalg.norm(n)
    if n_norm < 1e-9:
        raise PolarAlignmentError(
            "The two pointings are identical -- need a real commanded rotation between them"
        )
    n = n / n_norm
    helper = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(n, helper)
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(n, e1)

    def axis_at(t: float) -> np.ndarray:
        return np.cos(t) * e1 + np.sin(t) * e2

    # Initial guess: the parameter t0 corresponding to the true pole's own
    # position on this bisector circle (its actual projection, not
    # assumed exact -- real misalignment is expected to be a small
    # perturbation away from t0, which is what search_bracket_deg bounds).
    true_pole_vec = _altaz_unit_vector(site_lat_deg, 0.0)
    t0 = float(np.arctan2(np.dot(true_pole_vec, e2), np.dot(true_pole_vec, e1)))

    target_rad = np.radians(commanded_rotation_deg)

    def f(t: float) -> float:
        phi = _rotation_angle_about_axis(v1, v2, axis_at(t))
        return _wrap_deg_signed(np.degrees(phi - target_rad))

    def _try_bracket(bracket_deg: float) -> float | None:
        """Bisect for a root within +/-bracket_deg of the true pole's own
        position. Returns None (not a raised error) if the two endpoints
        don't straddle a root -- lets the caller retry wider rather than
        giving up on the first, possibly-too-narrow attempt."""
        bracket_rad = np.radians(bracket_deg)
        lo, hi = t0 - bracket_rad, t0 + bracket_rad
        f_lo, f_hi = f(lo), f(hi)
        if f_lo == 0.0:
            return lo
        if f_hi == 0.0:
            return hi
        if (f_lo > 0) == (f_hi > 0):
            return None
        for _ in range(60):
            mid = (lo + hi) / 2.0
            f_mid = f(mid)
            if f_mid == 0.0:
                return mid
            if (f_mid > 0) == (f_lo > 0):
                lo, f_lo = mid, f_mid
            else:
                hi, f_hi = mid, f_mid
        return (lo + hi) / 2.0

    # A real misalignment can easily exceed a tight first guess (this
    # branch's mount measured ~6.4 deg off in azimuth on a real run,
    # just outside the old fixed 5 deg search -- causing an intermittent,
    # confusing failure right at the edge of the window). Retry with a
    # progressively wider search before giving up, rather than failing on
    # the first, possibly-too-narrow attempt.
    attempted_brackets = [search_bracket_deg, search_bracket_deg * 3.0, search_bracket_deg * 6.0]
    t_solution: float | None = None
    for bracket_deg in attempted_brackets:
        t_solution = _try_bracket(bracket_deg)
        if t_solution is not None:
            break

    if t_solution is None:
        raise PolarAlignmentError(
            f"Couldn't pin down the polar axis error -- it looks like more than "
            f"{attempted_brackets[-1]:.0f} degrees off in azimuth and/or altitude, which is further than "
            "this method can reliably measure in one pass. This usually means either the mount is quite far "
            "from polar aligned (get it roughly pointed north/at the right altitude first, e.g. using the "
            "mount's built-in compass/altitude scale, then retry), or the two shots didn't actually differ by "
            "the RA amount this was expecting (a bad plate solve, or the mount didn't rotate as much as "
            "reported). Try again; if it keeps happening, increase the rotation step size."
        )

    mount_pole_vec = axis_at(t_solution)
    mount_pole_alt, mount_pole_az = _unit_vector_to_altaz(mount_pole_vec)

    alt_error_arcmin = (mount_pole_alt - site_lat_deg) * 60.0
    az_error_arcmin = _wrap_deg_signed(mount_pole_az) * 60.0

    return {
        "az_error_arcmin": az_error_arcmin,
        "alt_error_arcmin": alt_error_arcmin,
        "mount_pole_alt_deg": mount_pole_alt,
        "mount_pole_az_deg": mount_pole_az,
    }


def format_angle_arcmin(value_arcmin: float) -> str:
    """Render an arcmin magnitude as deg/arcmin/arcsec -- a raw arcmin
    number is hard to read at a glance once it's in the hundreds (this
    branch's actual first live reading was ~386 arcmin, i.e. over 6
    degrees), and arcsec resolution matters once it's small and you're
    trying to fine-tune the last bit of error."""
    total_arcsec = abs(value_arcmin) * 60.0
    deg, rem = divmod(total_arcsec, 3600.0)
    arcmin, arcsec = divmod(rem, 60.0)
    if deg >= 1:
        return f"{int(deg)}°{int(arcmin):02d}'{arcsec:04.1f}\""
    if arcmin >= 1:
        return f"{int(arcmin)}'{arcsec:04.1f}\""
    return f"{arcsec:.1f}\""


def describe_adjustment(az_error_arcmin: float, alt_error_arcmin: float) -> dict[str, Any]:
    """Structured, human-readable description of the solved polar axis
    error and the correction it implies.

    Deliberately reports the unambiguous physical direction the axis
    needs to MOVE (west/east, up/down) rather than "turn this knob
    clockwise" -- which specific adjuster direction corrects a given
    error depends on the mount's own adjuster mechanism (varies by
    model, and even by which side of a given bolt you're turning), and
    guessing that wrong is exactly the kind of mistake this module's
    synthetic validation was built to avoid. "Move the axis west" is
    mount-independent; "turn clockwise" is not.
    """
    az_error_dir = "east" if az_error_arcmin > 0 else "west"
    alt_error_dir = "above" if alt_error_arcmin > 0 else "below"
    az_move_dir = "west" if az_error_arcmin > 0 else "east"
    alt_move_dir = "down" if alt_error_arcmin > 0 else "up"
    az_str = format_angle_arcmin(az_error_arcmin)
    alt_str = format_angle_arcmin(alt_error_arcmin)
    text = (
        f"Move the axis {az_move_dir} by {az_str} (azimuth) and {alt_move_dir} by {alt_str} (altitude). "
        f"Currently {az_str} too far {az_error_dir} of true north, {alt_str} {alt_error_dir} true pole altitude."
    )
    return {
        "text": text,
        "az_move_direction": az_move_dir,
        "az_move_amount": az_str,
        "alt_move_direction": alt_move_dir,
        "alt_move_amount": alt_str,
    }


__all__ = ["solve_polar_axis_error", "describe_adjustment", "format_angle_arcmin", "PolarAlignmentError"]

"""Test-driven development of the all-sky polar alignment math core.

Ground truth generation: define a KNOWN, deliberately-wrong mount polar
axis (as an azimuth/altitude offset from the true pole), simulate what two
plate-solved positions WOULD be if a mount with that exact axis rotated by
a known commanded angle between two exposures, then confirm the solver
recovers the injected error exactly. This is the safety net for physically
instructive math -- a sign error here would tell a real person to turn a
real adjustment knob the wrong way.
"""

import sys

sys.path.insert(0, "/app")

import numpy as np
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time
import astropy.units as u

from app.services.polar_alignment import solve_polar_axis_error

failures = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


def rodrigues_rotate(v, axis, angle_rad):
    """Rotate vector v by angle_rad about unit vector axis."""
    axis = axis / np.linalg.norm(axis)
    return (
        v * np.cos(angle_rad)
        + np.cross(axis, v) * np.sin(angle_rad)
        + axis * np.dot(axis, v) * (1 - np.cos(angle_rad))
    )


def altaz_to_unit_vector(alt_deg, az_deg):
    """Standard astronomy convention: Az measured from North through East."""
    alt = np.radians(alt_deg)
    az = np.radians(az_deg)
    x = np.cos(alt) * np.cos(az)  # North component
    y = np.cos(alt) * np.sin(az)  # East component
    z = np.sin(alt)  # Up component
    return np.array([x, y, z])


def unit_vector_to_altaz(v):
    x, y, z = v
    alt = np.degrees(np.arcsin(np.clip(z, -1, 1)))
    az = np.degrees(np.arctan2(y, x)) % 360.0
    return alt, az


site_lat = 36.783579
site_lon = -75.958845
site = EarthLocation(lat=site_lat * u.deg, lon=site_lon * u.deg, height=3 * u.m)

# Injected error: mount's true rotation axis is offset from the real pole
# by az_error (positive = axis points east of true north) and alt_error
# (positive = axis points above true pole altitude i.e. above latitude).
for az_error_arcmin, alt_error_arcmin in [
    (15.0, -8.0),
    (-20.0, 25.0),
    (5.0, 5.0),
    (-3.0, -12.0),
]:
    true_pole_altaz = (site_lat, 0.0)  # Alt=latitude, Az=North(0)
    mount_pole_alt = site_lat + alt_error_arcmin / 60.0
    mount_pole_az = (0.0 + az_error_arcmin / 60.0) % 360.0
    mount_pole_vec = altaz_to_unit_vector(mount_pole_alt, mount_pole_az)

    # Pick a starting pointing (somewhere reasonably placed) and a known
    # commanded rotation angle (mount rotates its RA axis by this amount
    # between the two captures -- analogous to slewing ~60 deg of HA).
    t1 = Time("2026-09-20T02:00:00", scale="utc")
    start_alt, start_az = 55.0, 150.0
    v1 = altaz_to_unit_vector(start_alt, start_az)
    commanded_angle_deg = 60.0
    v2 = rodrigues_rotate(v1, mount_pole_vec, np.radians(commanded_angle_deg))
    alt2, az2 = unit_vector_to_altaz(v2)

    # Convert both ground-truth Alt/Az pointings to RA/Dec "solved
    # positions" at their respective (here, identical for simplicity --
    # timing offset between the two real exposures is a separate, much
    # smaller effect) observation time, exactly as a real plate solve
    # would report, so the solver under test only ever sees RA/Dec + time
    # + site, matching its real call signature.
    aa1 = AltAz(alt=start_alt * u.deg, az=start_az * u.deg, obstime=t1, location=site)
    aa2 = AltAz(alt=alt2 * u.deg, az=az2 * u.deg, obstime=t1, location=site)
    radec1 = SkyCoord(aa1).icrs
    radec2 = SkyCoord(aa2).icrs

    result = solve_polar_axis_error(
        ra1_deg=radec1.ra.deg,
        dec1_deg=radec1.dec.deg,
        time1=t1,
        ra2_deg=radec2.ra.deg,
        dec2_deg=radec2.dec.deg,
        time2=t1,
        commanded_rotation_deg=commanded_angle_deg,
        site_lat_deg=site_lat,
        site_lon_deg=site_lon,
        site_height_m=3.0,
    )

    az_recovered = result["az_error_arcmin"]
    alt_recovered = result["alt_error_arcmin"]

    check(
        f"az_error_arcmin recovered (injected={az_error_arcmin}, got={az_recovered:.3f})",
        abs(az_recovered - az_error_arcmin) < 0.5,
    )
    check(
        f"alt_error_arcmin recovered (injected={alt_error_arcmin}, got={alt_recovered:.3f})",
        abs(alt_recovered - alt_error_arcmin) < 0.5,
    )

print()
if failures:
    print(f"{len(failures)} FAILURE(S):", failures)
    sys.exit(1)
else:
    print("All checks passed.")

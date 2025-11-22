"""ADES/OBS80 report generation from measurements and submission logging."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterable

from sqlmodel import Session

from app.db.session import get_session
from app.models import Measurement, SubmissionLog


def validate_measurement(meas: Measurement) -> list[str]:
    flags: list[str] = []
    if meas.ra_deg is None or meas.dec_deg is None:
        flags.append("missing_coords")
    if meas.obs_time is None:
        flags.append("missing_obs_time")
    if not meas.station_code:
        flags.append("missing_station_code")
    return flags


def generate_ades(measurements: Iterable[Measurement]) -> str:
    """Produce a minimal ADES XML string for the given measurements."""

    rows = []
    for m in measurements:
        flags = validate_measurement(m)
        if flags:
            continue
        rows.append(
            f"""    <observation>
      <mpcCode>{m.station_code}</mpcCode>
      <measTime>{m.obs_time.isoformat()}Z</measTime>
      <ra>{m.ra_deg:.6f}</ra>
      <dec>{m.dec_deg:.6f}</dec>
      {"<mag>{:.2f}</mag>".format(m.magnitude) if m.magnitude is not None else ""}
      {"<band>{}</band>".format(m.band) if m.band else ""}
      {"<astErr>{:.2f}</astErr>".format(m.ra_uncert_arcsec or m.dec_uncert_arcsec) if (m.ra_uncert_arcsec or m.dec_uncert_arcsec) else ""}
      {"<astCat>{}</astCat>".format(m.software) if m.software else ""}
    </observation>"""
        )
    body = "\n".join(rows)
    return f"""<ades>
  <header>
    <creationDate>{dt.datetime.utcnow().isoformat()}Z</creationDate>
  </header>
  <data>
{body}
  </data>
</ades>"""


def generate_obs80(measurements: Iterable[Measurement]) -> str:
    """Produce a basic OBS80 text block (stub; real formatting needs full MPC fields)."""

    lines = []
    for m in measurements:
        flags = validate_measurement(m)
        if flags:
            continue
        line = f"{m.station_code or 'XXX'} {m.obs_time.isoformat()} RA={m.ra_deg:.6f} Dec={m.dec_deg:.6f}"
        if m.magnitude is not None:
            line += f" Mag={m.magnitude:.2f}{m.band or ''}"
        lines.append(line)
    return "\n".join(lines)


def mark_reviewed(measurements: Iterable[Measurement]) -> None:
    for m in measurements:
        m.reviewed = True


def archive_report(
    measurements: Iterable[Measurement],
    format: str = "ADES",
    session: Session | None = None,
    channel: str = "api",
    notes: str | None = None,
) -> SubmissionLog:
    body = generate_ades(measurements) if format.upper() == "ADES" else generate_obs80(measurements)
    target_path = Path("/data/reports") / f"report_{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.{format.lower()}"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(body, encoding="utf-8")
    ids = [m.id for m in measurements if m.id]

    def _persist(db: Session) -> SubmissionLog:
        log = SubmissionLog(
            channel=channel,
            status="pending",
            response=None,
            report_path=str(target_path),
            measurement_ids=json.dumps(ids),
            notes=notes,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    if session:
        return _persist(session)
    with get_session() as db:
        return _persist(db)


__all__ = ["generate_ades", "generate_obs80", "validate_measurement", "mark_reviewed"]

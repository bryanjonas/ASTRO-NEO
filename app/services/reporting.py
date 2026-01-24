"""Reporting service for generating ADES/MPC80 reports and handling submissions."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from sqlmodel import Session, select

from app.core.config import settings
from app.models import Measurement, SubmissionLog, SiteConfig


class ReportService:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def generate_ades(self, measurements: List[Measurement]) -> str:
        """Generate ADES XML for a list of measurements (single object)."""
        if not measurements:
            return ""

        # Group by object to ensure single object per file (as per requirement)
        target_name = measurements[0].target
        
        # Fetch SiteConfig for context
        site_config = self.session.exec(select(SiteConfig).where(SiteConfig.name == settings.site_name)).first()
        if not site_config:
            # Fallback defaults if no config
            site_config = SiteConfig(
                name=settings.site_name, 
                latitude=settings.site_latitude, 
                longitude=settings.site_longitude, 
                altitude_m=settings.site_altitude_m,
                telescope_design="Reflector",
                telescope_aperture=0.0,
                telescope_detector="CCD"
            )

        root = ET.Element("ades", version="2022") # Updated version
        obs_block = ET.SubElement(root, "obsBlock")
        
        # --- obsContext ---
        ctx = ET.SubElement(obs_block, "obsContext")
        
        # Observatory
        obs_el = ET.SubElement(ctx, "observatory")
        ET.SubElement(obs_el, "mpcCode").text = settings.station_code
        ET.SubElement(obs_el, "name").text = site_config.name
        
        # Submitter
        sub_el = ET.SubElement(ctx, "submitter")
        ET.SubElement(sub_el, "name").text = settings.observer_initials # Should be full name ideally
        
        # Measurers
        meas_el = ET.SubElement(ctx, "measurers")
        ET.SubElement(meas_el, "name").text = settings.observer_initials
        
        # Telescope
        tel_el = ET.SubElement(ctx, "telescope")
        ET.SubElement(tel_el, "design").text = site_config.telescope_design
        ET.SubElement(tel_el, "aperture").text = f"{site_config.telescope_aperture:.2f}"
        ET.SubElement(tel_el, "detector").text = site_config.telescope_detector
        
        # --- obsData ---
        data_el = ET.SubElement(obs_block, "obsData")
        
        for m in measurements:
            # We assume optical for now
            obs = ET.SubElement(data_el, "optical")
            
            # Identification Group
            ET.SubElement(obs, "provID").text = m.target
            ET.SubElement(obs, "trkSub").text = m.target # Using target as tracklet ID for now
            ET.SubElement(obs, "mode").text = "CCD"
            ET.SubElement(obs, "stn").text = m.station_code or settings.station_code
            
            # Location Group (not needed for fixed station)
            
            # Observation Group
            ET.SubElement(obs, "obsTime").text = m.obs_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            ET.SubElement(obs, "ra").text = f"{m.ra_deg:.7f}"
            ET.SubElement(obs, "dec").text = f"{m.dec_deg:.7f}"
            ET.SubElement(obs, "astCat").text = m.ast_cat or "Gaia2"
            
            if m.ra_uncert_arcsec:
                ET.SubElement(obs, "rmsRA").text = f"{m.ra_uncert_arcsec:.3f}"
            if m.dec_uncert_arcsec:
                ET.SubElement(obs, "rmsDec").text = f"{m.dec_uncert_arcsec:.3f}"
                
            # Photometry Group
            if m.magnitude:
                ET.SubElement(obs, "mag").text = f"{m.magnitude:.2f}"
                ET.SubElement(obs, "band").text = m.band or "R"
            
        # Pretty print XML
        from xml.dom import minidom
        xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
        return xml_str

    def generate_ades_psv(self, measurements: List[Measurement]) -> str:
        """Generate ADES PSV payload for a list of measurements."""
        if not measurements:
            return ""

        targets = sorted({m.target for m in measurements})
        target_label = targets[0] if len(targets) == 1 else f"{targets[0]}+{len(targets) - 1}"
        logging.info("Generating ADES PSV for target=%s (count=%d)", target_label, len(measurements))
        site_config = self.session.exec(select(SiteConfig).where(SiteConfig.name == settings.site_name)).first() if self.session else None
        if not site_config:
            site_config = SiteConfig(
                name=settings.site_name,
                latitude=settings.site_latitude,
                longitude=settings.site_longitude,
                altitude_m=settings.site_altitude_m,
                telescope_design="Reflector",
                telescope_aperture=0.0,
                telescope_detector="CCD",
            )

        catalog = measurements[0].ast_cat or "GaiaDR3"
        telescope = f"{site_config.telescope_design} {site_config.telescope_aperture:.2f}m".strip()
        instrument = site_config.telescope_detector

        lines = [
            "# version=2017",
            f"# observatoryCode={settings.station_code}",
            f"# submitter={settings.observer_initials}",
            f"# observer={settings.observer_initials}",
            f"# measurer={settings.observer_initials}",
            f"# telescope={telescope}",
            f"# instrument={instrument}",
            f"# catalog={catalog}",
            "permID provID obsTime ra dec sigRA sigDec mag band",
        ]

        for m in measurements:
            obs_time = m.obs_time
            if obs_time.tzinfo is None:
                obs_time = obs_time.replace(tzinfo=timezone.utc)
            else:
                obs_time = obs_time.astimezone(timezone.utc)
            timestamp = obs_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]

            sig_ra = m.ra_uncert_arcsec
            sig_dec = m.dec_uncert_arcsec
            if sig_ra is None or sig_dec is None:
                fallback = settings.astrometry_default_seeing_arcsec or 1.0
                sig_ra = sig_ra or fallback
                sig_dec = sig_dec or fallback

            mag = "" if m.magnitude is None else f"{m.magnitude:.2f}"
            band = "" if not m.band else str(m.band)

            prov_id = m.target or ""
            fields = [
                "",
                prov_id,
                timestamp,
                f"{m.ra_deg:.7f}",
                f"{m.dec_deg:.7f}",
                f"{sig_ra:.3f}",
                f"{sig_dec:.3f}",
                mag,
                band,
            ]
            row = " ".join(fields).rstrip()
            logging.debug(
                "PSV row: target=%s obs_time=%s ra=%.7f dec=%.7f sig_ra=%.3f sig_dec=%.3f mag=%s band=%s",
                prov_id,
                timestamp,
                m.ra_deg,
                m.dec_deg,
                sig_ra,
                sig_dec,
                mag or "",
                band or "",
            )
            lines.append(row)

        return "\n".join(lines) + "\n"

    def validate_ades_psv(self, psv_text: str) -> tuple[bool, list[str]]:
        """Validate PSV content against minimal ADES requirements."""
        errors: list[str] = []
        try:
            psv_text.encode("ascii")
        except UnicodeEncodeError:
            errors.append("Non-ASCII characters found in PSV content.")
            return False, errors

        lines = [line.rstrip("\n") for line in psv_text.splitlines() if line.strip() != ""]
        if not lines:
            return False, ["Empty PSV payload."]

        header = [line for line in lines if line.startswith("#")]
        if not header:
            return False, ["Missing PSV header block."]

        required_keys = {
            "version",
            "observatoryCode",
            "submitter",
            "observer",
            "measurer",
            "telescope",
            "instrument",
            "catalog",
        }
        present_keys = set()
        for line in header:
            if "=" in line:
                key = line[1:].split("=", 1)[0].strip()
                present_keys.add(key)
        missing = required_keys - present_keys
        if missing:
            errors.append(f"Missing PSV header fields: {', '.join(sorted(missing))}")

        schema_line_index = next((i for i, line in enumerate(lines) if not line.startswith("#")), None)
        if schema_line_index is None:
            errors.append("Missing PSV schema line.")
            return False, errors

        schema_line = lines[schema_line_index]
        expected_schema = "permID provID obsTime ra dec sigRA sigDec mag band"
        if schema_line.strip() != expected_schema:
            errors.append("PSV schema line does not match ADES spec.")

        rows = lines[schema_line_index + 1 :]
        if len(rows) < 4:
            errors.append("Fewer than 4 observation rows.")

        prev_time = None
        seen_times = set()
        for idx, row in enumerate(rows, start=1):
            parts = row.split()
            if len(parts) < 7:
                errors.append(f"Row {idx} has insufficient columns.")
                continue

            if len(parts) == 7:
                parts = ["", parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], "", ""]
            elif len(parts) == 8:
                parts = ["", parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6], parts[7]]
            else:
                parts = parts[:9]

            obs_time = parts[2]
            if obs_time in seen_times:
                errors.append(f"Duplicate obsTime at row {idx}.")
            seen_times.add(obs_time)
            try:
                parsed_time = datetime.strptime(obs_time, "%Y-%m-%dT%H:%M:%S.%f")
            except ValueError:
                errors.append(f"Invalid obsTime format at row {idx}.")
                continue
            if prev_time and parsed_time <= prev_time:
                errors.append(f"Non-monotonic obsTime at row {idx}.")
            prev_time = parsed_time

            try:
                ra = float(parts[3])
                dec = float(parts[4])
                sig_ra = float(parts[5])
                sig_dec = float(parts[6])
            except ValueError:
                errors.append(f"Non-numeric value in row {idx}.")
                continue

            if not (0.0 <= ra < 360.0):
                errors.append(f"RA out of range at row {idx}.")
            if not (-90.0 <= dec <= 90.0):
                errors.append(f"Dec out of range at row {idx}.")
            if sig_ra <= 0 or sig_dec <= 0:
                errors.append(f"Non-positive uncertainty at row {idx}.")

        return len(errors) == 0, errors

    def write_ades_psv_bundle(
        self,
        measurements: List[Measurement],
        output_dir: str | None = None,
        bundle_label: str | None = None,
    ) -> dict[str, Any]:
        """Generate PSV, validate it, and persist PSV/validation/metadata bundle."""
        measurements = sorted(measurements, key=lambda m: m.obs_time)
        output_dir = output_dir or settings.psv_output_dir
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        logging.info("Writing PSV bundle to %s", output_path)

        target = bundle_label or (measurements[0].target if measurements else "UNKNOWN")
        safe_target = re.sub(r"[^A-Za-z0-9_-]+", "_", target)
        date_str = measurements[0].obs_time.strftime("%Y%m%d") if measurements else datetime.utcnow().strftime("%Y%m%d")
        filename = f"OBS{settings.station_code}_{date_str}_{safe_target}.ades.psv"
        psv_path = output_path / filename
        validation_path = output_path / f"{psv_path.stem}.validation.log"
        metadata_path = output_path / f"{psv_path.stem}.metadata.json"

        psv_text = self.generate_ades_psv(measurements)
        is_valid, errors = self.validate_ades_psv(psv_text)
        if not is_valid:
            logging.error("PSV validation failed with %d errors", len(errors))

        psv_path.write_text(psv_text, encoding="ascii")
        validation_lines = []
        if is_valid:
            validation_lines.append("VALIDATION: OK")
        else:
            validation_lines.append("VALIDATION: FAILED")
            validation_lines.extend([f"ERROR: {err}" for err in errors])
        validation_path.write_text("\n".join(validation_lines) + "\n", encoding="ascii")

        sha256 = hashlib.sha256(psv_text.encode("ascii")).hexdigest()
        targets = sorted({m.target for m in measurements})
        metadata = {
            "target": target,
            "targets": targets,
            "station_code": settings.station_code,
            "measurement_ids": [m.id for m in measurements if m.id],
            "count": len(measurements),
            "hash_sha256": sha256,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "psv_path": str(psv_path),
            "validation_path": str(validation_path),
            "valid": is_valid,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="ascii")

        return {
            "psv_path": str(psv_path),
            "validation_path": str(validation_path),
            "metadata_path": str(metadata_path),
            "valid": is_valid,
            "errors": errors,
        }

    def validate_ades(self, xml_content: str) -> tuple[bool, str]:
        """Validate ADES XML against the XSD schema."""
        try:
            from lxml import etree
            
            # Load schema
            # We assume the schema is in the documentation folder
            schema_path = "documentation/submit.xsd"
            with open(schema_path, 'rb') as f:
                schema_root = etree.XML(f.read())
            schema = etree.XMLSchema(schema_root)
            
            # Parse XML
            parser = etree.XMLParser(schema=schema)
            etree.fromstring(xml_content.encode('utf-8'), parser)
            
            return True, "Valid"
        except ImportError:
            return False, "Validation skipped: lxml not installed"
        except Exception as e:
            return False, str(e)

    def generate_mpc80(self, measurements: List[Measurement]) -> str:
        """Generate legacy 80-column MPC format."""
        lines = []
        for m in measurements:
            # Format: 
            #     ZK24B010  C2024 01 21.12345 01 23 45.67 +12 34 56.7          18.5 R      H06
            # Columns:
            # 0-4: Packed Prov ID (or 5-11 for unpacked)
            # 14: Note 1 (C=CCD)
            # 15-31: Date (YYYY MM DD.ddddd)
            # 32-43: RA (HH MM SS.ss)
            # 44-55: Dec (+DD MM SS.s)
            # 65-69: Mag
            # 70: Band
            # 77-79: Station Code
            
            # Simplified generation (needs rigorous formatting)
            # Using astropy for coordinate conversion to sexagesimal
            from astropy.coordinates import SkyCoord
            from astropy import units as u
            
            c = SkyCoord(ra=m.ra_deg*u.deg, dec=m.dec_deg*u.deg)
            ra_hms = c.ra.hms
            dec_dms = c.dec.dms
            
            # Date
            dt = m.obs_time
            day_fraction = dt.day + (dt.hour + dt.minute/60 + dt.second/3600) / 24.0
            date_str = f"{dt.year:04d} {dt.month:02d} {day_fraction:08.5f}"
            
            # RA
            ra_str = f"{int(ra_hms.h):02d} {int(ra_hms.m):02d} {ra_hms.s:05.2f}"
            
            # Dec
            sign = "+" if dec_dms.d >= 0 else "-"
            dec_abs = abs(dec_dms.d)
            dec_str = f"{sign}{int(dec_abs):02d} {int(abs(dec_dms.m)):02d} {abs(dec_dms.s):04.1f}"
            
            # Mag
            mag_str = f"{m.magnitude:5.1f}" if m.magnitude else "     "
            band = m.band or " "
            
            # Station
            stn = m.station_code or settings.station_code or "XXX"
            
            # Target (truncate to 12 chars for unpacked, or pack it)
            # For now, just use first 12 chars
            target = (m.target + "            ")[:12]
            
            line = f"{target}  C{date_str} {ra_str} {dec_str}          {mag_str} {band}      {stn}"
            lines.append(line)
            
        return "\n".join(lines)

    def submit_report(self, payload: str, channel: str = "email", measurement_ids: List[int] = []) -> SubmissionLog:
        """Submit the report via the specified channel."""
        # Validate if ADES
        validation_status = "Not Validated"
        if payload.strip().startswith("<"):
            is_valid, msg = self.validate_ades(payload)
            validation_status = "Valid" if is_valid else f"Invalid: {msg}"
            if not is_valid:
                logging.warning(f"ADES Validation Failed: {msg}")
                # We might want to block submission here, but for now just log it
        
        # Mock submission for now
        status = "sent"
        response = f"Mock submission successful. Validation: {validation_status}"
        
        if channel == "email":
            # TODO: Implement email sending
            pass
        elif channel == "api":
            # TODO: Implement MPC API
            pass
            
        log = SubmissionLog(
            channel=channel,
            status=status,
            response=response,
            report_path=None, # We could save to disk
            measurement_ids=json.dumps(measurement_ids),
            notes=f"Submitted {len(measurement_ids)} observations. {validation_status}"
        )
        
        if self.session:
            self.session.add(log)
            self.session.commit()
            self.session.refresh(log)
            
        return log


def archive_report(measurements: List[Measurement], format: str = "ADES", session: Session | None = None) -> SubmissionLog:
    """Legacy wrapper for archiving a report."""
    svc = ReportService(session)
    if format.upper() == "ADES":
        payload = svc.generate_ades(measurements)
    else:
        payload = svc.generate_mpc80(measurements)
        
    # "Archive" implies saving but not necessarily submitting?
    # The original usage suggests it returns a log.
    # Let's use submit_report with a special channel or just reuse it.
    return svc.submit_report(payload, channel="archive", measurement_ids=[m.id for m in measurements if m.id])

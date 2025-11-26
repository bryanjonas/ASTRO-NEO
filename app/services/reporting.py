"""Reporting service for generating ADES/MPC80 reports and handling submissions."""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
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


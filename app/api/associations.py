"""Association management API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import get_db
from app.models import CandidateAssociation, CaptureLog
from app.services.analysis import AnalysisService

router = APIRouter(prefix="/associations", tags=["associations"])


class ManualAssociationPayload(BaseModel):
    """Payload for manually creating or correcting an association."""
    capture_id: int = Field(..., description="ID of the capture log entry")
    ra_deg: float = Field(..., description="Right Ascension in degrees")
    dec_deg: float = Field(..., description="Declination in degrees")
    click_x: float | None = Field(None, description="Original click X coordinate (optional)")
    click_y: float | None = Field(None, description="Original click Y coordinate (optional)")


class AssociationUpdatePayload(BaseModel):
    """Payload for updating an existing association."""
    ra_deg: float = Field(..., description="Updated Right Ascension in degrees")
    dec_deg: float = Field(..., description="Updated Declination in degrees")


@router.get("/")
def list_associations(
    capture_id: int | None = None,
    limit: int = 50,
    session: Session = Depends(get_db),
) -> list[CandidateAssociation]:
    """
    List associations, optionally filtered by capture ID.

    Returns associations ordered by creation date (newest first).
    """
    stmt = select(CandidateAssociation).order_by(CandidateAssociation.created_at.desc()).limit(limit)

    if capture_id is not None:
        stmt = stmt.where(CandidateAssociation.capture_id == capture_id)

    return session.exec(stmt).all()


@router.get("/{association_id}")
def get_association(
    association_id: int,
    session: Session = Depends(get_db),
) -> CandidateAssociation:
    """Get a specific association by ID."""
    assoc = session.get(CandidateAssociation, association_id)
    if not assoc:
        raise HTTPException(status_code=404, detail="Association not found")
    return assoc


@router.post("/")
def create_manual_association(
    payload: ManualAssociationPayload,
    session: Session = Depends(get_db),
) -> CandidateAssociation:
    """
    Manually create or correct an association.

    This is used when the user clicks on an image to specify the asteroid location.
    The frontend should use /dashboard/analysis/resolve_click first to get precise
    centroid, then call this endpoint to store the association.
    """
    # Verify capture exists
    capture = session.get(CaptureLog, payload.capture_id)
    if not capture:
        raise HTTPException(status_code=404, detail="Capture not found")

    # Check if association already exists
    existing = session.exec(
        select(CandidateAssociation).where(
            CandidateAssociation.capture_id == payload.capture_id
        )
    ).first()

    if existing:
        # Update existing association (mark as manually corrected)
        existing.ra_deg = payload.ra_deg
        existing.dec_deg = payload.dec_deg
        existing.method = "corrected"
        existing.updated_at = datetime.utcnow()

        session.add(existing)
        session.commit()
        session.refresh(existing)

        return existing

    # Create new manual association
    assoc = CandidateAssociation(
        capture_id=payload.capture_id,
        ra_deg=payload.ra_deg,
        dec_deg=payload.dec_deg,
        method="manual",
        created_at=datetime.utcnow(),
    )

    session.add(assoc)
    session.commit()
    session.refresh(assoc)

    return assoc


@router.patch("/{association_id}")
def update_association(
    association_id: int,
    payload: AssociationUpdatePayload,
    session: Session = Depends(get_db),
) -> CandidateAssociation:
    """
    Update an existing association's position.

    Marks the association as manually corrected.
    """
    assoc = session.get(CandidateAssociation, association_id)
    if not assoc:
        raise HTTPException(status_code=404, detail="Association not found")

    assoc.ra_deg = payload.ra_deg
    assoc.dec_deg = payload.dec_deg
    assoc.method = "corrected" if assoc.method == "auto" else "manual"
    assoc.updated_at = datetime.utcnow()

    session.add(assoc)
    session.commit()
    session.refresh(assoc)

    return assoc


@router.delete("/{association_id}")
def delete_association(
    association_id: int,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Delete an association."""
    assoc = session.get(CandidateAssociation, association_id)
    if not assoc:
        raise HTTPException(status_code=404, detail="Association not found")

    session.delete(assoc)
    session.commit()

    return {"success": True, "deleted_id": association_id}


@router.post("/auto/{capture_id}")
def run_auto_association(
    capture_id: int,
    use_star_subtraction: bool = True,
    session: Session = Depends(get_db),
) -> CandidateAssociation:
    """
    Manually trigger auto-association for a specific capture.

    This is useful for re-running association after ephemeris updates
    or for captures that weren't auto-associated initially.
    """
    capture = session.get(CaptureLog, capture_id)
    if not capture:
        raise HTTPException(status_code=404, detail="Capture not found")

    # Check if WCS file exists
    from pathlib import Path
    from astropy.wcs import WCS

    wcs_path = Path(capture.path).with_suffix('.wcs')
    if not wcs_path.exists():
        raise HTTPException(
            status_code=400,
            detail="No WCS file found - plate solve the image first"
        )

    try:
        wcs = WCS(str(wcs_path))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load WCS: {e}"
        )

    # Run association
    analysis = AnalysisService(session)
    assoc = analysis.auto_associate(
        session,
        capture,
        wcs,
        use_star_subtraction=use_star_subtraction
    )

    if not assoc:
        raise HTTPException(
            status_code=404,
            detail="No match found - check ephemeris and image quality"
        )

    return assoc


@router.get("/capture/{capture_id}/status")
def get_capture_association_status(
    capture_id: int,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Get association status for a capture.

    Returns information about whether the capture has been associated,
    the method used, and quality metrics.
    """
    capture = session.get(CaptureLog, capture_id)
    if not capture:
        raise HTTPException(status_code=404, detail="Capture not found")

    assoc = session.exec(
        select(CandidateAssociation).where(
            CandidateAssociation.capture_id == capture_id
        )
    ).first()

    if not assoc:
        return {
            "associated": False,
            "capture_id": capture_id,
            "target": capture.target,
            "path": capture.path,
        }

    return {
        "associated": True,
        "capture_id": capture_id,
        "target": capture.target,
        "path": capture.path,
        "association": {
            "id": assoc.id,
            "ra_deg": assoc.ra_deg,
            "dec_deg": assoc.dec_deg,
            "predicted_ra_deg": assoc.predicted_ra_deg,
            "predicted_dec_deg": assoc.predicted_dec_deg,
            "residual_arcsec": assoc.residual_arcsec,
            "snr": assoc.snr,
            "peak_counts": assoc.peak_counts,
            "method": assoc.method,
            "stars_subtracted": assoc.stars_subtracted,
            "created_at": assoc.created_at,
            "updated_at": assoc.updated_at,
        },
    }

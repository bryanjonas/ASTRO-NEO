"""Fetch and persist MPC NEOCP entries."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Tuple

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import get_session
from app.models import NeoCandidate

logger = logging.getLogger(__name__)

INPUT_MARKER = 'name="obj"'
FORMAT_TEXT = "text"
FORMAT_HTML = "html"
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
STATUS_RE = re.compile(r"\b(Updated|Added)\b\s+(?P<ts>[^\\[]*?UT)", re.IGNORECASE)


@dataclass
class CandidatePayload:
    trksub: str
    score: int | None = None
    observations: int | None = None
    observed_ut: str | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    vmag: float | None = None
    status: str | None = None
    status_ut: str | None = None
    raw_entry: str | None = None


@dataclass
class CandidateDiff:
    """Result of diffing parsed payloads against existing DB state."""

    new: list[str]
    updated: list[str]

    @property
    def total_changes(self) -> int:
        return len(self.new) + len(self.updated)


def fetch_neocp_feed(use_local: bool | None = None) -> Tuple[str, str, str]:
    """Fetch the NEOCP feed (text preferred, HTML fallback)."""

    if use_local or settings.neocp_use_local_sample:
        local_text = _load_local_text(Path(settings.neocp_local_text))
        if local_text is not None:
            return local_text, FORMAT_TEXT, f"file://{settings.neocp_local_text}"
        local_html = _load_local_html(Path(settings.neocp_local_html))
        return local_html, FORMAT_HTML, f"file://{settings.neocp_local_html}"

    try:
        with httpx.Client(timeout=settings.neocp_fetch_timeout) as client:
            response = client.get(settings.neocp_text_url)
            response.raise_for_status()
            return response.text, FORMAT_TEXT, settings.neocp_text_url
    except Exception as exc:  # pragma: no cover - network errors are handled gracefully
        logger.warning("Failed to fetch neocp.txt (%s); falling back to HTML page", exc)
        with httpx.Client(timeout=settings.neocp_fetch_timeout) as client:
            response = client.get(settings.neocp_html_url)
            response.raise_for_status()
            return response.text, FORMAT_HTML, settings.neocp_html_url


def parse_candidates(raw_text: str, source_format: str = FORMAT_TEXT) -> List[CandidatePayload]:
    """Extract candidate rows from the MPC feed."""

    candidates: List[CandidatePayload] = []
    if source_format == FORMAT_HTML:
        for raw_line in raw_text.splitlines():
            if INPUT_MARKER not in raw_line:
                continue
            clean_line = _strip_line(raw_line)
            payload = _parse_candidate_line(clean_line, raw_line.strip())
            if payload:
                candidates.append(payload)
        return candidates

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        payload = _parse_candidate_line(line, line)
        if payload:
            candidates.append(payload)
    return candidates


def refresh_neocp_candidates(use_local: bool | None = None) -> List[NeoCandidate]:
    """Fetch, parse, and persist the latest MPC entries."""

    feed, source_format, _source_url = fetch_neocp_feed(use_local=use_local)
    payloads = parse_candidates(feed, source_format=source_format)
    return sync_candidates(payloads)


def sync_candidates(
    payloads: Iterable[CandidatePayload], session: Session | None = None
) -> List[NeoCandidate]:
    """Upsert parsed candidates into the neocandidate table."""

    def _sync(db: Session) -> List[NeoCandidate]:
        results: List[NeoCandidate] = []
        now = datetime.utcnow()
        for payload in payloads:
            existing = db.exec(
                select(NeoCandidate).where(NeoCandidate.trksub == payload.trksub)
            ).first()
            if existing:
                _apply_payload(existing, payload, now)
                results.append(existing)
            else:
                model = NeoCandidate(
                    trksub=payload.trksub,
                    score=payload.score,
                    observations=payload.observations,
                    observed_ut=payload.observed_ut,
                    ra_deg=payload.ra_deg,
                    dec_deg=payload.dec_deg,
                    vmag=payload.vmag,
                    status=payload.status,
                    status_ut=payload.status_ut,
                    raw_entry=payload.raw_entry,
                    created_at=now,
                    updated_at=now,
                )
                db.add(model)
                results.append(model)
        db.commit()
        for record in results:
            db.refresh(record)
        return results

    if session:
        return _sync(session)

    with get_session() as db_session:
        return _sync(db_session)


def _apply_payload(model: NeoCandidate, payload: CandidatePayload, timestamp: datetime) -> None:
    model.score = payload.score
    model.observations = payload.observations
    model.observed_ut = payload.observed_ut
    model.ra_deg = payload.ra_deg
    model.dec_deg = payload.dec_deg
    model.vmag = payload.vmag
    model.status = payload.status
    model.status_ut = payload.status_ut
    model.raw_entry = payload.raw_entry
    model.updated_at = timestamp


def _strip_line(line: str) -> str:
    """Remove HTML tags and normalize whitespace."""

    without_tags = TAG_RE.sub("", line)
    return SPACE_RE.sub(" ", without_tags).strip()


def _parse_candidate_line(clean_line: str, raw_line: str) -> CandidatePayload | None:
    if "[" in clean_line and "]" in clean_line:
        return _parse_legacy_candidate_line(clean_line, raw_line)
    return _parse_modern_candidate_line(clean_line, raw_line)


def _parse_legacy_candidate_line(clean_line: str, raw_line: str) -> CandidatePayload | None:
    prefix, remainder = clean_line.split("[", 1)
    bracket_text, suffix = remainder.split("]", 1)
    tokens = prefix.strip().split()
    if not tokens:
        return None

    trksub = tokens[0]
    score = _safe_int(tokens[1]) if len(tokens) > 1 else None
    observations = _safe_int(tokens[2]) if len(tokens) > 2 else None
    observed_ut = bracket_text.split("UT")[0].strip(" .")

    ra_str = _extract_value(bracket_text, "R.A.")
    dec_str = _extract_value(bracket_text, "Decl.")
    vmag = _safe_float(_extract_value(bracket_text, "V"))

    status, status_ut = _parse_status(suffix)

    return CandidatePayload(
        trksub=trksub,
        score=score,
        observations=observations,
        observed_ut=observed_ut or None,
        ra_deg=_parse_ra(ra_str) if ra_str else None,
        dec_deg=_parse_dec(dec_str) if dec_str else None,
        vmag=vmag,
        status=status,
        status_ut=status_ut,
        raw_entry=raw_line,
    )


def _parse_modern_candidate_line(clean_line: str, raw_line: str) -> CandidatePayload | None:
    tokens = clean_line.split()
    if len(tokens) < 8:
        return None

    trksub = tokens[0]
    score = _safe_int(tokens[1]) if len(tokens) > 1 else None

    year = _safe_int(tokens[2]) if len(tokens) > 2 else None
    month = _safe_int(tokens[3]) if len(tokens) > 3 else None
    day_token = tokens[4] if len(tokens) > 4 else None
    ra_hours = _safe_float(tokens[5]) if len(tokens) > 5 else None
    dec_deg = _safe_float(tokens[6]) if len(tokens) > 6 else None
    vmag = _safe_float(tokens[7]) if len(tokens) > 7 else None

    observed_ut = None
    if year is not None and month is not None and day_token:
        observed_ut = f"{year:04d}-{month:02d}-{day_token} UT"

    ra_deg = ra_hours * 15.0 if ra_hours is not None else None

    status: str | None = None
    status_ut: str | None = None
    observations: int | None = None

    ut_index: int | None = None
    for idx, token in enumerate(tokens):
        if token.upper() == "UT":
            ut_index = idx
            break

    if ut_index is not None:
        status_index = ut_index - 3
        if status_index >= 0 and tokens[status_index].lower() in {"added", "updated"}:
            status = tokens[status_index].capitalize()
            status_ut = " ".join(tokens[status_index + 1 : ut_index + 1]) or None

        for tail_token in tokens[ut_index + 1 :]:
            observations = _safe_int(tail_token)
            if observations is not None:
                break

    return CandidatePayload(
        trksub=trksub,
        score=score,
        observations=observations,
        observed_ut=observed_ut,
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        vmag=vmag,
        status=status,
        status_ut=status_ut,
        raw_entry=raw_line,
    )


def _parse_status(suffix: str) -> tuple[str | None, str | None]:
    cleaned = suffix.replace("*", " ")
    cleaned = re.sub(r"\[.*?\]", "", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return None, None

    match = STATUS_RE.search(cleaned)
    if match:
        status = match.group(1).capitalize()
        timestamp = match.group("ts").strip()
        return status, timestamp
    return None, None


def _extract_value(text: str, label: str) -> str | None:
    pattern = rf"{re.escape(label)}\s*=\s*([^,]+)"
    match = re.search(pattern, text)
    if match:
        return match.group(1).strip()
    return None


def _parse_ra(value: str) -> float | None:
    parts = value.replace("h", " ").split()
    if not parts:
        return None
    try:
        hours = float(parts[0])
        minutes = float(parts[1]) if len(parts) > 1 else 0.0
        seconds = float(parts[2]) if len(parts) > 2 else 0.0
    except ValueError:
        return None
    total_hours = hours + minutes / 60.0 + seconds / 3600.0
    return total_hours * 15.0


def _parse_dec(value: str) -> float | None:
    value = value.replace("d", " ").strip()
    if not value:
        return None
    sign = -1 if value.startswith("-") else 1
    stripped = value.lstrip("+-")
    parts = stripped.split()
    try:
        degrees = float(parts[0])
        minutes = float(parts[1]) if len(parts) > 1 else 0.0
        seconds = float(parts[2]) if len(parts) > 2 else 0.0
    except ValueError:
        return None
    total = degrees + minutes / 60.0 + seconds / 3600.0
    return sign * total


def _safe_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _safe_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _load_local_html(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Local NEOCP sample not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_local_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def diff_candidate_payloads(
    payloads: Iterable[CandidatePayload], session: Session
) -> CandidateDiff:
    """Return lists of new and updated trksubs compared to DB state."""

    existing = {
        candidate.trksub: candidate
        for candidate in session.exec(select(NeoCandidate)).all()
    }
    new: list[str] = []
    updated: list[str] = []

    for payload in payloads:
        current = existing.get(payload.trksub)
        if not current:
            new.append(payload.trksub)
            continue
        if _payload_differs(current, payload):
            updated.append(payload.trksub)
    return CandidateDiff(new=new, updated=updated)


def _payload_differs(existing: NeoCandidate, payload: CandidatePayload) -> bool:
    """Return True if any tracked column differs between DB and payload."""

    return any(
        [
            existing.score != payload.score,
            existing.observations != payload.observations,
            existing.observed_ut != payload.observed_ut,
            existing.ra_deg != payload.ra_deg,
            existing.dec_deg != payload.dec_deg,
            existing.vmag != payload.vmag,
            existing.status != payload.status,
            existing.status_ut != payload.status_ut,
            existing.raw_entry != payload.raw_entry,
        ]
    )


__all__ = [
    "CandidatePayload",
    "CandidateDiff",
    "fetch_neocp_feed",
    "parse_candidates",
    "refresh_neocp_candidates",
    "sync_candidates",
    "diff_candidate_payloads",
]

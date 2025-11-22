"""Submission helpers (email/API stubs) and logging."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable
import json

from sqlmodel import Session

from app.core.config import settings
from app.db.session import get_session
from app.models import Measurement, SubmissionLog
from app.services.reporting import archive_report
from app.services.notifications import NOTIFICATIONS


class SubmissionService:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def submit(self, measurements: Iterable[Measurement], format: str = "ADES") -> SubmissionLog:
        log = archive_report(measurements, format=format, channel=settings.submission_channel, session=self.session)
        if settings.submission_channel == "email":
            self._send_email(log)
            log.status = "sent"
        else:
            log.status = "pending"
        self._save_log(log)
        return log

    def update_status(self, submission_id: int, status: str, response: str | None = None) -> SubmissionLog:
        def _update(db: Session) -> SubmissionLog:
            log = db.get(SubmissionLog, submission_id)
            if not log:
                raise ValueError("submission_not_found")
            log.status = status
            if response is not None:
                log.response = response
            db.add(log)
            db.commit()
            db.refresh(log)
            NOTIFICATIONS.add("info" if status == "acked" else "warn", f"Submission {status}", {"id": submission_id})
            return log

        if self.session:
            return _update(self.session)
        with get_session() as db:
            return _update(db)

    def _save_log(self, log: SubmissionLog) -> None:
        def _persist(db: Session) -> None:
            db.add(log)
            db.commit()
            db.refresh(log)

        if self.session:
            _persist(self.session)
        else:
            with get_session() as db:
                _persist(db)

    def _send_email(self, log: SubmissionLog) -> None:
        if not settings.mpc_email:
            return
        msg = EmailMessage()
        msg["From"] = settings.mpc_email
        msg["To"] = settings.mpc_email
        msg["Subject"] = "MPC Submission"
        body = "Attached ADES/OBS80 submission\n"
        msg.set_content(body)
        if log.report_path and Path(log.report_path).exists():
            payload = Path(log.report_path).read_text(encoding="utf-8")
            msg.add_attachment(payload, filename=Path(log.report_path).name)
        try:
            with smtplib.SMTP("localhost") as smtp:
                smtp.send_message(msg)
            log.response = json.dumps({"channel": "email", "status": "sent"})
        except Exception as exc:  # pragma: no cover - depends on local SMTP
            log.status = "failed"
            log.response = json.dumps({"error": str(exc)})

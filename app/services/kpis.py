"""KPI aggregation helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.db.session import get_session
from app.models import AstrometricSolution, SubmissionLog


class KPIService:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def _query(self, stmt):
        if self.session:
            return self.session.exec(stmt)
        with get_session() as db:
            return db.exec(stmt)

    def daily_counts(self, days: int = 7) -> dict:
        cutoff = datetime.utcnow() - timedelta(days=days)
        solves = self._query(
            select(AstrometricSolution).where(AstrometricSolution.solved_at >= cutoff)
        ).all()
        submissions = self._query(
            select(SubmissionLog).where(SubmissionLog.created_at >= cutoff)
        ).all()
        solved_per_day: dict[str, int] = {}
        for s in solves:
            day = s.solved_at.date().isoformat()
            solved_per_day[day] = solved_per_day.get(day, 0) + 1
        submissions_per_day: dict[str, int] = {}
        for sub in submissions:
            day = sub.created_at.date().isoformat()
            submissions_per_day[day] = submissions_per_day.get(day, 0) + 1
        return {
            "solved_per_day": solved_per_day,
            "submissions_per_day": submissions_per_day,
        }

    def submission_latency_stats(self, days: int = 7) -> dict:
        cutoff = datetime.utcnow() - timedelta(days=days)
        submissions = self._query(
            select(SubmissionLog).where(SubmissionLog.created_at >= cutoff)
        ).all()
        latencies = []
        for sub in submissions:
            # Placeholder: until ACK timestamps exist, use created_at
            latencies.append(0.0)
        return {
            "count": len(submissions),
            "latencies_seconds": latencies,
        }


__all__ = ["KPIService"]

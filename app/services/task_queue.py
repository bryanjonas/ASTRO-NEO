"""Lightweight retrying task queue for bridge commands."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from queue import SimpleQueue, Empty
from typing import Any, Callable

from app.services.notifications import NOTIFICATIONS

logger = logging.getLogger(__name__)


@dataclass
class Task:
    name: str
    func: Callable[[], Any]
    retries: int = 3
    backoff_seconds: float = 0.5


class TaskQueue:
    """Serial worker that retries failed tasks and logs alerts."""

    def __init__(self) -> None:
        self._queue: SimpleQueue[Task] = SimpleQueue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._thread.start()
            self._started = True

    def submit(self, task: Task) -> None:
        self._queue.put(task)
        if not self._started:
            self.start()

    def _worker(self) -> None:
        while True:
            try:
                task = self._queue.get(timeout=1.0)
            except Empty:
                continue
            attempts = 0
            while attempts < task.retries:
                attempts += 1
                try:
                    task.func()
                    if attempts > 1:
                        logger.info("Task %s succeeded after %s attempts", task.name, attempts)
                    break
                except Exception as exc:  # pragma: no cover - runtime safety
                    logger.warning("Task %s failed attempt %s/%s: %s", task.name, attempts, task.retries, exc)
                    if attempts >= task.retries:
                        NOTIFICATIONS.add(
                            "error",
                            f"Task {task.name} failed after {attempts} attempts",
                            {"error": str(exc)},
                        )
                        break
                    time.sleep(task.backoff_seconds * attempts)


TASK_QUEUE = TaskQueue()

__all__ = ["Task", "TaskQueue", "TASK_QUEUE"]

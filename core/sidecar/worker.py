"""Background worker for Kuza sidecar jobs."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from core.observability.logger import log_event, new_session_id
from core.sidecar.queue import SidecarJob, SidecarQueue


@dataclass
class JobResult:
    job_id: str
    name: str
    status: str
    result: Any = None
    error: str | None = None
    elapsed_seconds: float = 0.0


class SidecarWorker:
    """Runs SidecarQueue jobs on background threads."""

    def __init__(self, job_queue: SidecarQueue, workers: int = 1) -> None:
        self.job_queue = job_queue
        self.workers = max(1, workers)
        self.results: dict[str, JobResult] = {}
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start worker threads."""
        if self._threads:
            return

        self._stop_event.clear()

        for index in range(self.workers):
            thread = threading.Thread(
                target=self._run,
                name=f"kuza-sidecar-{index + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 5.0) -> None:
        """Request worker shutdown and wait briefly."""
        self._stop_event.set()

        for thread in self._threads:
            thread.join(timeout=timeout)

        self._threads.clear()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self.job_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self._execute(job)
            self.job_queue.task_done()

    def _execute(self, job: SidecarJob) -> None:
        session_id = new_session_id()
        started = time.monotonic()

        log_event(
            "sidecar_job_start",
            session_id=session_id,
            job_id=job.job_id,
            job_name=job.name,
            priority=job.priority,
        )

        try:
            value = job.function(*job.args, **job.kwargs)
            elapsed = time.monotonic() - started

            result = JobResult(
                job_id=job.job_id,
                name=job.name,
                status="completed",
                result=value,
                elapsed_seconds=round(elapsed, 3),
            )

            log_event(
                "sidecar_job_end",
                session_id=session_id,
                job_id=job.job_id,
                job_name=job.name,
                elapsed_seconds=result.elapsed_seconds,
                success=True,
            )

        except Exception as exc:
            elapsed = time.monotonic() - started

            result = JobResult(
                job_id=job.job_id,
                name=job.name,
                status="failed",
                error=str(exc),
                elapsed_seconds=round(elapsed, 3),
            )

            log_event(
                "sidecar_job_error",
                session_id=session_id,
                job_id=job.job_id,
                job_name=job.name,
                elapsed_seconds=result.elapsed_seconds,
                success=False,
                error_type=type(exc).__name__,
                error=str(exc),
            )

        with self._lock:
            self.results[job.job_id] = result

    def get_result(self, job_id: str) -> JobResult | None:
        """Return a completed job result, or None."""
        with self._lock:
            return self.results.get(job_id)

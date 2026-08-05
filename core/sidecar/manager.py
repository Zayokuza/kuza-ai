"""Persistent Sidecar manager with a shared evidence channel."""

from __future__ import annotations

import threading
import time

from core.sidecar.evidence import get_evidence_channel
from core.sidecar.queue import SidecarQueue
from core.sidecar.worker import SidecarWorker


class SidecarManager:
    """Singleton manager for background Python work."""

    def __init__(self, workers: int = 2):
        self.queue = SidecarQueue()
        self.worker = SidecarWorker(self.queue, workers=workers)
        self.worker.start()
        self.evidence = get_evidence_channel()

    def submit(
        self,
        name,
        function,
        *args,
        priority=100,
        context=None,
        **kwargs,
    ):
        return self.queue.submit(
            name,
            function,
            *args,
            priority=priority,
            context=context,
            **kwargs,
        )

    def result(self, job_id):
        return self.worker.get_result(job_id)

    def wait(self, job_id, timeout=1.0, poll_interval=0.02):
        """Wait briefly for a result while leaving long work asynchronous."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            result = self.result(job_id)
            if result is not None:
                return result
            time.sleep(max(0.005, poll_interval))
        return self.result(job_id)

    def pending(self):
        return self.queue.pending()

    def publish_main(self, kind, summary, *, task_id=None, details=None):
        return self.evidence.publish(
            "main",
            kind,
            summary,
            task_id=task_id,
            details=details,
        )

    def shared_context(self, *, since_sequence=0, limit=8, max_chars=2400):
        return self.evidence.format_context(
            since_sequence=since_sequence,
            limit=limit,
            max_chars=max_chars,
        )


_manager = None
_manager_lock = threading.Lock()


def get_sidecar():
    """Return the singleton sidecar manager."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = SidecarManager()
    return _manager


def reset_sidecar():
    """Stop and clear the singleton, primarily for tests."""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.worker.stop()
        _manager = None

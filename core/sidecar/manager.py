"""Persistent Sidecar manager."""

from __future__ import annotations

import threading

from core.sidecar.api import submit, result
from core.sidecar.queue import SidecarQueue
from core.sidecar.worker import SidecarWorker


class SidecarManager:
    """Singleton manager for the Kuza sidecar."""

    def __init__(self):
        self.queue = SidecarQueue()
        self.worker = SidecarWorker(self.queue, workers=2)
        self.worker.start()

    def submit(self, name, function, *args, priority=100, **kwargs):
        return self.queue.submit(
            name,
            function,
            *args,
            priority=priority,
            **kwargs,
        )

    def result(self, job_id):
        return self.worker.get_result(job_id)

    def pending(self):
        return self.queue.pending()


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

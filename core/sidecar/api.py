"""Simple API for the Kuza sidecar."""

from core.sidecar.queue import SidecarQueue
from core.sidecar.worker import SidecarWorker

_queue = SidecarQueue()
_worker = SidecarWorker(_queue, workers=2)
_worker.start()


def submit(name, function, *args, priority=100, **kwargs):
    """Submit a background job."""
    return _queue.submit(
        name,
        function,
        *args,
        priority=priority,
        **kwargs,
    )


def result(job_id):
    """Return a completed job result."""
    return _worker.get_result(job_id)

"""Thread-safe priority job queue for the Kuza sidecar."""

from __future__ import annotations

import itertools
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(order=True)
class SidecarJob:
    """One background job submitted to the sidecar."""

    priority: int
    sequence: int
    job_id: str = field(compare=False)
    name: str = field(compare=False)
    function: Callable[..., Any] = field(compare=False)
    args: tuple[Any, ...] = field(default_factory=tuple, compare=False)
    kwargs: dict[str, Any] = field(default_factory=dict, compare=False)
    created_at: float = field(default_factory=time.time, compare=False)
    context: dict[str, Any] = field(default_factory=dict, compare=False)


class SidecarQueue:
    """Priority queue used by sidecar worker threads."""

    def __init__(self) -> None:
        self._queue: queue.PriorityQueue[SidecarJob] = queue.PriorityQueue()
        self._sequence = itertools.count()
        self._lock = threading.Lock()

    def submit(
        self,
        name: str,
        function: Callable[..., Any],
        *args: Any,
        priority: int = 100,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """Submit a background job and return its unique ID."""
        with self._lock:
            sequence = next(self._sequence)

        job_id = uuid.uuid4().hex[:12]

        self._queue.put(
            SidecarJob(
                priority=priority,
                sequence=sequence,
                job_id=job_id,
                name=name,
                function=function,
                args=args,
                kwargs=kwargs,
                context=dict(context or {}),
            )
        )

        return job_id

    def get(self, timeout: float | None = None) -> SidecarJob:
        """Return the next available job."""
        return self._queue.get(timeout=timeout)

    def task_done(self) -> None:
        """Mark the current job as completed."""
        self._queue.task_done()

    def pending(self) -> int:
        """Return the number of jobs waiting."""
        return self._queue.qsize()

    def join(self) -> None:
        """Wait until all submitted jobs finish."""
        self._queue.join()

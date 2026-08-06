"""Public API for Kuza's single sidecar worker pool."""
from core.sidecar.manager import get_sidecar

def submit(name, function, *args, priority=100, **kwargs):
    return get_sidecar().submit(name, function, *args, priority=priority, **kwargs)

def result(job_id):
    return get_sidecar().result(job_id)

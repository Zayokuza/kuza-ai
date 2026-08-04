"""Built-in jobs executed by the Kuza sidecar."""

from __future__ import annotations

from core.implementation.analyzer import analyze_repository
from core.observability.logger import log_event, new_session_id


def repository_analysis(root="."):
    """Analyze the repository and return the index."""
    sid = new_session_id()

    log_event(
        "repository_analysis_start",
        session_id=sid,
        root=root,
    )

    result = analyze_repository(root)

    log_event(
        "repository_analysis_end",
        session_id=sid,
        files=len(result.files),
        symbols=sum(len(v) for v in result.symbols.values()),
        success=True,
    )

    return result

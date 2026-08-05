"""Deterministic validation for files changed by Kuza.

The model may suggest tests, but completion evidence is produced by real
subprocess results. Commands are argv lists and never use a shell.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class ValidationResult:
    command: tuple[str, ...]
    returncode: int
    output: str
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.returncode == 0

    def summary(self, max_chars: int = 1800) -> str:
        rendered = " ".join(self.command)
        status = "PASS" if self.passed else "FAIL"
        body = (self.output or "[no output]").strip()
        if len(body) > max_chars:
            body = body[-max_chars:]
        return f"[Validation {status}] {rendered}\n{body}"


def _normalize_paths(paths: Iterable[str | Path], root: Path) -> list[Path]:
    normalized: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            normalized.append(candidate)
    return normalized


def discover_validation_commands(
    changed_paths: Iterable[str | Path],
    root: str | Path | None = None,
    *,
    full_suite: bool = False,
) -> list[tuple[str, ...]]:
    """Return concrete validation commands for the changed files.

    Python syntax checking is always first. Matching tests are selected when
    they exist. A full suite is added only when explicitly requested.
    """
    workspace = Path(root or Path.cwd()).expanduser().resolve()
    changed = _normalize_paths(changed_paths, workspace)
    commands: list[tuple[str, ...]] = []

    python_files = [
        path for path in changed
        if path.suffix == ".py" and path.is_file()
    ]
    if python_files:
        relative = [str(path.relative_to(workspace)) for path in python_files]
        commands.append((sys.executable, "-m", "py_compile", *relative))

    # Validate data and scripts with local, deterministic tools only. No package
    # installation or network access is triggered by validation discovery.
    for path in changed:
        if path.suffix == ".json" and path.is_file():
            commands.append(
                (sys.executable, "-m", "json.tool", str(path.relative_to(workspace)))
            )

    shell_files = [
        str(path.relative_to(workspace))
        for path in changed
        if path.suffix in {".sh", ".bash"} and path.is_file()
    ]
    bash = shutil.which("bash")
    if shell_files and bash:
        commands.append((bash, "-n", *shell_files))

    node = shutil.which("node")
    for path in changed:
        if node and path.suffix in {".js", ".mjs", ".cjs"} and path.is_file():
            commands.append((node, "--check", str(path.relative_to(workspace))))

    if (
        shutil.which("go")
        and (workspace / "go.mod").is_file()
        and any(path.suffix == ".go" for path in changed)
    ):
        commands.append(("go", "test", "./..."))

    if (
        shutil.which("cargo")
        and (workspace / "Cargo.toml").is_file()
        and any(path.suffix == ".rs" for path in changed)
    ):
        commands.append(("cargo", "test", "--quiet"))

    explicit_tests = [
        path for path in python_files
        if path.name.startswith("test_") or path.name.endswith("_test.py")
    ]
    matching_tests: list[Path] = []
    for source in python_files:
        if source in explicit_tests:
            continue
        stem = source.stem
        candidates = [
            workspace / "tests" / f"test_{stem}.py",
            source.parent / f"test_{stem}.py",
            source.parent / f"{stem}_test.py",
        ]
        for candidate in candidates:
            if candidate.is_file() and candidate not in matching_tests:
                matching_tests.append(candidate)

    selected_tests = explicit_tests + [
        path for path in matching_tests if path not in explicit_tests
    ]
    if selected_tests:
        relative_tests = [str(path.relative_to(workspace)) for path in selected_tests]
        commands.append((sys.executable, "-m", "pytest", "-q", *relative_tests))

    has_pytest_project = (
        (workspace / "pytest.ini").is_file()
        or (workspace / "pyproject.toml").is_file()
        or (workspace / "setup.cfg").is_file()
        or (workspace / "tests").is_dir()
    )
    if full_suite and has_pytest_project:
        full = (sys.executable, "-m", "pytest", "-q")
        if full not in commands:
            commands.append(full)

    return commands


def run_validation(
    commands: Sequence[Sequence[str]],
    root: str | Path | None = None,
    *,
    timeout: int | None = None,
) -> list[ValidationResult]:
    """Execute validation commands and return real results."""
    workspace = Path(root or Path.cwd()).expanduser().resolve()
    effective_timeout = timeout or max(
        10,
        min(1800, int(os.environ.get("KUZA_VALIDATION_TIMEOUT", "180"))),
    )
    results: list[ValidationResult] = []

    for command in commands:
        argv = tuple(str(part) for part in command)
        try:
            proc = subprocess.run(
                argv,
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
                timeout=effective_timeout,
            )
            combined = "\n".join(
                part.strip() for part in (proc.stdout, proc.stderr) if part.strip()
            )
            results.append(
                ValidationResult(
                    command=argv,
                    returncode=proc.returncode,
                    output=combined,
                )
            )
        except subprocess.TimeoutExpired as exc:
            captured = "\n".join(
                str(part).strip()
                for part in (exc.stdout or "", exc.stderr or "")
                if str(part).strip()
            )
            results.append(
                ValidationResult(
                    command=argv,
                    returncode=124,
                    output=captured or f"Timed out after {effective_timeout}s",
                    timed_out=True,
                )
            )
        except OSError as exc:
            results.append(
                ValidationResult(
                    command=argv,
                    returncode=127,
                    output=str(exc),
                )
            )

        if results and not results[-1].passed:
            break

    return results


def validate_changed_paths(
    changed_paths: Iterable[str | Path],
    root: str | Path | None = None,
    *,
    full_suite: bool = False,
) -> list[ValidationResult]:
    """Discover and run validation for changed paths."""
    commands = discover_validation_commands(
        changed_paths,
        root=root,
        full_suite=full_suite,
    )
    return run_validation(commands, root=root)

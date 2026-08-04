"""Live Git repository context for Kuza planning and verification."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_PROJECT_DOCS = (
    "KUZA.md",
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "package.json",
)


def _clip(text: str, limit: int) -> str:
    """Limit large context values while preserving useful output."""
    text = str(text)
    if len(text) <= limit:
        return text
    removed = len(text) - limit
    return f"{text[:limit]}\n...[truncated {removed} characters]"


def _run_git(cwd: Path, *args: str, fallback: str = "") -> str:
    """Run a read-only Git command and return a safe fallback on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip() or fallback
    except (OSError, subprocess.SubprocessError):
        return fallback


@dataclass
class RepositoryContext:
    """Snapshot of the repository Kuza is currently operating inside."""

    cwd: str
    repo_root: str
    branch: str
    default_branch: str
    status: str
    recent_commits: list[str] = field(default_factory=list)
    project_docs: dict[str, str] = field(default_factory=dict)

    @classmethod
    def collect(cls, cwd: str | Path = ".") -> "RepositoryContext":
        """Collect current Git and project-document context."""
        working_dir = Path(cwd).expanduser().resolve()

        root_text = _run_git(
            working_dir,
            "rev-parse",
            "--show-toplevel",
            fallback=str(working_dir),
        )
        repo_root = Path(root_text).resolve()

        branch = _run_git(
            working_dir,
            "branch",
            "--show-current",
            fallback="-",
        )

        default_branch = _run_git(
            working_dir,
            "symbolic-ref",
            "--short",
            "refs/remotes/origin/HEAD",
            fallback="origin/main",
        ).removeprefix("origin/")

        status = _clip(
            _run_git(
                working_dir,
                "status",
                "--short",
                fallback="clean",
            )
            or "clean",
            2000,
        )

        commit_output = _run_git(
            working_dir,
            "log",
            "--oneline",
            "-5",
        )
        recent_commits = [
            line for line in commit_output.splitlines() if line.strip()
        ]

        docs: dict[str, str] = {}
        for base in (repo_root, working_dir):
            for name in DEFAULT_PROJECT_DOCS:
                path = base / name
                if not path.is_file():
                    continue

                try:
                    relative_name = str(path.relative_to(repo_root))
                except ValueError:
                    relative_name = str(path)

                if relative_name in docs:
                    continue

                try:
                    contents = path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                except OSError:
                    continue

                docs[relative_name] = _clip(contents, 1500)

        return cls(
            cwd=str(working_dir),
            repo_root=str(repo_root),
            branch=branch or "-",
            default_branch=default_branch or "main",
            status=status,
            recent_commits=recent_commits,
            project_docs=docs,
        )

    def to_prompt(self) -> str:
        """Format repository context for inclusion in a model prompt."""
        commits = "\n".join(
            f"- {commit}" for commit in self.recent_commits
        ) or "- none"

        docs = "\n".join(
            f"- {path}\n{contents}"
            for path, contents in self.project_docs.items()
        ) or "- none"

        return "\n".join(
            [
                "Repository context:",
                f"- cwd: {self.cwd}",
                f"- repo_root: {self.repo_root}",
                f"- branch: {self.branch}",
                f"- default_branch: {self.default_branch}",
                "- git_status:",
                self.status,
                "- recent_commits:",
                commits,
                "- project_documents:",
                docs,
            ]
        )

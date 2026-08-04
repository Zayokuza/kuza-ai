"""
Repository analyzer for Kuza.

Builds a lightweight Python code index containing:
- modules
- imports
- classes
- functions
- methods
- symbol locations
"""

from __future__ import annotations
from core.sidecar.manager import get_sidecar

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class SymbolInfo:
    """Location and type information for a discovered Python symbol."""

    name: str
    kind: str
    file: str
    line: int
    end_line: int | None = None


@dataclass
class FileAnalysis:
    """Analysis results for one Python source file."""

    path: str
    module: str
    imports: List[str] = field(default_factory=list)
    symbols: List[SymbolInfo] = field(default_factory=list)


@dataclass
class RepositoryAnalysis:
    """Combined analysis results for an entire repository."""

    root: str
    files: Dict[str, FileAnalysis] = field(default_factory=dict)
    symbols: Dict[str, List[SymbolInfo]] = field(default_factory=dict)

class SymbolCollector(ast.NodeVisitor):
    """Collect imports, classes, functions and methods."""

    def __init__(self, analysis: FileAnalysis):
        self.analysis = analysis

    def visit_Import(self, node):
        for alias in node.names:
            self.analysis.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        self.analysis.imports.append(module)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.analysis.symbols.append(
            SymbolInfo(
                name=node.name,
                kind="class",
                file=self.analysis.path,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", None),
            )
        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.analysis.symbols.append(
            SymbolInfo(
                name=node.name,
                kind="function",
                file=self.analysis.path,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", None),
            )
        )
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def _module_name(root: Path, file_path: Path) -> str:
    """Convert a Python file path into a dotted module name."""
    relative = file_path.relative_to(root).with_suffix("")
    parts = list(relative.parts)

    if parts and parts[-1] == "__init__":
        parts = parts[:-1]

    return ".".join(parts)


def analyze_repository(root: str | Path = ".") -> RepositoryAnalysis:
    """Scan Python files under root and build a repository index."""
    root_path = Path(root).resolve()
    repository = RepositoryAnalysis(root=str(root_path))

    ignored_dirs = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "venv",
        "node_modules",
    }

    for file_path in root_path.rglob("*.py"):
        if any(part in ignored_dirs for part in file_path.parts):
            continue

        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        relative_path = str(file_path.relative_to(root_path))

        file_analysis = FileAnalysis(
            path=relative_path,
            module=_module_name(root_path, file_path),
        )

        SymbolCollector(file_analysis).visit(tree)
        repository.files[relative_path] = file_analysis

        for symbol in file_analysis.symbols:
            repository.symbols.setdefault(symbol.name, []).append(symbol)

    return repository


def analyze_repository_async(root="."):
    """
    Submit repository analysis to the Sidecar.
    Returns a job ID immediately.
    """
    sidecar = get_sidecar()
    return sidecar.submit(
        "repository_analysis",
        analyze_repository,
        root,
        priority=10,
    )

"""
Deterministic project-root resolver.

Many CLI commands need to know "where is this repo?" without assuming that
the current working directory is the repo root. ProjectContext walks upward
from a starting path looking for well-known marker files.

Marker priority (first match wins):
  1. ``rigforge.yaml``        — explicit RIGForge project config
  2. ``pyproject.toml``       — Python project root
  3. ``.git/``                — git repository root

A ``ProjectContext`` always resolves to an absolute path. It also exposes the
canonical sub-directory layout used by the platform (``proofs/``,
``contracts/``, ``ledger/``, ``docs/``) so that callers never hard-code
relative paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_MARKERS = ("rigforge.yaml", "pyproject.toml", ".git")


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project context for a RIGForge invocation."""

    root: Path

    # ── Canonical layout ────────────────────────────────────────────────

    @property
    def proofs_dir(self) -> Path:
        return self.root / "proofs"

    @property
    def contracts_dir(self) -> Path:
        return self.root / "contracts"

    @property
    def ledger_dir(self) -> Path:
        return self.root / "ledger"

    @property
    def ledger_file(self) -> Path:
        return self.ledger_dir / "execution.jsonl"

    @property
    def docs_dir(self) -> Path:
        return self.root / "docs"

    @property
    def config_file(self) -> Path:
        return self.root / "rigforge.yaml"

    @property
    def workflows_dir(self) -> Path:
        return self.root / ".github" / "workflows"

    # ── Discovery ───────────────────────────────────────────────────────

    @classmethod
    def discover(cls, start: Path | str | None = None) -> "ProjectContext":
        """Walk upward from ``start`` looking for a project marker.

        If no marker is found, falls back to ``start`` itself (resolved).
        Never raises — the resolver is best-effort so that commands like
        ``rigforge init`` can run in an empty directory.
        """
        cwd = Path(start).resolve() if start is not None else Path.cwd().resolve()
        for candidate in (cwd, *cwd.parents):
            for marker in PROJECT_MARKERS:
                if (candidate / marker).exists():
                    return cls(root=candidate)
        return cls(root=cwd)

    def proof_file(self, phase: int) -> Path:
        """Canonical path to the proof packet for ``phase``."""
        return self.proofs_dir / f"phase{phase}_proof.json"

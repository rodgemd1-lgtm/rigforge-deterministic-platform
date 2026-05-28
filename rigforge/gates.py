"""
Quality gates and ``GateResult``.

A gate is a small, side-effect-free check that returns a structured result.
Gates are intentionally cheap (the heavyweight ones like ``pytest`` shell out
but return only summary information) so that ``rigforge run`` and
``rigforge doctor`` can run them inline.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict

from rigforge.context import ProjectContext


HARD_BLOCK = "hard_block"
SOFT_BLOCK = "soft_block"
ADVISORY = "advisory"


@dataclass
class GateResult:
    """Result of running a single quality gate."""

    name: str
    passed: bool
    severity: str = HARD_BLOCK
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Built-in gates ─────────────────────────────────────────────────────


def gate_python_version(min_major: int = 3, min_minor: int = 11) -> GateResult:
    ok = sys.version_info >= (min_major, min_minor)
    return GateResult(
        name="python_version",
        passed=ok,
        severity=HARD_BLOCK,
        detail=f"found {sys.version_info.major}.{sys.version_info.minor}, need >={min_major}.{min_minor}",
    )


def gate_repo_layout(ctx: ProjectContext) -> GateResult:
    required = [
        ctx.root / "pyproject.toml",
        ctx.root / "rigforge",
        ctx.root / "contracts",
    ]
    missing = [str(p.relative_to(ctx.root)) for p in required if not p.exists()]
    return GateResult(
        name="repo_layout",
        passed=not missing,
        severity=HARD_BLOCK,
        detail="missing: " + ", ".join(missing) if missing else "ok",
    )


def gate_ci_workflow(ctx: ProjectContext) -> GateResult:
    wf = ctx.workflows_dir / "ci.yml"
    ok = wf.exists()
    return GateResult(
        name="ci_workflow",
        passed=ok,
        severity=SOFT_BLOCK,
        detail=f"{wf.relative_to(ctx.root)} {'found' if ok else 'missing'}",
    )


def gate_contracts_present(ctx: ProjectContext) -> GateResult:
    yamls = list(ctx.contracts_dir.rglob("*.yaml")) if ctx.contracts_dir.exists() else []
    return GateResult(
        name="contracts_present",
        passed=bool(yamls),
        severity=SOFT_BLOCK,
        detail=f"{len(yamls)} contract YAML file(s) found",
    )


def gate_pytest(ctx: ProjectContext, *, quiet: bool = True) -> GateResult:
    """Run pytest from the project root. Returns soft-block on failure."""
    if shutil.which("pytest") is None:
        return GateResult(
            name="pytest",
            passed=False,
            severity=SOFT_BLOCK,
            detail="pytest not installed",
        )
    args = ["pytest", "-q"] if quiet else ["pytest"]
    proc = subprocess.run(args, cwd=str(ctx.root), capture_output=True, text=True)
    tail = "\n".join(proc.stdout.strip().splitlines()[-3:]) or proc.stderr.strip()[-300:]
    return GateResult(
        name="pytest",
        passed=proc.returncode == 0,
        severity=HARD_BLOCK,
        detail=tail or f"exit={proc.returncode}",
    )


def gate_ruff(ctx: ProjectContext) -> GateResult:
    if shutil.which("ruff") is None:
        return GateResult(
            name="ruff",
            passed=True,
            severity=ADVISORY,
            detail="ruff not installed (advisory)",
        )
    proc = subprocess.run(
        ["ruff", "check", "rigforge", "contracts"],
        cwd=str(ctx.root),
        capture_output=True,
        text=True,
    )
    return GateResult(
        name="ruff",
        passed=proc.returncode == 0,
        severity=ADVISORY,
        detail=(proc.stdout or proc.stderr).strip()[-300:] or "clean",
    )


def gate_contract_schema(ctx: ProjectContext) -> GateResult:
    """Validate every YAML in ``contracts/`` parses + matches DoneContract.

    YAMLs under ``contracts/v1/schemas/`` are the schema templates, not
    instances, so they are skipped.
    """
    import yaml as _yaml

    from contracts.v1 import DoneContract

    if not ctx.contracts_dir.exists():
        return GateResult(
            name="contract_schema",
            passed=True,
            severity=ADVISORY,
            detail="no contracts/ dir",
        )
    errors: list[str] = []
    checked = 0
    for path in ctx.contracts_dir.rglob("*.yaml"):
        if "schemas" in path.parts:
            continue
        try:
            data = _yaml.safe_load(path.read_text()) or {}
            DoneContract(**data)
            checked += 1
        except Exception as exc:  # noqa: BLE001 — surface any parse/validation error
            errors.append(f"{path.relative_to(ctx.root)}: {exc}")
    return GateResult(
        name="contract_schema",
        passed=not errors,
        severity=HARD_BLOCK,
        detail=f"{checked} ok"
        + ("; errors: " + " | ".join(errors[:3]) if errors else ""),
    )


# ── Per-phase bundles ──────────────────────────────────────────────────


def gates_for_phase(ctx: ProjectContext, phase: int) -> list[GateResult]:
    """Return the canonical gate set for a phase."""
    if phase == 1:
        return [gate_python_version(), gate_repo_layout(ctx)]
    if phase == 2:
        return [gate_python_version(), gate_repo_layout(ctx), gate_ci_workflow(ctx)]
    if phase == 3:
        return [gate_repo_layout(ctx), gate_contracts_present(ctx)]
    if phase == 4:
        return [gate_contracts_present(ctx), gate_contract_schema(ctx)]
    if phase == 5:
        return [gate_contract_schema(ctx), gate_pytest(ctx)]
    if phase == 6:
        return [gate_contracts_present(ctx), gate_pytest(ctx)]
    if phase == 7:
        return [gate_pytest(ctx), gate_ci_workflow(ctx)]
    return []


def all_blocking_failed(gates: list[GateResult]) -> list[GateResult]:
    return [g for g in gates if not g.passed and g.severity == HARD_BLOCK]

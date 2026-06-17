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

# Fallback wall-clock ceiling (seconds) for any gate that shells out, used when
# no project config supplies ``budgets.max_runtime_minutes``. A runaway test
# runner or linter must surface as a RED gate, never a hung seal.
DEFAULT_GATE_TIMEOUT_SECONDS = 300


def _gate_timeout_seconds(ctx: ProjectContext) -> int:
    """Resolve the subprocess timeout for a shelling-out gate.

    Sourced from ``budgets.max_runtime_minutes`` in ``rigforge.yaml`` when the
    config is loadable; otherwise the conservative default. Never raises — a
    broken config falls back to the default so the gate still runs.
    """
    try:
        from rigforge.config import load_config

        cfg = load_config(ctx)
        return max(1, int(cfg.budgets.max_runtime_minutes) * 60)
    except Exception:  # noqa: BLE001 — config errors must not disarm the timeout
        return DEFAULT_GATE_TIMEOUT_SECONDS


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
    """Run pytest from the project root.

    A missing test runner is a HARD_BLOCK: you cannot prove a phase is done
    when the suite that would catch failure never ran. A runaway suite is
    bounded by the configured runtime budget and surfaces as RED on timeout.
    """
    if shutil.which("pytest") is None:
        return GateResult(
            name="pytest",
            passed=False,
            severity=HARD_BLOCK,
            detail="pytest not installed (cannot verify a seal without a test runner)",
        )
    timeout = _gate_timeout_seconds(ctx)
    args = ["pytest", "-q"] if quiet else ["pytest"]
    try:
        proc = subprocess.run(
            args, cwd=str(ctx.root), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            name="pytest",
            passed=False,
            severity=HARD_BLOCK,
            detail=f"pytest timed out after {timeout}s",
        )
    tail = "\n".join(proc.stdout.strip().splitlines()[-3:]) or proc.stderr.strip()[-300:]
    return GateResult(
        name="pytest",
        passed=proc.returncode == 0,
        severity=HARD_BLOCK,
        detail=tail or f"exit={proc.returncode}",
    )


def gate_ruff(ctx: ProjectContext) -> GateResult:
    # ruff is a linter, not a verifier: its severity is ADVISORY, so an absent
    # ruff never waves a seal through — it cannot block one in the first place.
    if shutil.which("ruff") is None:
        return GateResult(
            name="ruff",
            passed=True,
            severity=ADVISORY,
            detail="ruff not installed (advisory)",
        )
    timeout = _gate_timeout_seconds(ctx)
    try:
        proc = subprocess.run(
            ["ruff", "check", "rigforge", "contracts"],
            cwd=str(ctx.root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            name="ruff",
            passed=False,
            severity=ADVISORY,
            detail=f"ruff timed out after {timeout}s",
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


def gate_config_valid(ctx: ProjectContext) -> GateResult:
    """Parse and validate ``rigforge.yaml`` via the typed loader."""
    from rigforge.config import load_config

    if not ctx.config_file.exists():
        return GateResult(
            name="config_valid",
            passed=True,
            severity=ADVISORY,
            detail="no rigforge.yaml (defaults assumed)",
        )
    try:
        cfg = load_config(ctx)
    except ValueError as exc:
        return GateResult(
            name="config_valid",
            passed=False,
            severity=HARD_BLOCK,
            detail=str(exc),
        )
    return GateResult(
        name="config_valid",
        passed=True,
        severity=HARD_BLOCK,
        detail=f"schema={cfg.schema_version} project={cfg.project}",
    )


# ── Per-phase bundles ──────────────────────────────────────────────────


def gates_for_phase(ctx: ProjectContext, phase: int) -> list[GateResult]:
    """Return the canonical gate set for a phase (executed sequentially)."""
    return [t() for t in gate_thunks_for_phase(ctx, phase)]


def gate_thunks_for_phase(ctx: ProjectContext, phase: int):
    """Return zero-arg callables for each gate in ``phase``.

    Returning thunks (rather than already-executed results) lets the harness
    schedule gates concurrently (G002) without changing the calling contract.
    """
    if phase == 1:
        return [gate_python_version, lambda: gate_repo_layout(ctx)]
    if phase == 2:
        return [
            gate_python_version,
            lambda: gate_repo_layout(ctx),
            lambda: gate_ci_workflow(ctx),
        ]
    if phase == 3:
        return [lambda: gate_repo_layout(ctx), lambda: gate_contracts_present(ctx)]
    if phase == 4:
        return [lambda: gate_contracts_present(ctx), lambda: gate_contract_schema(ctx)]
    if phase == 5:
        return [lambda: gate_contract_schema(ctx), lambda: gate_pytest(ctx)]
    if phase == 6:
        return [lambda: gate_contracts_present(ctx), lambda: gate_pytest(ctx)]
    if phase == 7:
        return [lambda: gate_pytest(ctx), lambda: gate_ci_workflow(ctx)]
    return []


def all_blocking_failed(gates: list[GateResult]) -> list[GateResult]:
    return [g for g in gates if not g.passed and g.severity == HARD_BLOCK]

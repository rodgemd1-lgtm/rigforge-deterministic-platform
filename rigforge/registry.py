"""
Capability registry — the extensibility seam (G009).

The built-in gates in ``rigforge.gates`` are hard-wired into per-phase
bundles. That is fine for the platform's own gates, but a *buyer* must be
able to add a capability — a named, phase-scoped gate — **without editing
core**. This module is that seam.

A *capability* is a named check that:

* targets one or more phases (or all phases via ``"*"``),
* returns a :class:`~rigforge.gates.GateResult` when run against a
  :class:`~rigforge.context.ProjectContext`,
* carries a default severity (hard_block / soft_block / advisory).

Capabilities are registered into a :class:`CapabilityRegistry`. The default
process-wide registry is :data:`REGISTRY`; the ``@capability`` decorator
registers into it. The harness consults the active registry so that any
registered capability runs *inside* the deterministic phase pipeline,
alongside the built-in gates, with execution order preserved for
reproducible proofs.

Determinism note: capabilities run in **registration order** within a phase,
after the built-in gates. The registry never imports a plugin on its own —
discovery is explicit (the operator imports their module, or points
``RIGFORGE_CAPABILITY_MODULES`` at it) so there is no hidden, environment-
dependent gate set.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from typing import Callable, Iterable

from rigforge.context import ProjectContext
from rigforge.gates import ADVISORY, HARD_BLOCK, SOFT_BLOCK, GateResult


# A capability is a callable taking the project context and returning a result.
CapabilityFn = Callable[[ProjectContext], GateResult]

_ALL_PHASES = "*"
_VALID_SEVERITIES = {HARD_BLOCK, SOFT_BLOCK, ADVISORY}


@dataclass(frozen=True)
class Capability:
    """A registered, phase-scoped capability (plugin gate)."""

    name: str
    fn: CapabilityFn
    phases: tuple[int, ...] | str = _ALL_PHASES
    severity: str = HARD_BLOCK
    description: str = ""

    def applies_to(self, phase: int) -> bool:
        if self.phases == _ALL_PHASES:
            return True
        return phase in self.phases  # type: ignore[operator]

    def run(self, ctx: ProjectContext) -> GateResult:
        """Run the capability, normalising the result.

        The capability's declared ``severity`` wins so a buyer can register a
        check as advisory or hard-blocking without re-deriving it inside the
        function body. Any exception is caught and surfaced as a failing gate
        (a capability that crashes must never silently pass a seal).
        """
        try:
            result = self.fn(ctx)
        except Exception as exc:  # noqa: BLE001 — a crash is a failed gate, not a skip
            return GateResult(
                name=self.name,
                passed=False,
                severity=self.severity,
                detail=f"capability raised: {exc}",
            )
        if not isinstance(result, GateResult):
            return GateResult(
                name=self.name,
                passed=False,
                severity=self.severity,
                detail=f"capability returned {type(result).__name__}, expected GateResult",
            )
        # Pin the name + declared severity so registration metadata is authoritative.
        return GateResult(
            name=self.name,
            passed=result.passed,
            severity=self.severity,
            detail=result.detail,
        )


class CapabilityRegistry:
    """An ordered, name-keyed collection of capabilities."""

    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    # ── Registration ───────────────────────────────────────────────────

    def register(
        self,
        name: str,
        fn: CapabilityFn,
        *,
        phases: Iterable[int] | str = _ALL_PHASES,
        severity: str = HARD_BLOCK,
        description: str = "",
        replace: bool = False,
    ) -> Capability:
        """Register ``fn`` under ``name``. Returns the stored Capability.

        Raises ``ValueError`` on a duplicate name (unless ``replace=True``),
        an unknown severity, or an out-of-range phase — registration is the
        right place to fail loudly, before any seal depends on the plugin.
        """
        if not name or not name.strip():
            raise ValueError("capability name must be non-empty")
        if name in self._caps and not replace:
            raise ValueError(f"capability '{name}' already registered (pass replace=True to override)")
        if severity not in _VALID_SEVERITIES:
            raise ValueError(f"unknown severity '{severity}'; expected one of {sorted(_VALID_SEVERITIES)}")
        if phases == _ALL_PHASES:
            norm_phases: tuple[int, ...] | str = _ALL_PHASES
        else:
            phase_tuple = tuple(int(p) for p in phases)  # type: ignore[union-attr]
            for p in phase_tuple:
                if not 1 <= p <= 7:
                    raise ValueError(f"phase {p} out of range (1-7)")
            norm_phases = phase_tuple
        cap = Capability(
            name=name, fn=fn, phases=norm_phases, severity=severity, description=description
        )
        self._caps[name] = cap
        return cap

    def unregister(self, name: str) -> None:
        self._caps.pop(name, None)

    def clear(self) -> None:
        self._caps.clear()

    # ── Query ──────────────────────────────────────────────────────────

    def __contains__(self, name: object) -> bool:
        return name in self._caps

    def __len__(self) -> int:
        return len(self._caps)

    def names(self) -> list[str]:
        return list(self._caps.keys())

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def all(self) -> list[Capability]:
        """All capabilities in registration order."""
        return list(self._caps.values())

    def for_phase(self, phase: int) -> list[Capability]:
        """Capabilities applicable to ``phase``, in registration order."""
        return [c for c in self._caps.values() if c.applies_to(phase)]

    def thunks_for_phase(self, ctx: ProjectContext, phase: int) -> list[Callable[[], GateResult]]:
        """Zero-arg callables for each applicable capability.

        Mirrors :func:`rigforge.gates.gate_thunks_for_phase` so the harness can
        schedule capability gates with the same parallel/serial machinery.
        """
        return [(lambda c=c: c.run(ctx)) for c in self.for_phase(phase)]

    def as_dicts(self) -> list[dict]:
        return [
            {
                "name": c.name,
                "phases": "all" if c.phases == _ALL_PHASES else list(c.phases),  # type: ignore[arg-type]
                "severity": c.severity,
                "description": c.description,
            }
            for c in self._caps.values()
        ]


# ── Default process-wide registry + decorator ──────────────────────────

REGISTRY = CapabilityRegistry()


def capability(
    name: str,
    *,
    phases: Iterable[int] | str = _ALL_PHASES,
    severity: str = HARD_BLOCK,
    description: str = "",
    registry: CapabilityRegistry | None = None,
):
    """Decorator registering a capability into a registry (default: ``REGISTRY``).

    Usage::

        from rigforge.registry import capability
        from rigforge.gates import GateResult, ADVISORY

        @capability("license_header", phases=[1], severity=ADVISORY)
        def check_license(ctx):
            ok = (ctx.root / "LICENSE").exists()
            return GateResult(name="license_header", passed=ok)
    """

    def _decorator(fn: CapabilityFn) -> CapabilityFn:
        # NB: ``registry or REGISTRY`` would be wrong — an empty CapabilityRegistry
        # is falsy via ``__len__``, so an explicit None check is required.
        target = REGISTRY if registry is None else registry
        target.register(
            name, fn, phases=phases, severity=severity, description=description, replace=True
        )
        return fn

    return _decorator


def discover_from_env(registry: CapabilityRegistry | None = None) -> list[str]:
    """Import modules named in ``RIGFORGE_CAPABILITY_MODULES`` (comma-separated).

    Importing the module runs its ``@capability`` decorators, which register
    into ``REGISTRY``. Returns the list of modules successfully imported.
    Discovery is explicit and opt-in — nothing is imported without the
    operator naming it, so the gate set stays deterministic.
    """
    spec = os.environ.get("RIGFORGE_CAPABILITY_MODULES", "").strip()
    if not spec:
        return []
    imported: list[str] = []
    for mod_name in (m.strip() for m in spec.split(",")):
        if not mod_name:
            continue
        importlib.import_module(mod_name)
        imported.append(mod_name)
    return imported

"""
Goal harness — converge-until-proven phase loop (G010).

``ArchonHarness.run`` executes a *single* phase. A goal harness drives the
phases of a :class:`~contracts.v1.DoneContract` until its done-condition is
**proven** (or a budget/iteration cap fires). This is the "run phases until a
stated contract is satisfied" loop that turns the platform from a per-phase
runner into a goal-seeking engine.

The done-condition is the contract's own acceptance, expressed concretely as:

* every targeted phase runs without blocking gate failures, **and**
* a caller-supplied ``done_check(ctx) -> bool`` returns True (the stated
  done-condition / DoneContract proof — e.g. "the artifact exists and is
  signed").

Convergence + bounds (no infinite loops, ever):

* ``max_iterations`` — hard cap on full passes (default from the contract).
* the :class:`~rigforge.harness.BudgetTracker` — each iteration charges a
  configurable cost/token estimate; a :class:`BudgetExceeded` stops the loop
  with ``stop_reason="budget"`` rather than crashing.
* a phase that keeps failing its blocking gates stops the loop with
  ``stop_reason="phase_blocked"`` — the loop does not spin on an
  unsatisfiable gate.

On success the goal harness seals the final phase with a ProofPacket and
records the outcome in the ledger, reusing the existing seal machinery so the
DoneContract and the proof stay bound to the same evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rigforge.context import ProjectContext
from rigforge.harness import ArchonHarness, BudgetExceeded, HarnessResult
from rigforge.proof import ProofPacket


# A done-check inspects the project and returns True when the goal is proven.
DoneCheck = Callable[[ProjectContext], bool]


@dataclass
class GoalIteration:
    """One pass of the goal loop over the targeted phases."""

    index: int
    phase_results: list[HarnessResult] = field(default_factory=list)
    done: bool = False
    blocked_phase: int | None = None
    cost_usd: float = 0.0
    tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "done": self.done,
            "blocked_phase": self.blocked_phase,
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "phases": [r.phase for r in self.phase_results],
            "all_phases_ok": all(r.ok for r in self.phase_results) if self.phase_results else False,
        }


@dataclass
class GoalResult:
    """Outcome of a converge-until-proven goal run."""

    proven: bool
    stop_reason: str  # "proven" | "max_iterations" | "budget" | "phase_blocked"
    iterations: list[GoalIteration] = field(default_factory=list)
    proof: ProofPacket | None = None
    cost_usd: float = 0.0
    tokens: int = 0

    @property
    def iteration_count(self) -> int:
        return len(self.iterations)

    def to_dict(self) -> dict:
        return {
            "proven": self.proven,
            "stop_reason": self.stop_reason,
            "iteration_count": self.iteration_count,
            "iterations": [it.to_dict() for it in self.iterations],
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "proof_phase": self.proof.phase if self.proof else None,
            "proof_sha256": self.proof.packet_sha256 if self.proof else None,
        }


class GoalHarness:
    """Drive phases to a proven done-condition, bounded by budgets (G010)."""

    def __init__(
        self,
        ctx: ProjectContext,
        *,
        harness: ArchonHarness | None = None,
    ):
        self.ctx = ctx
        self.harness = harness or ArchonHarness(ctx)

    def converge(
        self,
        *,
        phases: list[int],
        done_check: DoneCheck,
        verifier: str,
        max_iterations: int = 10,
        cost_per_iteration: float = 0.0,
        tokens_per_iteration: int = 0,
        seal_on_success: bool = True,
        evidence: str | None = None,
        artifacts: list[Path] | None = None,
    ) -> GoalResult:
        """Run ``phases`` repeatedly until ``done_check`` proves the goal.

        Returns a :class:`GoalResult`. The loop stops on the FIRST of:

        * ``done_check`` returns True after a clean pass → ``stop_reason="proven"``
        * a phase reports blocking gate failures → ``stop_reason="phase_blocked"``
        * the cost/token budget is exceeded → ``stop_reason="budget"``
        * ``max_iterations`` passes complete unproven → ``stop_reason="max_iterations"``
        """
        if not phases:
            raise ValueError("goal converge requires at least one phase")
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")

        iterations: list[GoalIteration] = []
        h = self.harness

        self.harness.ledger.append(
            kind="goal.start",
            actor=verifier,
            phases=phases,
            max_iterations=max_iterations,
        )

        stop_reason = "max_iterations"
        proven = False
        proof: ProofPacket | None = None

        for i in range(1, max_iterations + 1):
            it = GoalIteration(index=i)

            # Charge this iteration against the budget BEFORE doing the work, so
            # an over-budget run stops cleanly rather than completing work it
            # cannot pay for.
            if cost_per_iteration or tokens_per_iteration:
                try:
                    h.charge(
                        cost_usd=cost_per_iteration,
                        tokens=tokens_per_iteration,
                        actor=verifier,
                        note=f"goal iteration {i}",
                    )
                except BudgetExceeded:
                    stop_reason = "budget"
                    iterations.append(it)
                    break

            it.cost_usd = h.budget.cost_usd
            it.tokens = h.budget.tokens

            blocked = False
            for phase in phases:
                result = h.run(phase, verifier=verifier)
                it.phase_results.append(result)
                if not result.ok:
                    it.blocked_phase = phase
                    blocked = True
                    break

            if blocked:
                iterations.append(it)
                stop_reason = "phase_blocked"
                break

            # All targeted phases ran clean this pass — is the goal proven?
            if done_check(self.ctx):
                it.done = True
                proven = True
                stop_reason = "proven"
                iterations.append(it)
                break

            iterations.append(it)
            # else: loop again (a real generator would have mutated the tree).

        final_phase = phases[-1]
        if proven and seal_on_success:
            last_iter = iterations[-1]
            last_result = next(
                (r for r in reversed(last_iter.phase_results) if r.phase == final_phase),
                last_iter.phase_results[-1] if last_iter.phase_results else None,
            )
            proof = h.seal(
                final_phase,
                verifier=verifier,
                evidence=evidence or "goal converged: done-condition proven",
                artifacts=artifacts or [],
                gates=last_result.gates if last_result else [],
                envelope=last_result.envelope if last_result else None,
            )

        self.harness.ledger.append(
            kind="goal.finish",
            actor=verifier,
            proven=proven,
            stop_reason=stop_reason,
            iterations=len(iterations),
            cost_usd=h.budget.cost_usd,
            tokens=h.budget.tokens,
        )

        return GoalResult(
            proven=proven,
            stop_reason=stop_reason,
            iterations=iterations,
            proof=proof,
            cost_usd=h.budget.cost_usd,
            tokens=h.budget.tokens,
        )

"""
Evaluator-Optimizer Loop (research priority #2).

This module automates "prove it." Instead of running each gate once and
reporting pass/fail, the ``EvaluatorLoop`` wraps gate execution in a
retry-then-escalate loop:

    run gate → score against a rubric → if fail and retries remain, retry
             → else escalate (HARD_BLOCK / Gate-D)

The rubric is **deterministic and rules-based** by default: a gate scores
``PASS`` when (a) the gate returned ``passed=True`` and (b) the proof it
would produce is structurally valid (a non-empty ``GateResult`` with a
recognizable name). An optional LLM-rubric is left as a future seam
(``Rubric.score`` is the single method to override); the loop itself never
calls an LLM, so it stays deterministic and offline.

The FULL loop transcript — every attempt's score plus the final outcome —
is sealed into the ProofPacket via ``EvalLoopTranscript``, so a sealed
phase records not just "the gate passed" but "it took 2 attempts, here is
what each attempt scored, and here is why we stopped."

Design constraints (RIG build doctrine):

* **No infinite spin.** The loop is bounded by ``max_retries`` (default 3).
  When retries are exhausted it escalates and returns — it never loops
  again on the same gate.
* **Deterministic before agentic.** The default rubric is pure-Python and
  side-effect-free. No model call is made.
* **Proof, not assertion.** The transcript is part of the ProofPacket; a
  claim that "the loop converged" is backed by the recorded attempt scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from rigforge.gates import (
    ADVISORY,
    HARD_BLOCK,
    SOFT_BLOCK,
    GateResult,
)


# A gate thunk is the same zero-arg callable shape the harness already uses
# (see ``rigforge.gates.gate_thunks_for_phase``). Keeping the contract
# identical means the loop can wrap any existing gate without adaptation.
GateThunk = Callable[[], GateResult]


# ── Rubric ─────────────────────────────────────────────────────────────


# A score is a small, serializable verdict. ``passed`` is the binary
# rubric decision (did this attempt satisfy the gate + proof validity).
# ``score`` is a 0.0–1.0 confidence for future LLM-rubric use; the
# deterministic rubric emits only 0.0 or 1.0.
ScoreVerdict = Literal["pass", "fail"]


@dataclass
class AttemptScore:
    """One attempt of one gate inside the loop."""

    attempt: int  # 1-indexed: attempt 1 is the first run.
    gate_name: str
    passed: bool  # rubric verdict for THIS attempt.
    score: float  # 0.0–1.0; deterministic rubric → 0.0 or 1.0.
    verdict: ScoreVerdict
    detail: str  # the gate's own detail string (proof evidence).
    note: str = ""  # rubric note, e.g. "proof_valid=True".

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt,
            "gate_name": self.gate_name,
            "passed": self.passed,
            "score": self.score,
            "verdict": self.verdict,
            "detail": self.detail,
            "note": self.note,
        }


class Rubric:
    """Score a single ``GateResult`` against a deterministic rubric.

    Override ``score`` to plug in an LLM-rubric. The default implementation
    is rules-based and side-effect-free: PASS iff the gate passed AND the
    result carries usable proof (a non-empty name and a non-empty detail or
    an explicit pass). This is the "did the gate pass + is the proof valid"
    rubric the spec calls for.
    """

    def score(self, result: GateResult) -> tuple[bool, float, str]:
        """Return ``(passed, score_0_to_1, note)``.

        * ``passed`` — True if this attempt satisfies the rubric.
        * ``score``  — 0.0 or 1.0 for the deterministic rubric.
        * ``note``   — short rubric justification sealed into the transcript.
        """
        gate_passed = bool(result.passed)
        proof_valid = self._proof_is_valid(result)
        passed = gate_passed and proof_valid
        note = f"gate_passed={gate_passed}, proof_valid={proof_valid}"
        return passed, (1.0 if passed else 0.0), note

    @staticmethod
    def _proof_is_valid(result: GateResult) -> bool:
        """Structural proof-validity check (deterministic).

        A gate result is "proof-valid" when it carries an identifiable name
        and either passed (positive proof) or produced a detail line
        (negative proof — a failure reason is still valid evidence). A bare
        ``GateResult(name="", passed=False, detail="")`` is treated as
        invalid proof: it asserts failure without any evidence, which the
        rubric refuses to wave through.
        """
        has_name = bool(result.name and result.name.strip())
        if not has_name:
            return False
        # A pass is always valid proof. A fail must carry a reason.
        if result.passed:
            return True
        return bool(result.detail and result.detail.strip())


class DeterministicRubric(Rubric):
    """Explicit alias for the default rules-based rubric.

    Exists so call-sites and tests can name the rubric they mean; the base
    ``Rubric`` is already deterministic. Future ``LLMRubric`` subclasses
    ``Rubric`` and overrides ``score``.
    """


# ── Loop outcome + transcript ──────────────────────────────────────────


# How the loop terminated for a gate.
Outcome = Literal["converged", "escalated", "passed_first_try"]

# What escalation produced. ``hard_block`` is the default; ``gate_d`` is
# reserved for gates the loop is configured to treat as destructive
# (irreversible) — those escalate to a human Gate-D approval rather than
# an automatic block.
Escalation = Literal["hard_block", "gate_d", "none"]


@dataclass
class GateLoopRecord:
    """The loop's complete record for a single gate."""

    gate_name: str
    severity: str
    attempts: list[AttemptScore] = field(default_factory=list)
    outcome: Outcome = "escalated"
    escalation: Escalation = "none"
    final_passed: bool = False
    attempts_used: int = 0

    def to_dict(self) -> dict:
        return {
            "gate_name": self.gate_name,
            "severity": self.severity,
            "attempts": [a.to_dict() for a in self.attempts],
            "outcome": self.outcome,
            "escalation": self.escalation,
            "final_passed": self.final_passed,
            "attempts_used": self.attempts_used,
        }


@dataclass
class EvalLoopTranscript:
    """The FULL loop transcript, sealed into the ProofPacket.

    Records every gate's attempt history and the overall outcome so a
    sealed phase proves *how* each gate was satisfied (converged after N
    retries, passed first try, or escalated at the cap) — not just that it
    was.
    """

    enabled: bool
    max_retries: int
    records: list[GateLoopRecord] = field(default_factory=list)
    escalated: bool = False

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "max_retries": self.max_retries,
            "records": [r.to_dict() for r in self.records],
            "escalated": self.escalated,
        }

    @property
    def converged(self) -> bool:
        """True when every gate eventually passed (no escalation)."""
        return self.enabled and not self.escalated and all(
            r.final_passed for r in self.records
        )

    def record_for(self, gate_name: str) -> GateLoopRecord | None:
        for r in self.records:
            if r.gate_name == gate_name:
                return r
        return None


# ── The loop ───────────────────────────────────────────────────────────


# Default retry cap. Three retries is enough to ride out a flaky gate
# (e.g. a transient file-system race) without letting a genuinely broken
# gate spin forever. Configurable via ``max_retries``.
DEFAULT_MAX_RETRIES = 3

# Gates whose failure is treated as destructive → escalate to Gate-D
# (human approval) instead of an automatic HARD_BLOCK. By default this is
# empty: the loop escalates everything as HARD_BLOCK. Callers add gate
# names here when a gate guards an irreversible action.
DEFAULT_GATE_D_GATES: tuple[str, ...] = ()


class EvaluatorLoop:
    """Wrap gate execution in a score → retry → escalate loop.

    Usage (matches the harness's existing thunk contract)::

        loop = EvaluatorLoop(max_retries=3)
        transcript = loop.run_all(gate_thunks_for_phase(ctx, phase))
        if transcript.escalated:
            ...  # a gate hit the retry cap; blockers carry it

    The loop is deterministic, synchronous, and in-process — same
    constraints as the harness. It does NOT call an LLM; the rubric is
    rules-based. The seam for an LLM rubric is ``Rubric.score``.
    """

    def __init__(
        self,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        rubric: Rubric | None = None,
        gate_d_gates: tuple[str, ...] = DEFAULT_GATE_D_GATES,
    ):
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self.max_retries = max_retries
        self.rubric = rubric or DeterministicRubric()
        self.gate_d_gates = tuple(gate_d_gates)

    # ── Single gate ────────────────────────────────────────────────────

    def run_gate(self, thunk: GateThunk) -> GateLoopRecord:
        """Run one gate through the score → retry → escalate loop.

        Returns the full attempt history for this gate plus the outcome.
        The loop runs the gate once (attempt 1); if the rubric fails and
        retries remain, it retries. When retries are exhausted it escalates
        and stops — it never spins past the cap.
        """
        # Run the gate once. This first run doubles as the probe (we learn
        # the gate's name + severity from it) AND as attempt 1 — we reuse
        # the result rather than re-invoking, so an always-passing gate is
        # invoked exactly once and an always-failing gate exactly
        # ``max_retries`` times. Gate thunks are cheap and side-effect-free
        # by contract.
        probe = thunk()
        record = GateLoopRecord(gate_name=probe.name, severity=probe.severity)

        # attempt 1 consumes the probe; subsequent attempts re-invoke.
        result = probe
        for attempt in range(1, self.max_retries + 1):
            passed, score, note = self.rubric.score(result)
            record.attempts.append(
                AttemptScore(
                    attempt=attempt,
                    gate_name=result.name,
                    passed=passed,
                    score=score,
                    verdict="pass" if passed else "fail",
                    detail=result.detail,
                    note=note,
                )
            )
            record.attempts_used = attempt

            if passed:
                record.final_passed = True
                record.outcome = "passed_first_try" if attempt == 1 else "converged"
                record.escalation = "none"
                return record

            # Failed this attempt. If retries remain, re-run; else escalate.
            if attempt < self.max_retries:
                result = thunk()
                continue

            # Retries exhausted → escalate. Do NOT loop again.
            record.final_passed = False
            record.outcome = "escalated"
            record.escalation = self._escalation_for(record.gate_name)
            return record

        # Unreachable: the loop always returns inside the for-body. Kept for
        # type-checkers that can't see the exhaustive return.
        return record  # pragma: no cover

    def _escalation_for(self, gate_name: str) -> Escalation:
        """Pick the escalation target for a gate that hit the retry cap."""
        if gate_name in self.gate_d_gates:
            return "gate_d"
        return "hard_block"

    # ── All gates ──────────────────────────────────────────────────────

    def run_all(self, thunks: list[GateThunk]) -> EvalLoopTranscript:
        """Run every gate thunk through the loop, returning the transcript.

        Gates are executed in order (same determinism contract as the
        harness). The transcript's ``escalated`` flag is True if ANY gate
        escalated at the retry cap.
        """
        transcript = EvalLoopTranscript(
            enabled=True, max_retries=self.max_retries
        )
        for thunk in thunks:
            record = self.run_gate(thunk)
            transcript.records.append(record)
            if record.outcome == "escalated":
                transcript.escalated = True
        return transcript

    # ── Bridging to the harness ────────────────────────────────────────

    @staticmethod
    def final_results(transcript: EvalLoopTranscript) -> list[GateResult]:
        """Reconstruct the per-gate ``GateResult`` view of a transcript.

        The harness reasons in ``GateResult`` objects (blockers, sealing).
        This helper projects the loop's final per-gate verdict back into
        that shape: the last attempt's gate becomes the result, with
        ``passed`` set by the rubric verdict. Escalated gates become
        failing ``GateResult``s at their recorded severity.
        """
        out: list[GateResult] = []
        for rec in transcript.records:
            last = rec.attempts[-1]
            out.append(
                GateResult(
                    name=rec.gate_name,
                    passed=rec.final_passed,
                    severity=rec.severity,
                    detail=last.detail,
                )
            )
        return out


def disabled_transcript() -> EvalLoopTranscript:
    """A transcript that records "the loop was not active".

    Returned by ``harness.run`` when the eval-loop is opt-out, so downstream
    seal/serialize code always sees a transcript object (no None-checks).
    """
    return EvalLoopTranscript(enabled=False, max_retries=0)

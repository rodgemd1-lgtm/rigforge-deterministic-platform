"""
ArchonHarness — minimal but real phase orchestrator.

The harness drives the determinism contract: plan → run gates → either seal
or report blockers. It is intentionally synchronous and in-process; the
``GapFinding`` registry (G002) tracks the upgrade to multi-agent scheduling.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from rigforge.context import ProjectContext
from rigforge.evalloop import (
    EvaluatorLoop,
    EvalLoopTranscript,
    disabled_transcript,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only import to avoid a cycle
    from rigforge.registry import CapabilityRegistry
from rigforge.config import RigForgeConfig, load_config
from rigforge.gates import (
    GateResult,
    all_blocking_failed,
    gates_for_phase,
)
from rigforge.ledger import ExecutionLedger
from rigforge.spec import SpecBinding
from rigforge.proof import ArtifactRecord, GateOutcome, ProofPacket
from rigforge.run_envelope import RunEnvelope


PHASES: dict[int, str] = {
    1: "Bootstrap & Doctrine",
    2: "Environment Validation",
    3: "Runtime Kernel",
    4: "Control Plane Registries",
    5: "GEV Loop + DoneContract",
    6: "Archon + DeerFlow Harness",
    7: "Cockpit + Retrofit Protocol",
}


@dataclass
class PlanStep:
    name: str
    description: str


@dataclass
class HarnessResult:
    phase: int
    envelope: RunEnvelope
    gates: list[GateResult] = field(default_factory=list)
    blockers: list[GateResult] = field(default_factory=list)
    ok: bool = True
    cost_usd: float = 0.0
    tokens: int = 0
    budget_exceeded: bool = False
    # Evaluator-Optimizer loop transcript (research #2). Always present —
    # ``disabled_transcript()`` when the loop is opt-out — so seal/serialize
    # code never needs a None-check. Carries the full per-gate attempt
    # history that gets sealed into the ProofPacket.
    eval_loop: EvalLoopTranscript = field(default_factory=disabled_transcript)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "ok": self.ok,
            "envelope": self.envelope.to_dict(),
            "gates": [g.to_dict() for g in self.gates],
            "blockers": [b.to_dict() for b in self.blockers],
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "budget_exceeded": self.budget_exceeded,
            "eval_loop": self.eval_loop.to_dict(),
        }


class BudgetExceeded(RuntimeError):
    """Raised when a run's cost/token charge would exceed configured ceilings (G005)."""


@dataclass
class BudgetTracker:
    """In-memory tracker honouring ``RigForgeConfig.budgets`` (G005)."""

    max_cost_usd: float
    max_tokens: int
    cost_usd: float = 0.0
    tokens: int = 0

    def charge(self, *, cost_usd: float = 0.0, tokens: int = 0) -> None:
        if cost_usd < 0 or tokens < 0:
            raise ValueError("charges must be non-negative")
        new_cost = self.cost_usd + cost_usd
        new_tokens = self.tokens + tokens
        if new_cost > self.max_cost_usd:
            raise BudgetExceeded(
                f"cost budget exceeded: {new_cost:.4f} > {self.max_cost_usd:.4f} USD"
            )
        if new_tokens > self.max_tokens:
            raise BudgetExceeded(
                f"token budget exceeded: {new_tokens} > {self.max_tokens}"
            )
        self.cost_usd = new_cost
        self.tokens = new_tokens

    def snapshot(self) -> dict:
        return {
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "max_cost_usd": self.max_cost_usd,
            "max_tokens": self.max_tokens,
        }


class ArchonHarness:
    """In-process phase harness."""

    def __init__(
        self,
        ctx: ProjectContext,
        ledger: ExecutionLedger | None = None,
        *,
        config: RigForgeConfig | None = None,
        registry: "CapabilityRegistry | None" = None,
    ):
        self.ctx = ctx
        self.ledger = ledger or ExecutionLedger(ctx.ledger_file)
        try:
            self.config = config or load_config(ctx)
        except ValueError:
            # ``rigforge.yaml`` is malformed; the doctor gate surfaces the
            # precise error. Fall back to defaults so the harness still runs.
            self.config = RigForgeConfig()
        self.budget = BudgetTracker(
            max_cost_usd=self.config.budgets.max_cost_usd,
            max_tokens=self.config.budgets.max_tokens,
        )
        # The capability registry is the extensibility seam (G009): registered
        # plugin gates run inside the phase pipeline after the built-in gates.
        # Default to the process-wide REGISTRY so ``@capability``-decorated
        # plugins are picked up; tests inject an isolated registry.
        from rigforge.registry import REGISTRY

        # Explicit None check: an empty CapabilityRegistry is falsy via __len__,
        # so ``registry or REGISTRY`` would silently discard an injected empty one.
        self.registry: "CapabilityRegistry" = REGISTRY if registry is None else registry

    # ── Planning ───────────────────────────────────────────────────────

    def plan(self, phase: int) -> list[PlanStep]:
        gates = gates_for_phase(self.ctx, phase)
        steps = [PlanStep(name=g.name, description=f"Run gate: {g.name}") for g in gates]
        for cap in self.registry.for_phase(phase):
            steps.append(
                PlanStep(name=cap.name, description=f"Run capability: {cap.name} [{cap.severity}]")
            )
        steps.append(PlanStep(name="seal", description=f"Seal phase {phase} with ProofPacket"))
        return steps

    # ── Charging (G005) ────────────────────────────────────────────────

    def charge(self, *, cost_usd: float = 0.0, tokens: int = 0,
               actor: str | None = None, note: str | None = None) -> dict:
        """Charge cost/tokens to the run budget, logging to the ledger.

        Raises ``BudgetExceeded`` if the charge would breach a configured
        ceiling. The ledger always records the *attempted* charge so audits
        can reconstruct what tried to happen.
        """
        try:
            self.budget.charge(cost_usd=cost_usd, tokens=tokens)
            outcome = "ok"
        except BudgetExceeded as exc:
            outcome = f"exceeded: {exc}"
            self.ledger.append(
                kind="budget.charge",
                actor=actor,
                charge_cost_usd=cost_usd,
                charge_tokens=tokens,
                outcome=outcome,
                note=note,
                **self.budget.snapshot(),
            )
            raise
        record = self.ledger.append(
            kind="budget.charge",
            actor=actor,
            charge_cost_usd=cost_usd,
            charge_tokens=tokens,
            outcome=outcome,
            note=note,
            **self.budget.snapshot(),
        )
        return record

    # ── Execution ──────────────────────────────────────────────────────

    def _run_gates(self, phase: int) -> list[GateResult]:
        """Run the per-phase gate bundle, honouring scheduler parallelism (G002).

        Each gate is a thunk (zero-arg callable). When ``max_parallel_gates``
        is greater than 1, gates are dispatched to a thread pool — order is
        preserved in the returned list to keep proofs deterministic.
        """
        from rigforge.gates import gate_thunks_for_phase

        # Built-in gates first, then registered capabilities (G009). Order is
        # preserved in the returned list regardless of parallelism so proofs
        # stay deterministic.
        thunks = list(gate_thunks_for_phase(self.ctx, phase))
        thunks += self.registry.thunks_for_phase(self.ctx, phase)
        parallel = self.config.resolve_parallelism()
        if parallel <= 1 or len(thunks) <= 1:
            return [thunk() for thunk in thunks]
        results: list[GateResult | None] = [None] * len(thunks)
        with ThreadPoolExecutor(max_workers=min(parallel, len(thunks))) as pool:
            futures = {pool.submit(thunk): i for i, thunk in enumerate(thunks)}
            for fut in futures:
                idx = futures[fut]
                results[idx] = fut.result()
        return [r for r in results if r is not None]

    def _run_gates_with_eval_loop(self, phase: int) -> tuple[list[GateResult], EvalLoopTranscript]:
        """Run the per-phase gate bundle through the evaluator-optimizer loop.

        The loop wraps each gate thunk in a score → retry → escalate cycle
        (research #2). We reuse ``_run_gates``'s thunk gathering so the
        eval loop sees exactly the same gates (built-ins + registered
        capabilities). The loop is run sequentially — its retry semantics
        are per-gate, so parallel scheduling would interleave attempts and
        muddle the transcript. Determinism is preserved by the in-order
        transcript records.
        """
        from rigforge.gates import gate_thunks_for_phase

        thunks = list(gate_thunks_for_phase(self.ctx, phase))
        thunks += self.registry.thunks_for_phase(self.ctx, phase)
        loop = EvaluatorLoop(max_retries=self.config.eval_loop.max_retries)
        transcript = loop.run_all(thunks)
        gates = EvaluatorLoop.final_results(transcript)
        return gates, transcript

    def run(
        self,
        phase: int,
        *,
        dry_run: bool = False,
        verifier: str | None = None,
        eval_loop: bool | None = None,
    ) -> HarnessResult:
        envelope = RunEnvelope(phase=phase, dry_run=dry_run, verifier=verifier)
        self.ledger.append(
            kind="run.start",
            actor=verifier,
            run_id=envelope.run_id,
            phase=phase,
            dry_run=dry_run,
        )

        # Resolve whether the evaluator-optimizer loop is active. Explicit
        # argument wins; otherwise the config knob (``eval_loop.enabled``)
        # decides; default is off (opt-in) so existing behavior is unchanged.
        loop_active = bool(eval_loop) if eval_loop is not None else self.config.eval_loop.enabled

        if dry_run:
            envelope = envelope.finish()
            result = HarnessResult(phase=phase, envelope=envelope, gates=[], blockers=[], ok=True)
            self.ledger.append(
                kind="run.finish",
                actor=verifier,
                run_id=envelope.run_id,
                phase=phase,
                ok=True,
                dry_run=True,
            )
            return result

        if loop_active:
            gates, transcript = self._run_gates_with_eval_loop(phase)
        else:
            gates = self._run_gates(phase)
            transcript = disabled_transcript()

        blockers = all_blocking_failed(gates)
        envelope = envelope.finish()
        result = HarnessResult(
            phase=phase,
            envelope=envelope,
            gates=gates,
            blockers=blockers,
            ok=not blockers,
            cost_usd=self.budget.cost_usd,
            tokens=self.budget.tokens,
            eval_loop=transcript,
        )
        self.ledger.append(
            kind="run.finish",
            actor=verifier,
            run_id=envelope.run_id,
            phase=phase,
            ok=result.ok,
            blocker_count=len(blockers),
            cost_usd=self.budget.cost_usd,
            tokens=self.budget.tokens,
            eval_loop_enabled=loop_active,
            eval_loop_escalated=transcript.escalated,
        )
        return result

    # ── Resume (G007) ──────────────────────────────────────────────────

    def find_resumable(self) -> dict | None:
        """Return the most recent run that needs to be resumed, if any.

        A run is *resumable* if either:

        * a ``run.start`` event has no matching ``run.finish`` with the same
          ``run_id`` (crashed mid-run), or
        * the latest ``run.finish`` for a phase reports ``ok=False`` (had
          blocking failures).
        """
        events = self.ledger.read()
        starts: dict[str, dict] = {}
        finishes: set[str] = set()
        last_failed_phase: dict | None = None
        for ev in events:
            kind = ev.get("kind")
            run_id = ev.get("run_id")
            if not run_id:
                continue
            if kind == "run.start":
                starts[run_id] = ev
            elif kind == "run.finish":
                finishes.add(run_id)
                if ev.get("ok") is False:
                    last_failed_phase = {
                        "phase": ev.get("phase"),
                        "run_id": run_id,
                        "reason": "previous_run_failed",
                    }
        # Crashed (no finish): newest first.
        for run_id, start in reversed(list(starts.items())):
            if run_id not in finishes and not start.get("dry_run"):
                return {
                    "phase": start.get("phase"),
                    "run_id": run_id,
                    "reason": "no_finish_recorded",
                }
        return last_failed_phase

    def resume(self, *, verifier: str | None = None) -> HarnessResult | None:
        """Re-run the last unfinished or failed phase. Returns ``None`` if
        there is nothing to resume."""
        target = self.find_resumable()
        if target is None or not target.get("phase"):
            return None
        self.ledger.append(
            kind="run.resume",
            actor=verifier,
            phase=target["phase"],
            previous_run_id=target.get("run_id"),
            reason=target.get("reason"),
        )
        return self.run(int(target["phase"]), verifier=verifier)

    # ── Sealing ────────────────────────────────────────────────────────

    def seal(
        self,
        phase: int,
        *,
        verifier: str,
        evidence: str | None = None,
        artifacts: list[Path] | None = None,
        gates: list[GateResult] | None = None,
        envelope: RunEnvelope | None = None,
        eval_loop: EvalLoopTranscript | None = None,
        spec: "SpecBinding | None" = None,
    ) -> ProofPacket:
        artifact_records = [
            ArtifactRecord.from_path(Path(a), base=self.ctx.root) for a in (artifacts or [])
        ]
        gate_records = [
            GateOutcome(name=g.name, passed=g.passed, severity=g.severity, detail=g.detail)
            for g in (gates or [])
        ]
        # Seal the FULL eval-loop transcript (every attempt score + the
        # final outcome) into the packet when the loop ran. None keeps
        # older seal call-sites and pre-loop packets unchanged.
        loop_payload = eval_loop.to_dict() if eval_loop is not None else None
        packet = ProofPacket(
            phase=phase,
            name=PHASES.get(phase, "unknown"),
            verifier=verifier,
            evidence=evidence,
            artifacts=artifact_records,
            gates=gate_records,
            run_envelope=envelope,
            eval_loop=loop_payload,
            spec=spec,
        )
        path = self.ctx.proof_file(phase)
        signing_key = (
            self.config.resolve_signing_key(self.ctx.root)
            if self.config.signing.enabled
            else None
        )
        packet.write(path, signing_key=signing_key)
        self.ledger.append(
            kind="phase.seal",
            actor=verifier,
            phase=phase,
            proof_path=str(path.relative_to(self.ctx.root)),
            artifact_count=len(artifact_records),
            gate_count=len(gate_records),
            signed=bool(signing_key),
        )
        return ProofPacket.load(path)

    # ── Status ─────────────────────────────────────────────────────────

    def status(self) -> dict[int, dict]:
        signing_key = (
            self.config.resolve_signing_key(self.ctx.root)
            if self.config.signing.enabled
            else None
        )
        out: dict[int, dict] = {}
        for p, name in PHASES.items():
            path = self.ctx.proof_file(p)
            if path.exists():
                try:
                    packet = ProofPacket.load(path)
                    signed = bool(packet.signature)
                    # ``signature_ok`` is True/False only when we can actually
                    # check it (signed packet + key available); otherwise None.
                    signature_ok: bool | None = None
                    if signed and signing_key is not None:
                        signature_ok = packet.verify_signature(signing_key)
                    out[p] = {
                        "name": name,
                        "sealed": True,
                        "verifier": packet.verifier,
                        "sealed_at": packet.sealed_at.isoformat(),
                        "integrity_ok": packet.verify_integrity(),
                        "signed": signed,
                        "signature_ok": signature_ok,
                        "artifact_count": len(packet.artifacts),
                    }
                except Exception as exc:  # noqa: BLE001
                    out[p] = {"name": name, "sealed": True, "error": str(exc)}
            else:
                out[p] = {"name": name, "sealed": False}
        return out

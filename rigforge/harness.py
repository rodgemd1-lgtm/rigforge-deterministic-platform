"""
ArchonHarness — minimal but real phase orchestrator.

The harness drives the determinism contract: plan → run gates → either seal
or report blockers. It is intentionally synchronous and in-process; the
``GapFinding`` registry (G002) tracks the upgrade to multi-agent scheduling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rigforge.context import ProjectContext
from rigforge.gates import (
    GateResult,
    all_blocking_failed,
    gates_for_phase,
)
from rigforge.ledger import ExecutionLedger
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

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "ok": self.ok,
            "envelope": self.envelope.to_dict(),
            "gates": [g.to_dict() for g in self.gates],
            "blockers": [g.to_dict() for g in self.blockers],
        }


class ArchonHarness:
    """In-process phase harness."""

    def __init__(self, ctx: ProjectContext, ledger: ExecutionLedger | None = None):
        self.ctx = ctx
        self.ledger = ledger or ExecutionLedger(ctx.ledger_file)

    # ── Planning ───────────────────────────────────────────────────────

    def plan(self, phase: int) -> list[PlanStep]:
        gates = gates_for_phase(self.ctx, phase)
        steps = [PlanStep(name=g.name, description=f"Run gate: {g.name}") for g in gates]
        steps.append(PlanStep(name="seal", description=f"Seal phase {phase} with ProofPacket"))
        return steps

    # ── Execution ──────────────────────────────────────────────────────

    def run(self, phase: int, *, dry_run: bool = False, verifier: str | None = None) -> HarnessResult:
        envelope = RunEnvelope(phase=phase, dry_run=dry_run, verifier=verifier)
        self.ledger.append(
            kind="run.start",
            actor=verifier,
            run_id=envelope.run_id,
            phase=phase,
            dry_run=dry_run,
        )

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

        gates = gates_for_phase(self.ctx, phase)
        blockers = all_blocking_failed(gates)
        envelope = envelope.finish()
        result = HarnessResult(
            phase=phase,
            envelope=envelope,
            gates=gates,
            blockers=blockers,
            ok=not blockers,
        )
        self.ledger.append(
            kind="run.finish",
            actor=verifier,
            run_id=envelope.run_id,
            phase=phase,
            ok=result.ok,
            blocker_count=len(blockers),
        )
        return result

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
    ) -> ProofPacket:
        artifact_records = [
            ArtifactRecord.from_path(Path(a), base=self.ctx.root) for a in (artifacts or [])
        ]
        gate_records = [
            GateOutcome(name=g.name, passed=g.passed, severity=g.severity, detail=g.detail)
            for g in (gates or [])
        ]
        packet = ProofPacket(
            phase=phase,
            name=PHASES.get(phase, "unknown"),
            verifier=verifier,
            evidence=evidence,
            artifacts=artifact_records,
            gates=gate_records,
            run_envelope=envelope,
        )
        path = self.ctx.proof_file(phase)
        packet.write(path)
        self.ledger.append(
            kind="phase.seal",
            actor=verifier,
            phase=phase,
            proof_path=str(path.relative_to(self.ctx.root)),
            artifact_count=len(artifact_records),
            gate_count=len(gate_records),
        )
        return ProofPacket.load(path)

    # ── Status ─────────────────────────────────────────────────────────

    def status(self) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for p, name in PHASES.items():
            path = self.ctx.proof_file(p)
            if path.exists():
                try:
                    packet = ProofPacket.load(path)
                    out[p] = {
                        "name": name,
                        "sealed": True,
                        "verifier": packet.verifier,
                        "sealed_at": packet.sealed_at.isoformat(),
                        "integrity_ok": packet.verify_integrity(),
                        "artifact_count": len(packet.artifacts),
                    }
                except Exception as exc:  # noqa: BLE001
                    out[p] = {"name": name, "sealed": True, "error": str(exc)}
            else:
                out[p] = {"name": name, "sealed": False}
        return out

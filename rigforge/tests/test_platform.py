"""Tests for ProofPacket, RunEnvelope, ExecutionLedger, Harness, Gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rigforge.context import ProjectContext
from rigforge.gates import gate_python_version, gate_repo_layout, gates_for_phase, HARD_BLOCK
from rigforge.harness import ArchonHarness
from rigforge.ledger import ExecutionLedger
from rigforge.proof import (
    ArtifactRecord,
    BYTE_IDENTICAL_CLAIM,
    GateOutcome,
    ModelMetadata,
    ProofPacket,
)
from rigforge.run_envelope import RunEnvelope


@pytest.fixture
def ctx(tmp_path: Path) -> ProjectContext:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "rigforge").mkdir()
    (tmp_path / "contracts").mkdir()
    return ProjectContext.discover(tmp_path)


# ── ProjectContext ──────────────────────────────────────────────────────


class TestProjectContext:
    def test_discover_walks_upward(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        ctx = ProjectContext.discover(nested)
        assert ctx.root == tmp_path.resolve()

    def test_fallback_when_no_marker(self, tmp_path):
        ctx = ProjectContext.discover(tmp_path)
        assert ctx.root == tmp_path.resolve()


# ── RunEnvelope ────────────────────────────────────────────────────────


class TestRunEnvelope:
    def test_envelope_records_metadata(self):
        env = RunEnvelope(phase=1)
        assert env.phase == 1
        assert env.run_id.startswith("run_")
        assert env.python_version
        assert len(env.env_fingerprint) == 64
        assert env.finished_at is None
        finished = env.finish()
        assert finished.finished_at is not None
        assert finished.duration_seconds() is not None

    def test_envelope_serializes(self):
        env = RunEnvelope(phase=2, dry_run=True)
        data = env.to_dict()
        assert data["dry_run"] is True
        assert data["phase"] == 2


# ── ProofPacket ────────────────────────────────────────────────────────


class TestProofPacket:
    def test_artifact_record_hashes_file(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        rec = ArtifactRecord.from_path(f, base=tmp_path)
        assert rec.exists is True
        assert len(rec.sha256) == 64
        assert rec.size_bytes == 5

    def test_artifact_record_missing(self, tmp_path):
        rec = ArtifactRecord.from_path(tmp_path / "nope.txt", base=tmp_path)
        assert rec.exists is False
        assert rec.sha256 == ""

    def test_seal_and_integrity(self, tmp_path):
        packet = ProofPacket(phase=1, name="Bootstrap", verifier="alice", evidence="ok")
        sealed = packet.sealed()
        assert len(sealed.packet_sha256) == 64
        assert sealed.verify_integrity() is True
        # tamper
        bad = sealed.model_copy(update={"evidence": "modified"})
        assert bad.verify_integrity() is False

    def test_write_and_load_roundtrip(self, tmp_path):
        packet = ProofPacket(phase=1, name="Bootstrap", verifier="alice")
        path = tmp_path / "proof.json"
        packet.write(path)
        loaded = ProofPacket.load(path)
        assert loaded.verifier == "alice"
        assert loaded.verify_integrity() is True

    def test_load_legacy_v0(self, tmp_path):
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps({
            "phase": 1, "name": "Bootstrap",
            "sealed_at": "2026-01-01T00:00:00Z",
            "status": "verified", "artifacts": ["a.txt"],
        }))
        loaded = ProofPacket.load(path)
        assert loaded.phase == 1
        assert loaded.schema_version == "0.0.0"


# ── Determinism honesty qualifier (G012) ────────────────────────────────


class TestDeterminismQualifier:
    def test_steps_default_to_deterministic(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        rec = ArtifactRecord.from_path(f, base=tmp_path)
        gate = GateOutcome(name="lint", passed=True)
        assert rec.kind == "deterministic"
        assert gate.kind == "deterministic"
        assert gate.model is None
        assert gate.is_stochastic is False

    def test_stochastic_step_is_tagged_and_records_model_and_seed(self, tmp_path):
        f = tmp_path / "gen.txt"
        f.write_text("llm output")
        art = ArtifactRecord.from_path(f, base=tmp_path, kind="llm-stochastic")
        gate = GateOutcome(
            name="generate",
            passed=True,
            kind="llm-stochastic",
            model=ModelMetadata(model_id="sonnet", version="2025-09", temperature=0.7, seed=42),
        )
        # The stochastic step is tagged...
        assert art.kind == "llm-stochastic"
        assert gate.kind == "llm-stochastic"
        assert gate.is_stochastic is True
        # ...and records the model + seed for reproducibility.
        assert gate.model is not None
        assert gate.model.model_id == "sonnet"
        assert gate.model.seed == 42
        # The packet round-trips the tag + model through JSON.
        packet = ProofPacket(
            phase=1, name="Build", verifier="alice", artifacts=[art], gates=[gate]
        ).sealed()
        path = tmp_path / "proof.json"
        packet.write(path)
        loaded = ProofPacket.load(path)
        assert loaded.gates[0].kind == "llm-stochastic"
        assert loaded.gates[0].model.seed == 42
        assert loaded.artifacts[0].kind == "llm-stochastic"

    def test_byte_identical_claim_is_qualified_not_absolute(self):
        # The exported claim string must NOT be a bare absolute "byte-identical";
        # it must qualify it to deterministic steps and name the stochastic path.
        assert "byte-identical for deterministic steps" in BYTE_IDENTICAL_CLAIM
        assert "llm-stochastic" in BYTE_IDENTICAL_CLAIM
        assert "seed" in BYTE_IDENTICAL_CLAIM


# ── ExecutionLedger ────────────────────────────────────────────────────


class TestLedger:
    def test_append_and_read(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.jsonl")
        ledger.append(kind="run.start", actor="alice", phase=1)
        ledger.append(kind="run.finish", actor="alice", phase=1, ok=True)
        events = ledger.read()
        assert len(events) == 2
        assert events[0]["kind"] == "run.start"
        assert ledger.read(kind="run.finish")[0]["ok"] is True

    def test_read_empty_when_no_file(self, tmp_path):
        assert ExecutionLedger(tmp_path / "nope.jsonl").read() == []


# ── Gates ──────────────────────────────────────────────────────────────


class TestGates:
    def test_python_version_passes(self):
        g = gate_python_version(3, 11)
        assert g.passed is True
        assert g.severity == HARD_BLOCK

    def test_repo_layout(self, ctx):
        g = gate_repo_layout(ctx)
        assert g.passed is True

    def test_gates_for_phase_returns_list(self, ctx):
        for p in range(1, 8):
            assert isinstance(gates_for_phase(ctx, p), list)


# ── ArchonHarness ──────────────────────────────────────────────────────


class TestHarness:
    def test_plan_includes_seal(self, ctx):
        harness = ArchonHarness(ctx)
        steps = harness.plan(1)
        assert any(s.name == "seal" for s in steps)

    def test_dry_run_does_not_seal(self, ctx):
        harness = ArchonHarness(ctx)
        result = harness.run(1, dry_run=True, verifier="alice")
        assert result.ok is True
        assert result.gates == []
        assert not (ctx.proofs_dir / "phase1_proof.json").exists()

    def test_seal_writes_packet_and_ledger(self, ctx, tmp_path):
        harness = ArchonHarness(ctx)
        artifact = ctx.root / "artifact.txt"
        artifact.write_text("hi")
        result = harness.run(1, verifier="alice")
        packet = harness.seal(
            1, verifier="alice", evidence="ok",
            artifacts=[artifact], gates=result.gates, envelope=result.envelope,
        )
        assert packet.verify_integrity()
        assert ctx.ledger_file.exists()
        events = ExecutionLedger(ctx.ledger_file).read(kind="phase.seal")
        assert events and events[-1]["phase"] == 1

    def test_status_round_trip(self, ctx):
        harness = ArchonHarness(ctx)
        run = harness.run(1, verifier="alice")
        harness.seal(1, verifier="alice", gates=run.gates, envelope=run.envelope)
        st = harness.status()
        assert st[1]["sealed"] is True
        assert st[2]["sealed"] is False

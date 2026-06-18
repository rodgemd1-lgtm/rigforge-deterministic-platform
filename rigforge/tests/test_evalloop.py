"""Tests for the evaluator-optimizer loop (research priority #2).

Covers the two load-bearing behaviours the spec demands:

* **convergence** — a gate that fails attempt 1 and passes attempt 2 makes
  the loop converge; the sealed transcript shows BOTH attempts.
* **no-spin** — a gate that always fails stops at the retry cap, escalates,
  and does NOT spin past it.

Plus the wiring: opt-in flag off by default, on when requested, transcript
sealed into the ProofPacket, and the existing 158-test baseline unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rigforge.config import RigForgeConfig, EvalLoopConfig
from rigforge.context import ProjectContext
from rigforge.evalloop import (
    AttemptScore,
    DEFAULT_MAX_RETRIES,
    DeterministicRubric,
    EvaluatorLoop,
    EvalLoopTranscript,
    GateLoopRecord,
    Rubric,
    disabled_transcript,
)
from rigforge.gates import HARD_BLOCK, GateResult
from rigforge.harness import ArchonHarness


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def ctx(tmp_path: Path) -> ProjectContext:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "rigforge").mkdir()
    (tmp_path / "contracts").mkdir()
    return ProjectContext.discover(tmp_path)


class _ScriptedGate:
    """A gate thunk that returns a scripted sequence of results.

    Each call pops the next result. This lets a test say "fail once then
    pass" or "always fail" deterministically, and lets us count how many
    times the loop actually invoked the gate (the no-spin proof).
    """

    def __init__(self, name: str, results: list[GateResult]):
        self.name = name
        self._results = list(results)
        self.call_count = 0

    def __call__(self) -> GateResult:
        # The EvaluatorLoop probes once to learn name+severity, then runs.
        # That probe consumes the first result; we mirror that by serving
        # results in order and reusing the last if the script is exhausted.
        self.call_count += 1
        if len(self._results) == 1:
            return self._results[0]
        return self._results.pop(0)


def _result(name: str, passed: bool, *, detail: str = "ok", severity: str = HARD_BLOCK) -> GateResult:
    return GateResult(name=name, passed=passed, severity=severity, detail=detail)


# ── Rubric (deterministic) ─────────────────────────────────────────────


class TestDeterministicRubric:
    def test_passing_gate_with_detail_scores_pass(self):
        rubric = DeterministicRubric()
        passed, score, note = rubric.score(_result("g", True, detail="ok"))
        assert passed is True
        assert score == 1.0
        assert "gate_passed=True" in note

    def test_failing_gate_with_reason_is_valid_proof_but_still_fails(self):
        # A fail with a detail line is structurally valid proof, but the
        # rubric still returns fail because gate_passed=False.
        rubric = DeterministicRubric()
        passed, score, _ = rubric.score(_result("g", False, detail="boom"))
        assert passed is False
        assert score == 0.0

    def test_failing_gate_with_empty_detail_is_invalid_proof(self):
        # A bare fail with no reason is refused: proof_valid=False forces
        # the rubric to fail regardless of anything else.
        rubric = DeterministicRubric()
        bare = GateResult(name="g", passed=False, severity=HARD_BLOCK, detail="")
        passed, _, note = rubric.score(bare)
        assert passed is False
        assert "proof_valid=False" in note

    def test_empty_name_is_invalid_proof(self):
        rubric = DeterministicRubric()
        passed, _, _ = rubric.score(GateResult(name="", passed=True, detail="x"))
        assert passed is False


# ── CONVERGENCE: fail attempt 1, pass attempt 2 ────────────────────────


class TestLoopConverges:
    """THE convergence proof: fail then pass → loop converges, both
    attempts recorded in the transcript."""

    def test_fail_then_pass_converges_and_records_both_attempts(self):
        # Script: first run fails, second run passes.
        gate = _ScriptedGate(
            "flaky_gate",
            [
                _result("flaky_gate", False, detail="transient error"),
                _result("flaky_gate", True, detail="ok"),
            ],
        )
        loop = EvaluatorLoop(max_retries=3)
        record = loop.run_gate(gate)

        # Converged, not escalated.
        assert record.outcome == "converged"
        assert record.final_passed is True
        assert record.escalation == "none"

        # THE load-bearing assertion: BOTH attempts are in the transcript.
        assert len(record.attempts) == 2
        assert [a.attempt for a in record.attempts] == [1, 2]
        assert record.attempts[0].passed is False  # attempt 1 failed
        assert record.attempts[0].verdict == "fail"
        assert record.attempts[1].passed is True  # attempt 2 passed
        assert record.attempts[1].verdict == "pass"
        assert record.attempts_used == 2

        # The loop did NOT keep spinning after the pass. The probe result is
        # reused as attempt 1 (no wasted run), so invocations == attempts.
        assert gate.call_count == 2

    def test_pass_first_try_records_single_attempt(self):
        gate = _ScriptedGate("solid_gate", [_result("solid_gate", True)])
        loop = EvaluatorLoop(max_retries=3)
        record = loop.run_gate(gate)

        assert record.outcome == "passed_first_try"
        assert record.final_passed is True
        assert len(record.attempts) == 1
        assert record.attempts_used == 1


# ── NO-SPIN: always fails → stop at cap, escalate ──────────────────────


class TestLoopDoesNotSpin:
    """THE no-spin proof: a gate that always fails stops at the retry cap,
    escalates, and never spins past it."""

    def test_always_failing_gate_stops_at_cap_and_escalates(self):
        # Script: always fails (single-result script is served forever).
        always_fail = _ScriptedGate(
            "broken_gate", [_result("broken_gate", False, detail="hard failure")]
        )
        loop = EvaluatorLoop(max_retries=3)
        record = loop.run_gate(always_fail)

        # Escalated, NOT converged.
        assert record.outcome == "escalated"
        assert record.final_passed is False
        assert record.escalation == "hard_block"

        # Stopped exactly at the cap: 3 attempts, not 4, not 30.
        assert len(record.attempts) == 3
        assert record.attempts_used == 3
        assert [a.attempt for a in record.attempts] == [1, 2, 3]
        assert all(a.passed is False for a in record.attempts)

        # THE no-spin assertion: the gate was invoked exactly `attempts_used`
        # times (the probe is reused as attempt 1). If the loop spun past the
        # cap this would be far higher than 3.
        assert always_fail.call_count == 3

    def test_gate_d_escalation_for_destructive_gate(self):
        always_fail = _ScriptedGate(
            "deploy_gate", [_result("deploy_gate", False, detail="no approval")]
        )
        loop = EvaluatorLoop(max_retries=2, gate_d_gates=("deploy_gate",))
        record = loop.run_gate(always_fail)

        assert record.outcome == "escalated"
        assert record.escalation == "gate_d"  # destructive → human gate, not auto-block
        assert record.attempts_used == 2

    def test_max_retries_one_means_no_retry(self):
        # Edge: max_retries=1 → run once, escalate on fail, no retry at all.
        always_fail = _ScriptedGate(
            "g", [_result("g", False, detail="nope")]
        )
        loop = EvaluatorLoop(max_retries=1)
        record = loop.run_gate(always_fail)
        assert record.outcome == "escalated"
        assert len(record.attempts) == 1

    def test_max_retries_must_be_positive(self):
        with pytest.raises(ValueError):
            EvaluatorLoop(max_retries=0)


# ── run_all + transcript ───────────────────────────────────────────────


class TestRunAllTranscript:
    def test_run_all_aggregates_records_and_escalated_flag(self):
        good = _ScriptedGate("good", [_result("good", True)])
        bad = _ScriptedGate("bad", [_result("bad", False, detail="x")])
        loop = EvaluatorLoop(max_retries=2)
        transcript = loop.run_all([good, bad])

        assert isinstance(transcript, EvalLoopTranscript)
        assert transcript.enabled is True
        assert len(transcript.records) == 2
        # One gate escalated → transcript escalated.
        assert transcript.escalated is True
        assert transcript.converged is False

    def test_run_all_converged_when_all_pass(self):
        good = _ScriptedGate("good", [_result("good", True)])
        loop = EvaluatorLoop()
        transcript = loop.run_all([good])
        assert transcript.escalated is False
        assert transcript.converged is True

    def test_final_results_projects_back_to_gate_results(self):
        good = _ScriptedGate("good", [_result("good", True)])
        bad = _ScriptedGate("bad", [_result("bad", False, detail="x")])
        loop = EvaluatorLoop(max_retries=2)
        transcript = loop.run_all([good, bad])
        results = EvaluatorLoop.final_results(transcript)

        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False
        assert all(isinstance(r, GateResult) for r in results)

    def test_disabled_transcript_is_marked_disabled(self):
        t = disabled_transcript()
        assert t.enabled is False
        assert t.records == []
        assert t.converged is False


# ── Harness wiring: opt-in flag ────────────────────────────────────────


class TestHarnessWiring:
    def test_loop_off_by_default(self, ctx):
        harness = ArchonHarness(ctx)
        result = harness.run(1, verifier="tester")
        # Default: loop not active → disabled transcript.
        assert result.eval_loop.enabled is False
        assert result.eval_loop is disabled_transcript() or result.eval_loop.enabled is False

    def test_loop_on_via_argument(self, ctx, monkeypatch):
        # Phase 1 gates are python_version + repo_layout, both pass on a
        # real interpreter + the scaffolded ctx. So the loop should run
        # and converge (every gate passes first try).
        harness = ArchonHarness(ctx)
        result = harness.run(1, verifier="tester", eval_loop=True)

        assert result.eval_loop.enabled is True
        assert result.eval_loop.converged is True
        assert result.eval_loop.escalated is False
        # Each gate record shows passed_first_try.
        outcomes = {r.outcome for r in result.eval_loop.records}
        assert outcomes <= {"passed_first_try", "converged"}

    def test_loop_on_via_config(self, ctx):
        # Config knob enables the loop without the argument.
        harness = ArchonHarness(ctx)
        harness.config.eval_loop = EvalLoopConfig(enabled=True, max_retries=2)
        result = harness.run(1, verifier="tester")
        assert result.eval_loop.enabled is True
        assert result.eval_loop.max_retries == 2

    def test_argument_overrides_config_off(self, ctx):
        # Config says on, explicit False wins.
        harness = ArchonHarness(ctx)
        harness.config.eval_loop = EvalLoopConfig(enabled=True)
        result = harness.run(1, verifier="tester", eval_loop=False)
        assert result.eval_loop.enabled is False

    def test_dry_run_skips_loop(self, ctx):
        harness = ArchonHarness(ctx)
        result = harness.run(1, dry_run=True, verifier="tester", eval_loop=True)
        assert result.eval_loop.enabled is False  # dry-run never runs gates

    def test_transcript_attached_to_result_to_dict(self, ctx):
        harness = ArchonHarness(ctx)
        result = harness.run(1, verifier="tester", eval_loop=True)
        payload = result.to_dict()
        assert "eval_loop" in payload
        assert payload["eval_loop"]["enabled"] is True


# ── Sealing: transcript sealed into the ProofPacket ────────────────────


class TestSealSealsTranscript:
    def test_seal_writes_eval_loop_transcript_into_packet(self, ctx):
        harness = ArchonHarness(ctx)
        result = harness.run(1, verifier="tester", eval_loop=True)
        packet = harness.seal(
            1,
            verifier="tester",
            evidence="phase 1 done",
            gates=result.gates,
            envelope=result.envelope,
            eval_loop=result.eval_loop,
        )

        # The transcript is in the sealed packet.
        assert packet.eval_loop is not None
        assert packet.eval_loop["enabled"] is True
        assert len(packet.eval_loop["records"]) >= 1
        # Each record carries its attempt history.
        for rec in packet.eval_loop["records"]:
            assert "attempts" in rec
            assert len(rec["attempts"]) >= 1

        # Integrity still holds with the new field.
        assert packet.verify_integrity() is True

    def test_seal_without_loop_leaves_eval_loop_none(self, ctx):
        harness = ArchonHarness(ctx)
        result = harness.run(1, verifier="tester")  # loop off
        packet = harness.seal(
            1,
            verifier="tester",
            evidence="phase 1 done",
            gates=result.gates,
            envelope=result.envelope,
            eval_loop=None,
        )
        assert packet.eval_loop is None
        assert packet.verify_integrity() is True

    def test_sealed_packet_round_trips_through_disk(self, ctx):
        harness = ArchonHarness(ctx)
        result = harness.run(1, verifier="tester", eval_loop=True)
        harness.seal(
            1,
            verifier="tester",
            evidence="phase 1 done",
            gates=result.gates,
            envelope=result.envelope,
            eval_loop=result.eval_loop,
        )
        from rigforge.proof import ProofPacket

        reloaded = ProofPacket.load(ctx.proof_file(1))
        assert reloaded.eval_loop is not None
        assert reloaded.eval_loop["enabled"] is True
        assert reloaded.verify_integrity() is True


# ── End-to-end convergence through the harness with a scripted gate ────


class TestHarnessEndToEndConvergence:
    """Drive the harness's own loop path with a gate that fails then passes,
    proving the wiring (not just the loop in isolation) converges and seals
    both attempts."""

    def test_harness_loop_converges_on_flaky_gate(self, ctx, monkeypatch):
        # Patch the gate-thunk builder for phase 1 to return a scripted
        # flaky gate that fails once then passes. This exercises the full
        # harness.run → _run_gates_with_eval_loop → EvaluatorLoop path.
        flaky = _ScriptedGate(
            "flaky_phase_gate",
            [
                _result("flaky_phase_gate", False, detail="race"),
                _result("flaky_phase_gate", True, detail="ok"),
            ],
        )

        def fake_thunks(_ctx, _phase):
            return [flaky]

        monkeypatch.setattr(
            "rigforge.harness.gate_thunks_for_phase",
            fake_thunks,
            raising=False,
        )
        # The harness imports gate_thunks_for_phase lazily inside the method;
        # patch at the source module too.
        import rigforge.gates as gates_mod

        monkeypatch.setattr(gates_mod, "gate_thunks_for_phase", fake_thunks)

        harness = ArchonHarness(ctx)
        # Empty registry so no capability thunks are appended.
        from rigforge.registry import CapabilityRegistry

        harness.registry = CapabilityRegistry()

        result = harness.run(1, verifier="tester", eval_loop=True)

        assert result.eval_loop.enabled is True
        assert result.eval_loop.escalated is False
        rec = result.eval_loop.record_for("flaky_phase_gate")
        assert rec is not None
        assert rec.outcome == "converged"
        # BOTH attempts sealed.
        assert len(rec.attempts) == 2
        assert rec.attempts[0].passed is False
        assert rec.attempts[1].passed is True

    def test_harness_loop_escalates_on_always_failing_gate(self, ctx, monkeypatch):
        always_fail = _ScriptedGate(
            "broken_phase_gate",
            [_result("broken_phase_gate", False, detail="hard")],
        )

        def fake_thunks(_ctx, _phase):
            return [always_fail]

        import rigforge.gates as gates_mod

        monkeypatch.setattr(gates_mod, "gate_thunks_for_phase", fake_thunks)

        harness = ArchonHarness(ctx)
        from rigforge.registry import CapabilityRegistry

        harness.registry = CapabilityRegistry()

        result = harness.run(1, verifier="tester", eval_loop=True)

        # Escalated at the cap (default 3) — no spin.
        rec = result.eval_loop.record_for("broken_phase_gate")
        assert rec.outcome == "escalated"
        assert rec.escalation == "hard_block"
        assert rec.attempts_used == 3
        assert len(rec.attempts) == 3
        # Invoked exactly attempts_used times (probe reused as attempt 1).
        assert always_fail.call_count == 3

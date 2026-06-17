"""Pillar A tests — capability registry (G009) + goal harness (G010).

These tests prove the two harness/capability features RUN, not just exist:

* a custom capability registers and actually executes inside the phase
  pipeline (its result lands in the HarnessResult gates),
* a hard-blocking capability can block a phase,
* the goal loop converges and stops on proof,
* the goal loop respects the iteration cap and the cost/token budget.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigforge.context import ProjectContext
from rigforge.gates import ADVISORY, HARD_BLOCK, GateResult
from rigforge.harness import ArchonHarness
from rigforge.registry import Capability, CapabilityRegistry, capability


@pytest.fixture
def ctx(tmp_path: Path) -> ProjectContext:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "rigforge").mkdir()
    (tmp_path / "contracts").mkdir()
    return ProjectContext.discover(tmp_path)


# ── G009 capability registry ────────────────────────────────────────────


class TestCapabilityRegistry:
    def test_register_and_query(self):
        reg = CapabilityRegistry()
        reg.register("c1", lambda c: GateResult(name="c1", passed=True), phases=[1])
        assert "c1" in reg
        assert len(reg) == 1
        assert reg.names() == ["c1"]
        assert reg.for_phase(1) and not reg.for_phase(2)

    def test_all_phases_wildcard(self):
        reg = CapabilityRegistry()
        reg.register("everywhere", lambda c: GateResult(name="everywhere", passed=True))
        assert reg.for_phase(1) and reg.for_phase(7)

    def test_duplicate_rejected(self):
        reg = CapabilityRegistry()
        reg.register("dup", lambda c: GateResult(name="dup", passed=True))
        with pytest.raises(ValueError):
            reg.register("dup", lambda c: GateResult(name="dup", passed=True))
        # replace=True is allowed
        reg.register("dup", lambda c: GateResult(name="dup", passed=False), replace=True)
        assert reg.get("dup") is not None

    def test_bad_severity_rejected(self):
        reg = CapabilityRegistry()
        with pytest.raises(ValueError):
            reg.register("c", lambda c: GateResult(name="c", passed=True), severity="explode")

    def test_bad_phase_rejected(self):
        reg = CapabilityRegistry()
        with pytest.raises(ValueError):
            reg.register("c", lambda c: GateResult(name="c", passed=True), phases=[99])

    def test_capability_crash_is_failed_gate_not_skip(self, ctx):
        def boom(_c):
            raise RuntimeError("kaboom")

        cap = Capability(name="boom", fn=boom, severity=HARD_BLOCK)
        result = cap.run(ctx)
        assert result.passed is False
        assert "kaboom" in result.detail

    def test_capability_wrong_return_type_is_failed_gate(self, ctx):
        cap = Capability(name="bad", fn=lambda c: "not a gate")  # type: ignore[arg-type]
        result = cap.run(ctx)
        assert result.passed is False
        assert "expected GateResult" in result.detail

    def test_decorator_registers_into_target(self):
        reg = CapabilityRegistry()

        @capability("deco", phases=[2], severity=ADVISORY, registry=reg)
        def _check(c):
            return GateResult(name="deco", passed=True)

        assert "deco" in reg
        assert reg.get("deco").severity == ADVISORY


# ── G009 capability runs INSIDE the phase pipeline ──────────────────────


class TestCapabilityRunsInPipeline:
    def test_custom_capability_actually_runs_in_phase(self, ctx):
        """The crux: a registered capability must EXECUTE in harness.run()."""
        reg = CapabilityRegistry()
        calls: list[str] = []

        def my_cap(c: ProjectContext) -> GateResult:
            calls.append("ran")
            return GateResult(name="my_cap", passed=True, severity=ADVISORY)

        reg.register("my_cap", my_cap, phases=[1], severity=ADVISORY)

        harness = ArchonHarness(ctx, registry=reg)
        result = harness.run(1, verifier="tester")

        gate_names = [g.name for g in result.gates]
        assert "my_cap" in gate_names, f"capability did not run; gates={gate_names}"
        assert calls == ["ran"]
        assert result.ok is True

    def test_capability_appears_in_plan(self, ctx):
        reg = CapabilityRegistry()
        reg.register("planned", lambda c: GateResult(name="planned", passed=True), phases=[1])
        harness = ArchonHarness(ctx, registry=reg)
        steps = [s.name for s in harness.plan(1)]
        assert "planned" in steps
        assert steps[-1] == "seal"

    def test_hard_block_capability_can_block_phase(self, ctx):
        reg = CapabilityRegistry()
        reg.register(
            "must_fail",
            lambda c: GateResult(name="must_fail", passed=False),
            phases=[1],
            severity=HARD_BLOCK,
        )
        harness = ArchonHarness(ctx, registry=reg)
        result = harness.run(1, verifier="tester")
        assert result.ok is False
        assert any(b.name == "must_fail" for b in result.blockers)

    def test_capability_only_runs_for_targeted_phase(self, ctx):
        reg = CapabilityRegistry()
        ran: list[int] = []

        def cap(c: ProjectContext) -> GateResult:
            ran.append(1)
            return GateResult(name="phase2_only", passed=True, severity=ADVISORY)

        reg.register("phase2_only", cap, phases=[2], severity=ADVISORY)
        harness = ArchonHarness(ctx, registry=reg)
        harness.run(1, verifier="t")
        assert ran == []  # did not run on phase 1
        names = [g.name for g in harness.run(2, verifier="t").gates]
        assert "phase2_only" in names

    def test_capability_runs_under_parallelism(self, ctx, monkeypatch):
        monkeypatch.setenv("RIGFORGE_MAX_PARALLEL_GATES", "4")
        reg = CapabilityRegistry()
        reg.register("par_cap", lambda c: GateResult(name="par_cap", passed=True, severity=ADVISORY),
                     phases=[2], severity=ADVISORY)
        harness = ArchonHarness(ctx, registry=reg)
        result = harness.run(2, verifier="t")
        assert "par_cap" in [g.name for g in result.gates]


# ── G010 goal harness ───────────────────────────────────────────────────


class TestGoalHarness:
    def test_converges_and_stops_on_proof(self, ctx):
        from rigforge.goal import GoalHarness

        gh = GoalHarness(ctx)
        result = gh.converge(
            phases=[1],
            done_check=lambda c: True,
            verifier="tester",
            max_iterations=5,
        )
        assert result.proven is True
        assert result.stop_reason == "proven"
        assert result.iteration_count == 1  # proved on the first clean pass
        assert result.proof is not None
        assert result.proof.verify_integrity()
        # Sealing actually wrote a proof packet for the final phase.
        assert ctx.proof_file(1).exists()

    def test_done_check_gating_converges_when_artifact_appears(self, ctx):
        from rigforge.goal import GoalHarness

        target = ctx.root / "deliverable.txt"
        state = {"iter": 0}

        def done_check(c: ProjectContext) -> bool:
            # Simulate a generator that produces the artifact on the 2nd pass.
            state["iter"] += 1
            if state["iter"] >= 2:
                target.write_text("done")
            return target.exists()

        gh = GoalHarness(ctx)
        result = gh.converge(
            phases=[1],
            done_check=done_check,
            verifier="tester",
            max_iterations=5,
        )
        assert result.proven is True
        assert result.iteration_count == 2

    def test_respects_max_iterations_cap(self, ctx):
        from rigforge.goal import GoalHarness

        gh = GoalHarness(ctx)
        result = gh.converge(
            phases=[1],
            done_check=lambda c: False,  # never proven
            verifier="tester",
            max_iterations=3,
        )
        assert result.proven is False
        assert result.stop_reason == "max_iterations"
        assert result.iteration_count == 3
        assert result.proof is None

    def test_respects_cost_budget_cap(self, ctx):
        from rigforge.goal import GoalHarness

        # Tighten the cost budget so the 3rd charge blows it.
        ctx.config_file.write_text("budgets:\n  max_cost_usd: 0.25\n  max_tokens: 1000000\n")
        gh = GoalHarness(ctx)
        result = gh.converge(
            phases=[1],
            done_check=lambda c: False,  # force it to keep iterating
            verifier="tester",
            max_iterations=10,
            cost_per_iteration=0.10,
        )
        assert result.proven is False
        assert result.stop_reason == "budget"
        # 0.10 + 0.10 ok (0.20), third charge → 0.30 > 0.25 → stop on iter 3.
        assert result.iteration_count == 3
        assert result.cost_usd == pytest.approx(0.20)

    def test_respects_token_budget_cap(self, ctx):
        from rigforge.goal import GoalHarness

        ctx.config_file.write_text("budgets:\n  max_cost_usd: 100\n  max_tokens: 25\n")
        gh = GoalHarness(ctx)
        result = gh.converge(
            phases=[1],
            done_check=lambda c: False,
            verifier="tester",
            max_iterations=10,
            tokens_per_iteration=10,
        )
        assert result.proven is False
        assert result.stop_reason == "budget"
        assert result.tokens == 20  # 10 + 10 ok, third (30) rejected

    def test_stops_when_phase_blocked(self, ctx):
        from rigforge.goal import GoalHarness
        from rigforge.registry import CapabilityRegistry

        reg = CapabilityRegistry()
        reg.register("blocker", lambda c: GateResult(name="blocker", passed=False),
                     phases=[1], severity=HARD_BLOCK)
        harness = ArchonHarness(ctx, registry=reg)
        gh = GoalHarness(ctx, harness=harness)
        result = gh.converge(
            phases=[1],
            done_check=lambda c: True,
            verifier="tester",
            max_iterations=5,
        )
        assert result.proven is False
        assert result.stop_reason == "phase_blocked"
        assert result.iterations[-1].blocked_phase == 1

    def test_empty_phases_rejected(self, ctx):
        from rigforge.goal import GoalHarness

        with pytest.raises(ValueError):
            GoalHarness(ctx).converge(phases=[], done_check=lambda c: True, verifier="t")

    def test_ledger_records_goal_lifecycle(self, ctx):
        from rigforge.goal import GoalHarness
        from rigforge.ledger import ExecutionLedger

        GoalHarness(ctx).converge(
            phases=[1], done_check=lambda c: True, verifier="tester", max_iterations=2
        )
        starts = ExecutionLedger(ctx.ledger_file).read(kind="goal.start")
        finishes = ExecutionLedger(ctx.ledger_file).read(kind="goal.finish")
        assert starts and finishes
        assert finishes[-1]["proven"] is True
        assert finishes[-1]["stop_reason"] == "proven"


# ── G009/G010 CLI ───────────────────────────────────────────────────────


class TestCapabilityAndGoalCLI:
    def test_capabilities_cli_lists_from_env_module(self, ctx, monkeypatch, tmp_path):
        from click.testing import CliRunner
        from rigforge.cli import main

        # Write a plugin module that registers a capability on import.
        plugin = tmp_path / "myplugin.py"
        plugin.write_text(
            "from rigforge.registry import capability\n"
            "from rigforge.gates import GateResult, ADVISORY\n"
            "@capability('plugin_cap', phases=[1], severity=ADVISORY)\n"
            "def _c(ctx):\n"
            "    return GateResult(name='plugin_cap', passed=True)\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.setenv("RIGFORGE_CAPABILITY_MODULES", "myplugin")
        # Clear the shared registry first so the test is hermetic.
        from rigforge.registry import REGISTRY
        REGISTRY.clear()

        runner = CliRunner()
        import json as _json
        r = runner.invoke(main, ["--cwd", str(ctx.root), "--json", "capabilities"])
        assert r.exit_code == 0, r.output
        payload = _json.loads(r.output)
        names = [c["name"] for c in payload["capabilities"]]
        assert "plugin_cap" in names
        assert "myplugin" in payload["imported_modules"]
        REGISTRY.clear()

    def test_goal_cli_converges_and_seals(self, ctx):
        from click.testing import CliRunner
        from rigforge.cli import main
        import json as _json

        # Clean shared registry so it doesn't block phase 1.
        from rigforge.registry import REGISTRY
        REGISTRY.clear()

        runner = CliRunner()
        r = runner.invoke(
            main, ["--cwd", str(ctx.root), "--json", "goal", "--phases", "1", "--max-iterations", "3"]
        )
        assert r.exit_code == 0, r.output
        payload = _json.loads(r.output)
        assert payload["proven"] is True
        assert payload["stop_reason"] == "proven"

    def test_goal_cli_fails_when_artifact_missing(self, ctx):
        from click.testing import CliRunner
        from rigforge.cli import main
        import json as _json

        from rigforge.registry import REGISTRY
        REGISTRY.clear()

        runner = CliRunner()
        r = runner.invoke(
            main,
            ["--cwd", str(ctx.root), "--json", "goal", "--phases", "1",
             "--done-artifact", str(ctx.root / "never.txt"), "--max-iterations", "2"],
        )
        assert r.exit_code == 1
        payload = _json.loads(r.output)
        assert payload["proven"] is False
        assert payload["stop_reason"] == "max_iterations"

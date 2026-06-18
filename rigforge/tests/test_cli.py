"""Tests for the upgraded RIGForge CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rigforge.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A minimal RIGForge project rooted at ``tmp_path``."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "rigforge").mkdir()
    (tmp_path / "contracts" / "v1").mkdir(parents=True)
    return tmp_path


def _invoke(runner, project: Path, *args: str):
    return runner.invoke(main, ["--cwd", str(project), *args])


# ── version + top-level ─────────────────────────────────────────────────


class TestVersion:
    def test_version_flag(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output


# ── init ────────────────────────────────────────────────────────────────


class TestInit:
    def test_init_scaffolds_layout(self, runner, tmp_path):
        result = _invoke(runner, tmp_path, "init")
        assert result.exit_code == 0, result.output
        assert (tmp_path / "proofs").is_dir()
        assert (tmp_path / "ledger").is_dir()
        assert (tmp_path / "docs").is_dir()
        assert (tmp_path / "rigforge.yaml").exists()

    def test_init_json(self, runner, tmp_path):
        result = _invoke(runner, tmp_path, "--json", "init")
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert "rigforge.yaml" in payload["created"]


# ── verdicts (swarm board) ──────────────────────────────────────────────


class TestVerdicts:
    def _seed(self, project: Path):
        from rigforge.ledger import ExecutionLedger

        led = ExecutionLedger(project / "ledger" / "execution.jsonl")
        for _ in range(3):
            led.append(kind="verify", actor="good-agent", accepted=True)
        led.append(kind="verify", actor="rogue-bot", accepted=False)

    def test_verdicts_json(self, runner, project):
        self._seed(project)
        result = _invoke(runner, project, "--json", "verdicts")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["verdicts"]["good-agent"]["accepted"] == 3
        assert payload["verdicts"]["rogue-bot"]["rejected"] == 1

    def test_verdicts_text_lists_agents(self, runner, project):
        self._seed(project)
        result = _invoke(runner, project, "verdicts")
        assert result.exit_code == 0, result.output
        assert "good-agent" in result.output
        assert "rogue-bot" in result.output


# ── spec-check (spec-bound proofs, Move #2) ──────────────────────────────


class TestSpecCheck:
    def _write_proof(self, project: Path, *, lint_gate: bool):
        import secrets

        from rigforge.proof import GateOutcome, ProofPacket
        from rigforge.spec import Spec, SpecBinding

        spec_f = project / "spec.md"
        spec_f.write_text("## AC\n- [ ] tests pass\n- [ ] lint clean\n")
        key = secrets.token_bytes(32)
        gates = [GateOutcome(name="tests pass", passed=True)]
        if lint_gate:
            gates.append(GateOutcome(name="lint clean", passed=True))
        packet = ProofPacket(
            phase=1, name="a", verifier="v", evidence="e", gates=gates,
            spec=SpecBinding.of(Spec.from_file(spec_f)),
        ).sealed(signing_key=key)
        proof_f = project / "proof.json"
        packet.write(proof_f, signing_key=key)
        return proof_f, spec_f

    def test_spec_check_pass(self, runner, tmp_path):
        proof, spec = self._write_proof(tmp_path, lint_gate=True)
        r = _invoke(runner, tmp_path, "spec-check", "--proof", str(proof), "--spec", str(spec))
        assert r.exit_code == 0, r.output
        assert "PASS" in r.output

    def test_spec_check_fails_on_skipped_criterion(self, runner, tmp_path):
        proof, spec = self._write_proof(tmp_path, lint_gate=False)
        r = _invoke(runner, tmp_path, "spec-check", "--proof", str(proof), "--spec", str(spec))
        assert r.exit_code == 1
        assert "MISSING" in r.output and "lint clean" in r.output


# ── doctor ──────────────────────────────────────────────────────────────


class TestDoctor:
    def test_doctor_runs(self, runner, project):
        result = _invoke(runner, project, "--json", "doctor")
        payload = json.loads(result.output)
        names = [c["name"] for c in payload["checks"]]
        assert "python_version" in names
        assert "repo_layout" in names
        assert "ci_workflow" in names


# ── run + seal + verify ─────────────────────────────────────────────────


class TestRunSealVerify:
    def test_run_dry_run(self, runner, project):
        result = _invoke(runner, project, "--json", "run", "1", "--dry-run")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["phase"] == 1
        assert payload["envelope"]["dry_run"] is True
        assert payload["gates"] == []

    def test_run_invalid_phase(self, runner, project):
        assert _invoke(runner, project, "run", "0").exit_code != 0
        assert _invoke(runner, project, "run", "8").exit_code != 0

    def test_seal_writes_proof_with_hash(self, runner, project):
        _invoke(runner, project, "init")
        # phase 1 only checks python+repo layout — should pass in a normal env
        artifact = project / "docs" / "evidence.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("hello")
        result = _invoke(
            runner, project, "--json",
            "seal", "1",
            "--artifact", str(artifact),
            "--evidence", "phase 1 bootstrap complete",
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert len(payload["packet_sha256"]) == 64
        proof_path = project / "proofs" / "phase1_proof.json"
        assert proof_path.exists()
        raw = json.loads(proof_path.read_text())
        assert raw["verifier"]
        assert raw["packet_sha256"] == payload["packet_sha256"]
        assert raw["artifacts"][0]["sha256"]

    def test_verify_reports_unsealed(self, runner, project):
        _invoke(runner, project, "init")
        result = _invoke(runner, project, "--json", "verify")
        # No phases sealed → ok=True (no errors), but all phases NOT SEALED
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert all(not p["sealed"] for p in payload["phases"])

    def test_verify_strict_passes_after_seal(self, runner, project):
        _invoke(runner, project, "init")
        artifact = project / "docs" / "evidence.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("hi")
        seal_res = _invoke(runner, project, "seal", "1", "--artifact", str(artifact))
        assert seal_res.exit_code == 0
        result = _invoke(runner, project, "--json", "verify", "--strict")
        payload = json.loads(result.output)
        assert payload["ok"] is True
        phase1 = next(p for p in payload["phases"] if p["phase"] == 1)
        assert phase1["integrity_ok"] is True


# ── contract group ─────────────────────────────────────────────────────


class TestContractGroup:
    def test_contract_list_empty(self, runner, project):
        result = _invoke(runner, project, "--json", "contract", "list")
        payload = json.loads(result.output)
        assert payload["count"] == 0

    def test_contract_create_and_validate(self, runner, project, tmp_path):
        out = project / "contracts" / "v1" / "demo.yaml"
        result = _invoke(
            runner, project,
            "contract", "create",
            "--studio", "strategy",
            "--lane", "BC-DEMO-V1",
            "--objective", "demo contract",
            "--out", str(out),
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        val = _invoke(runner, project, "--json", "contract", "validate", str(out))
        assert val.exit_code == 0
        assert json.loads(val.output)["ok"] is True

    def test_contract_inspect(self, runner, project):
        out = project / "contracts" / "v1" / "demo.yaml"
        _invoke(
            runner, project,
            "contract", "create",
            "--studio", "strategy", "--lane", "BC-DEMO-V1",
            "--out", str(out),
        )
        result = _invoke(runner, project, "--json", "contract", "inspect", str(out))
        payload = json.loads(result.output)
        assert payload["studio"] == "strategy"
        assert payload["lane"] == "BC-DEMO-V1"


# ── archon group ───────────────────────────────────────────────────────


class TestArchon:
    def test_archon_plan(self, runner, project):
        result = _invoke(runner, project, "--json", "archon", "plan", "1")
        payload = json.loads(result.output)
        assert payload["phase"] == 1
        assert any(s["name"] == "seal" for s in payload["steps"])

    def test_archon_status_includes_ledger(self, runner, project):
        _invoke(runner, project, "init")
        _invoke(runner, project, "run", "1", "--dry-run")
        result = _invoke(runner, project, "--json", "archon", "status")
        payload = json.loads(result.output)
        assert len(payload["ledger_tail"]) >= 2  # run.start + run.finish


# ── review / questions / gaps ──────────────────────────────────────────


class TestReview:
    def test_questions_has_20(self, runner):
        result = runner.invoke(main, ["--json", "questions"])
        payload = json.loads(result.output)
        assert len(payload["questions"]) == 20

    def test_gaps_listed(self, runner):
        result = runner.invoke(main, ["--json", "gaps"])
        payload = json.loads(result.output)
        assert payload["gaps"]
        assert all("id" in g for g in payload["gaps"])

    def test_review_text(self, runner, project):
        result = _invoke(runner, project, "review")
        assert result.exit_code == 0
        assert "engineering questions" in result.output


# ── MCP serve help still works ─────────────────────────────────────────


class TestMCPHelp:
    def test_mcp_serve_help(self, runner):
        result = runner.invoke(main, ["mcp-serve", "--help"])
        assert result.exit_code == 0
        assert "MCP" in result.output or "mcp" in result.output.lower()

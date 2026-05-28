"""Tests for RIGForge CLI commands."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rigforge.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory with proofs and contracts."""
    proofs_dir = tmp_path / "proofs"
    proofs_dir.mkdir()
    return tmp_path


class TestVersionCommand:
    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output


class TestStatusCommand:
    def test_status_no_proofs(self, runner):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("rigforge.cli.PROOF_DIR", Path(tmp) / "proofs"):
                with patch("rigforge.cli.Path", lambda p: Path(tmp) / p if not Path(p).is_absolute() else Path(p)):
                    result = runner.invoke(main, ["status"])
                    assert result.exit_code == 0
                    assert "unsealed" in result.output or "Phase" in result.output


class TestRunCommand:
    def test_run_phase_1(self, runner):
        result = runner.invoke(main, ["run", "1"])
        assert result.exit_code == 0
        assert "Phase 1" in result.output

    def test_run_phase_7(self, runner):
        result = runner.invoke(main, ["run", "7"])
        assert result.exit_code == 0
        assert "Phase 7" in result.output

    def test_run_invalid_phase(self, runner):
        result = runner.invoke(main, ["run", "0"])
        assert result.exit_code != 0

    def test_run_phase_8_fails(self, runner):
        result = runner.invoke(main, ["run", "8"])
        assert result.exit_code != 0


class TestSealCommand:
    def test_seal_creates_proof(self, runner, tmp_path):
        proof_dir = tmp_path / "proofs"
        with patch("rigforge.cli.PROOF_DIR", proof_dir):
            result = runner.invoke(main, ["seal", "1"])
            assert result.exit_code == 0
            assert "Sealing Phase 1" in result.output


class TestVerifyCommand:
    def test_verify_no_proofs(self, runner, tmp_path):
        proof_dir = tmp_path / "proofs"
        proof_dir.mkdir()
        with patch("rigforge.cli.PROOF_DIR", proof_dir):
            result = runner.invoke(main, ["verify"])
            assert result.exit_code == 0
            assert "NOT SEALED" in result.output or "not yet sealed" in result.output

    def test_verify_with_proofs(self, runner, tmp_path):
        proof_dir = tmp_path / "proofs"
        proof_dir.mkdir()
        for i in range(1, 8):
            proof = {"phase": i, "name": f"Phase {i}", "sealed_at": "2026-01-01T00:00:00Z", "status": "verified"}
            (proof_dir / f"phase{i}_proof.json").write_text(json.dumps(proof))
        with patch("rigforge.cli.PROOF_DIR", proof_dir):
            result = runner.invoke(main, ["verify"])
            assert result.exit_code == 0
            assert "All 7 phases verified" in result.output


class TestContractCommand:
    def test_contract_list(self, runner):
        result = runner.invoke(main, ["contract"])
        assert result.exit_code == 0
        assert "DoneContract" in result.output


class TestMCPCommand:
    def test_mcp_serve_help(self, runner):
        result = runner.invoke(main, ["mcp-serve", "--help"])
        assert result.exit_code == 0
        assert "MCP" in result.output or "mcp" in result.output.lower()
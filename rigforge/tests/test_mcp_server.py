"""Tests for RIGForge MCP server."""

import pytest
from rigforge.mcp_server import (
    contract_create,
    contract_validate,
    contract_list,
    phase_status,
    proof_seal,
)


class TestContractCreate:
    def test_create_basic(self):
        result = contract_create(studio="test", lane="BC-TEST-V1")
        assert result["studio"] == "test"
        assert result["lane"] == "BC-TEST-V1"
        assert result["verifier_package"]["generator"] == "PyCode"
        assert result["verifier_package"]["verifier"] == "Codex CLI"
        assert result["verifier_package"]["evaluator"] == "Human"

    def test_create_with_objective(self):
        result = contract_create(studio="strategy", lane="BC-STRAT-V1", objective="Build strategy")
        assert result["objective"] == "Build strategy"


class TestContractValidate:
    def test_validate_valid(self):
        contract = {
            "studio": "app",
            "lane": "D3-APP-V1",
            "verifier_package": {
                "generator": "PyCode",
                "verifier": "Codex CLI",
                "evaluator": "Human",
            },
        }
        result = contract_validate(contract)
        assert result["valid"] is True

    def test_validate_invalid(self):
        contract = {"studio": ""}  # Missing required lane
        result = contract_validate(contract)
        assert result["valid"] is False


class TestContractList:
    def test_list_returns_list(self):
        result = contract_list()
        assert isinstance(result, list)


class TestProofSeal:
    def test_seal_phase(self, tmp_path):
        import rigforge.mcp_server as mcp
        original_dir = mcp.PROOF_DIR
        mcp.PROOF_DIR = tmp_path / "proofs"
        try:
            result = proof_seal(phase=1)
            assert result["phase"] == 1
            assert result["status"] == "verified"
        finally:
            mcp.PROOF_DIR = original_dir
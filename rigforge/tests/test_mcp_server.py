"""Tests for RIGForge MCP server."""

import pytest
from rigforge.mcp_server import (
    contract_create,
    contract_validate,
    contract_list,
    handle_jsonrpc,
    list_tools,
    phase_status,
    proof_seal,
    seal_and_verify,
)
from rigforge.ledger import ExecutionLedger


class TestSealAndVerify:
    """Move #3 — the one-call MCP adoption path that feeds the swarm board."""

    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("RIGFORGE_SIGNING_KEY", "test-signing-key")
        (tmp_path / "build.bin").write_text("real build output")

    def test_honest_claim_accepted_and_recorded(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        v = seal_and_verify("claude-code", "feature X", artifacts=["build.bin"])
        assert v["accepted"] is True
        assert v["integrity_ok"] is True
        assert v["signature_ok"] is True
        assert v["packet_sha256"]
        board = ExecutionLedger(tmp_path / "ledger" / "execution.jsonl").verdicts()
        assert board["claude-code"]["accepted"] == 1
        assert board["claude-code"]["rejected"] == 0

    def test_exposed_in_catalogue(self):
        assert any(t["name"] == "gev.seal_and_verify" for t in list_tools())

    def test_callable_over_jsonrpc(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        resp = handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "gev.seal_and_verify",
                    "arguments": {"agent": "cursor", "name": "feat Y", "artifacts": ["build.bin"]},
                },
            }
        )
        assert "error" not in resp
        assert "result" in resp

    def test_spec_bound_accept_when_criteria_met(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / "spec.md").write_text("## AC\n- [ ] tests pass\n- [ ] lint clean\n")
        v = seal_and_verify(
            "good",
            "feat",
            artifacts=["build.bin"],
            gates=[{"name": "tests pass", "passed": True}, {"name": "lint clean", "passed": True}],
            spec="spec.md",
        )
        assert v["accepted"] is True
        assert v["spec"]["ok"] is True

    def test_spec_bound_reject_when_criterion_skipped(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / "spec.md").write_text("## AC\n- [ ] tests pass\n- [ ] lint clean\n")
        # The artifact is intact, but the agent skipped 'lint clean' → REJECTED.
        v = seal_and_verify(
            "lazy",
            "feat",
            artifacts=["build.bin"],
            gates=[{"name": "tests pass", "passed": True}],
            spec="spec.md",
        )
        assert v["integrity_ok"] is True  # the file is fine...
        assert v["accepted"] is False  # ...but the spec was not satisfied
        assert "lint clean" in v["spec"]["missing"]


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
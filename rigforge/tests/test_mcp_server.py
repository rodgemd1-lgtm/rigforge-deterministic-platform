"""Tests for RIGForge MCP server."""

import pytest
from rigforge.mcp_server import (
    contract_create,
    contract_validate,
    contract_list,
    phase_status,
    proof_seal,
    git_status_tool,
    list_tools,
    list_resources,
    list_prompts,
    get_prompt,
    read_resource,
    handle_jsonrpc,
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


class TestGitStatusTool:
    def test_returns_dict(self):
        result = git_status_tool()
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        result = git_status_tool()
        for key in ("available", "in_repo", "dirty"):
            assert key in result


class TestListTools:
    def test_returns_list(self):
        tools = list_tools()
        assert isinstance(tools, list)
        assert len(tools) >= 6

    def test_all_tools_have_required_keys(self):
        for tool in list_tools():
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool

    def test_git_status_tool_present(self):
        names = [t["name"] for t in list_tools()]
        assert "gev.git_status" in names


class TestListResources:
    def test_returns_list(self):
        resources = list_resources()
        assert isinstance(resources, list)
        assert len(resources) >= 4

    def test_all_resources_have_required_keys(self):
        for resource in list_resources():
            assert "uri" in resource
            assert "name" in resource
            assert "mimeType" in resource

    def test_expected_uris_present(self):
        uris = [r["uri"] for r in list_resources()]
        assert "rigforge://phases" in uris
        assert "rigforge://contracts" in uris
        assert "rigforge://gaps" in uris
        assert "rigforge://git/status" in uris


class TestReadResource:
    def test_phases_resource(self):
        result = read_resource("rigforge://phases")
        assert "contents" in result
        assert len(result["contents"]) > 0
        assert result["contents"][0]["mimeType"] == "application/json"

    def test_contracts_resource(self):
        result = read_resource("rigforge://contracts")
        assert "contents" in result

    def test_gaps_resource(self):
        result = read_resource("rigforge://gaps")
        assert "contents" in result
        import json
        data = json.loads(result["contents"][0]["text"])
        assert "open" in data
        assert "resolved" in data

    def test_git_status_resource(self):
        result = read_resource("rigforge://git/status")
        assert "contents" in result

    def test_unknown_resource_returns_error(self):
        result = read_resource("rigforge://unknown")
        assert result["contents"] == []
        assert "error" in result


class TestListPrompts:
    def test_returns_list(self):
        prompts = list_prompts()
        assert isinstance(prompts, list)
        assert len(prompts) >= 3

    def test_all_prompts_have_required_keys(self):
        for prompt in list_prompts():
            assert "name" in prompt
            assert "description" in prompt

    def test_expected_prompts_present(self):
        names = [p["name"] for p in list_prompts()]
        assert "create_contract" in names
        assert "review_phase" in names
        assert "plan_v10" in names


class TestGetPrompt:
    def test_create_contract_prompt(self):
        result = get_prompt("create_contract", {"studio": "app", "lane": "BC-APP-V1"})
        assert "messages" in result
        assert len(result["messages"]) > 0
        text = result["messages"][0]["content"]["text"]
        assert "app" in text
        assert "BC-APP-V1" in text

    def test_review_phase_prompt(self):
        result = get_prompt("review_phase", {"phase": "3"})
        assert "messages" in result
        text = result["messages"][0]["content"]["text"]
        assert "3" in text

    def test_plan_v10_prompt(self):
        result = get_prompt("plan_v10", {"repo": "my-repo", "current_score": "3/10"})
        assert "messages" in result
        text = result["messages"][0]["content"]["text"]
        assert "my-repo" in text
        assert "3/10" in text

    def test_unknown_prompt_returns_error(self):
        result = get_prompt("nonexistent")
        assert "error" in result


class TestHandleJsonrpc:
    def test_initialize_declares_resources_and_prompts(self):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        result = handle_jsonrpc(msg)
        caps = result["result"]["capabilities"]
        assert "resources" in caps
        assert "prompts" in caps
        assert "tools" in caps

    def test_resources_list(self):
        msg = {"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}}
        result = handle_jsonrpc(msg)
        assert "resources" in result["result"]
        assert len(result["result"]["resources"]) >= 4

    def test_resources_read(self):
        msg = {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
               "params": {"uri": "rigforge://phases"}}
        result = handle_jsonrpc(msg)
        assert "contents" in result["result"]

    def test_prompts_list(self):
        msg = {"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {}}
        result = handle_jsonrpc(msg)
        assert "prompts" in result["result"]
        assert len(result["result"]["prompts"]) >= 3

    def test_prompts_get(self):
        msg = {"jsonrpc": "2.0", "id": 5, "method": "prompts/get",
               "params": {"name": "plan_v10", "arguments": {"repo": "test-repo"}}}
        result = handle_jsonrpc(msg)
        assert "messages" in result["result"]

    def test_tools_call_git_status(self):
        msg = {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
               "params": {"name": "gev.git_status", "arguments": {}}}
        result = handle_jsonrpc(msg)
        assert "content" in result["result"]
        assert "available" in result["result"]["content"]

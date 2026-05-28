"""
RIGForge MCP Server — exposes contract tools for AI coding agents.

Tools exposed:
  gev.contract_create   — Create a new DoneContract
  gev.contract_validate — Validate a DoneContract against schema
  gev.contract_list     — List all contracts
  gev.phase_status      — Get phase seal status
  gev.proof_seal        — Seal a phase with proof packet

Start with: rigforge mcp-serve
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Tool definitions ───────────────────────────────────────────────────

PROOF_DIR = Path("proofs")

PHASES = {
    1: "Bootstrap & Doctrine",
    2: "Environment Validation",
    3: "Runtime Kernel",
    4: "Control Plane Registries",
    5: "GEV Loop + DoneContract",
    6: "Archon + DeerFlow Harness",
    7: "Cockpit + Retrofit Protocol",
}


def contract_create(
    studio: str,
    lane: str,
    objective: str | None = None,
    generator: str = "PyCode",
    verifier: str = "Codex CLI",
    evaluator: str = "Human",
) -> dict[str, Any]:
    """Create a new DoneContract with GEV triad."""
    from contracts.v1 import DoneContract, VerifierPackage
    from contracts.v1.models.verifier_package import AgentRole

    agent_map = {role.value: role for role in AgentRole}

    dc = DoneContract(
        studio=studio,
        lane=lane,
        objective=objective,
        created_at=datetime.now(timezone.utc),
        verifier_package=VerifierPackage(
            generator=agent_map[generator],
            verifier=agent_map[verifier],
            evaluator=agent_map[evaluator],
        ),
    )
    return dc.to_yaml_dict()


def contract_validate(contract: dict[str, Any]) -> dict[str, Any]:
    """Validate a DoneContract dict against the Pydantic model."""
    from contracts.v1 import DoneContract

    try:
        dc = DoneContract(**contract)
        return {"valid": True, "sealed": dc.is_sealed(), "contract": dc.to_yaml_dict()}
    except Exception as e:
        return {"valid": False, "error": str(e)}


def contract_list() -> list[str]:
    """List all DoneContract YAML files."""
    contracts_dir = Path("contracts")
    if not contracts_dir.exists():
        return []
    return [str(f) for f in contracts_dir.rglob("*.yaml")]


def phase_status(phase: int | None = None) -> dict[str, Any]:
    """Get seal status for a phase or all phases."""
    if phase is not None:
        proof_file = PROOF_DIR / f"phase{phase}_proof.json"
        if proof_file.exists():
            return {"phase": phase, "sealed": True, "proof": json.loads(proof_file.read_text())}
        return {"phase": phase, "sealed": False, "proof": None}

    results = {}
    for p in PHASES:
        proof_file = PROOF_DIR / f"phase{p}_proof.json"
        if proof_file.exists():
            results[f"phase{p}"] = {"sealed": True, "name": PHASES[p]}
        else:
            results[f"phase{p}"] = {"sealed": False, "name": PHASES[p]}
    return results


def proof_seal(phase: int, artifacts: list[str] | None = None) -> dict[str, Any]:
    """Seal a phase with a proof packet."""
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    proof = {
        "phase": phase,
        "name": PHASES.get(phase, "unknown"),
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "status": "verified",
        "artifacts": artifacts or [],
    }
    proof_file = PROOF_DIR / f"phase{phase}_proof.json"
    proof_file.write_text(json.dumps(proof, indent=2))
    return proof


# ── MCP server factory ─────────────────────────────────────────────────

def create_mcp_server(host: str = "0.0.0.0", port: int = 8765, services: list[str] | None = None):
    """Create a FastAPI MCP server with contract tools.

    This is the entry-point for `rigforge mcp-serve`.
    """
    try:
        from fastapi import FastAPI
    except ImportError:
        raise ImportError("MCP dependencies not installed. Run: pip install rigforge[mcp]")

    services = services or ["recall", "stitch", "archon", "deerflow"]

    app = FastAPI(
        title="RIGForge MCP Server",
        version="1.0.0",
        description="MCP server exposing RIGForge contract tools for AI coding agents.",
    )

    @app.get("/")
    def root():
        return {"service": "rigforge-mcp", "version": "1.0.0", "services": services}

    @app.get("/health")
    def health():
        return {"status": "ok", "services": services}

    @app.post("/tools/contract_create")
    def api_contract_create(studio: str, lane: str, objective: str | None = None,
                            generator: str = "PyCode", verifier: str = "Codex CLI",
                            evaluator: str = "Human"):
        return contract_create(studio, lane, objective, generator, verifier, evaluator)

    @app.post("/tools/contract_validate")
    def api_contract_validate(contract: dict[str, Any]):
        return contract_validate(contract)

    @app.get("/tools/contract_list")
    def api_contract_list():
        return contract_list()

    @app.get("/tools/phase_status")
    def api_phase_status(phase: int | None = None):
        return phase_status(phase)

    @app.post("/tools/proof_seal")
    def api_proof_seal(phase: int, artifacts: list[str] | None = None):
        return proof_seal(phase, artifacts)

    @app.get("/mcp/tools")
    def mcp_tools_list():
        """MCP-compliant tool listing for AI agent discovery."""
        return {
            "tools": [
                {
                    "name": "gev.contract_create",
                    "description": "Create a new DoneContract with GEV triad",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "studio": {"type": "string"},
                            "lane": {"type": "string"},
                            "objective": {"type": "string"},
                            "generator": {"type": "string", "default": "PyCode"},
                            "verifier": {"type": "string", "default": "Codex CLI"},
                            "evaluator": {"type": "string", "default": "Human"},
                        },
                        "required": ["studio", "lane"],
                    },
                },
                {
                    "name": "gev.contract_validate",
                    "description": "Validate a DoneContract against schema",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "contract": {"type": "object"},
                        },
                        "required": ["contract"],
                    },
                },
                {
                    "name": "gev.contract_list",
                    "description": "List all DoneContract files",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "gev.phase_status",
                    "description": "Get phase seal status (all or single phase)",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "phase": {"type": "integer", "minimum": 1, "maximum": 7},
                        },
                    },
                },
                {
                    "name": "gev.proof_seal",
                    "description": "Seal a phase with a proof packet",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "phase": {"type": "integer", "minimum": 1, "maximum": 7},
                            "artifacts": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["phase"],
                    },
                },
            ]
        }

    return app
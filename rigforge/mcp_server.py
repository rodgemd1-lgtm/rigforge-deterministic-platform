"""
RIGForge MCP Server — exposes contract tools for AI coding agents.

Tools exposed:
  gev.contract_create   — Create a new DoneContract
  gev.contract_validate — Validate a DoneContract against schema
  gev.contract_list     — List all contracts
  gev.phase_status      — Get phase seal status
  gev.proof_seal        — Seal a phase with proof packet

Transports:
  http   — FastAPI app served by uvicorn (default; supports bearer-token auth)
  stdio  — Line-delimited JSON-RPC 2.0 over stdin/stdout (G001), for AI
           agents that prefer the MCP-canonical stdio transport.

Start with: rigforge mcp-serve [--transport stdio|http] [--auth-token TOKEN]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, IO


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


# ── Tool dispatcher (shared by HTTP + stdio transports) ─────────────────

TOOL_DISPATCH: dict[str, Callable[..., Any]] = {
    "gev.contract_create": contract_create,
    "gev.contract_validate": lambda contract: contract_validate(contract),
    "gev.contract_list": lambda: contract_list(),
    "gev.phase_status": lambda phase=None: phase_status(phase),
    "gev.proof_seal": lambda phase, artifacts=None: proof_seal(phase, artifacts),
}


def list_tools() -> list[dict[str, Any]]:
    """MCP-style tool catalogue, shared by HTTP + stdio responses."""
    return [
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
                "properties": {"contract": {"type": "object"}},
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


def _jsonrpc_response(*, id: Any, result: Any = None, error: dict | None = None) -> dict:
    out: dict[str, Any] = {"jsonrpc": "2.0", "id": id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def handle_jsonrpc(message: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one JSON-RPC 2.0 request and return the response dict.

    Supported methods:
      - ``initialize``        — MCP handshake (returns server info)
      - ``tools/list``        — return the catalogue
      - ``tools/call``        — invoke a tool by name with ``arguments``
      - ``ping``              — liveness
    """
    rpc_id = message.get("id")
    method = message.get("method", "")
    params = message.get("params") or {}

    if method == "initialize":
        return _jsonrpc_response(
            id=rpc_id,
            result={
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "rigforge", "version": "1.0.0"},
                "capabilities": {"tools": {"listChanged": False}},
            },
        )
    if method == "ping":
        return _jsonrpc_response(id=rpc_id, result={"pong": True})
    if method == "tools/list":
        return _jsonrpc_response(id=rpc_id, result={"tools": list_tools()})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = TOOL_DISPATCH.get(name)
        if fn is None:
            return _jsonrpc_response(
                id=rpc_id,
                error={"code": -32601, "message": f"unknown tool: {name}"},
            )
        try:
            result = fn(**args)
        except TypeError as exc:
            return _jsonrpc_response(
                id=rpc_id,
                error={"code": -32602, "message": f"invalid arguments: {exc}"},
            )
        except Exception as exc:  # noqa: BLE001
            return _jsonrpc_response(
                id=rpc_id, error={"code": -32000, "message": str(exc)}
            )
        return _jsonrpc_response(id=rpc_id, result={"content": result})

    return _jsonrpc_response(
        id=rpc_id, error={"code": -32601, "message": f"unknown method: {method}"}
    )


def serve_stdio(input_stream: IO[str] | None = None,
                output_stream: IO[str] | None = None) -> None:
    """Run the MCP server over line-delimited JSON-RPC on stdin/stdout (G001).

    Each line on ``input_stream`` is parsed as one JSON-RPC request. The
    response is written as a single JSON line to ``output_stream``. The loop
    exits cleanly on EOF.
    """
    inp = input_stream or sys.stdin
    out = output_stream or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _jsonrpc_response(
                id=None,
                error={"code": -32700, "message": f"parse error: {exc}"},
            )
        else:
            response = handle_jsonrpc(message)
        out.write(json.dumps(response) + "\n")
        out.flush()


# ── MCP server factory ─────────────────────────────────────────────────

def create_mcp_server(host: str = "0.0.0.0", port: int = 8765,
                      services: list[str] | None = None,
                      auth_token: str | None = None):
    """Create a FastAPI MCP server with contract tools.

    This is the entry-point for ``rigforge mcp-serve --transport http``.
    When ``auth_token`` is non-empty, every request (except ``/health``
    and ``/``) must carry a matching ``Authorization`` bearer header (G003).
    """
    try:
        from fastapi import FastAPI
    except ImportError:
        raise ImportError("MCP dependencies not installed. Run: pip install rigforge[mcp]")

    services = services or ["recall", "stitch", "archon", "deerflow"]
    auth_token = (auth_token or "").strip() or None

    app = FastAPI(
        title="RIGForge MCP Server",
        version="1.0.0",
        description="MCP server exposing RIGForge contract tools for AI coding agents.",
    )

    app.state.auth_token = auth_token

    OPEN_PATHS = {"/", "/health", "/openapi.json", "/docs", "/redoc"}

    @app.middleware("http")
    async def _auth_middleware(request, call_next):
        if auth_token is None or request.url.path in OPEN_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or token.strip() != auth_token:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"detail": "unauthorized"}, status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @app.get("/")
    def root():
        return {"service": "rigforge-mcp", "version": "1.0.0", "services": services,
                "auth_required": auth_token is not None}

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
        return {"tools": list_tools()}

    @app.post("/mcp/rpc")
    def mcp_rpc(body: dict):
        """JSON-RPC 2.0 endpoint, mirror of the stdio transport (G001)."""
        return handle_jsonrpc(body)

    return app
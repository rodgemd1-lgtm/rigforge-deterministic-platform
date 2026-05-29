"""
RIGForge MCP Server — exposes contract tools for AI coding agents.

Tools exposed:
  gev.contract_create   — Create a new DoneContract
  gev.contract_validate — Validate a DoneContract against schema
  gev.contract_list     — List all contracts
  gev.phase_status      — Get phase seal status
  gev.proof_seal        — Seal a phase with proof packet
  gev.git_status        — Read-only git repository state (G009)

Resources exposed (MCP resources/list + resources/read):
  rigforge://phases     — phase seal status JSON
  rigforge://contracts  — list of contract YAML files
  rigforge://gaps       — open platform gaps
  rigforge://git/status — current git state (credentials scrubbed)

Prompts exposed (MCP prompts/list + prompts/get):
  create_contract       — guided DoneContract authoring template
  review_phase          — phase readiness review template
  plan_v10              — V10 planning template for Looper/Copilot agents

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


# ── Git tool (G009) ────────────────────────────────────────────────────


def git_status_tool(cwd: str | None = None) -> dict[str, Any]:
    """Return a safe, read-only snapshot of the current git state (G009)."""
    from rigforge.git_agent import git_status
    return git_status(cwd=cwd).to_dict()


# ── Tool dispatcher (shared by HTTP + stdio transports) ─────────────────

TOOL_DISPATCH: dict[str, Callable[..., Any]] = {
    "gev.contract_create": contract_create,
    "gev.contract_validate": lambda contract: contract_validate(contract),
    "gev.contract_list": lambda: contract_list(),
    "gev.phase_status": lambda phase=None: phase_status(phase),
    "gev.proof_seal": lambda phase, artifacts=None: proof_seal(phase, artifacts),
    "gev.git_status": lambda cwd=None: git_status_tool(cwd),
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
        {
            "name": "gev.git_status",
            "description": "Read-only git repository state: branch, commit, dirty flag (G009)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Directory to inspect (default: current)"},
                },
            },
        },
    ]


# ── MCP Resources ──────────────────────────────────────────────────────

_RESOURCES = [
    {
        "uri": "rigforge://phases",
        "name": "Phase Seal Status",
        "description": "Current seal status for all 7 RIGForge phases",
        "mimeType": "application/json",
    },
    {
        "uri": "rigforge://contracts",
        "name": "Contract Files",
        "description": "List of all DoneContract YAML files in this project",
        "mimeType": "application/json",
    },
    {
        "uri": "rigforge://gaps",
        "name": "Platform Gaps",
        "description": "Open and resolved platform gaps tracked by RIGForge",
        "mimeType": "application/json",
    },
    {
        "uri": "rigforge://git/status",
        "name": "Git Repository State",
        "description": "Current git state (branch, commit, dirty flag). Credentials scrubbed.",
        "mimeType": "application/json",
    },
]


def list_resources() -> list[dict[str, Any]]:
    """Return the MCP resource catalogue."""
    return _RESOURCES


def read_resource(uri: str) -> dict[str, Any]:
    """Return the content of a resource by URI.

    Returns a dict with ``contents`` list per MCP spec
    (each item has ``uri``, ``mimeType``, ``text``).
    """
    import json as _json

    def _content(data: Any) -> dict[str, Any]:
        return {"uri": uri, "mimeType": "application/json", "text": _json.dumps(data, default=str)}

    if uri == "rigforge://phases":
        return {"contents": [_content(phase_status())]}
    if uri == "rigforge://contracts":
        return {"contents": [_content(contract_list())]}
    if uri == "rigforge://gaps":
        from rigforge.gaps import as_dicts, resolved_as_dicts
        return {"contents": [_content({"open": as_dicts(), "resolved": resolved_as_dicts()})]}
    if uri == "rigforge://git/status":
        return {"contents": [_content(git_status_tool())]}

    return {"contents": [], "error": f"unknown resource: {uri}"}


# ── MCP Prompts ────────────────────────────────────────────────────────

_PROMPTS = [
    {
        "name": "create_contract",
        "description": "Guided DoneContract authoring template for a new build lane",
        "arguments": [
            {"name": "studio", "description": "Studio name (e.g. strategy, app, platform)", "required": True},
            {"name": "lane", "description": "Lane ID (e.g. BC-RIG-STRATEGY-V4)", "required": True},
            {"name": "objective", "description": "One-sentence build objective", "required": False},
        ],
    },
    {
        "name": "review_phase",
        "description": "Phase readiness review template for an agent or human reviewer",
        "arguments": [
            {"name": "phase", "description": "Phase number (1-7)", "required": True},
        ],
    },
    {
        "name": "plan_v10",
        "description": "V10 planning template for Looper/Copilot agents — design from V0 toward V10",
        "arguments": [
            {"name": "repo", "description": "Repository name", "required": True},
            {"name": "current_score", "description": "Current V10 KPI score (e.g. 3/10)", "required": False},
        ],
    },
]


def list_prompts() -> list[dict[str, Any]]:
    """Return the MCP prompt catalogue."""
    return _PROMPTS


def get_prompt(name: str, arguments: dict[str, str] | None = None) -> dict[str, Any]:
    """Render a prompt template by name with optional arguments.

    Returns a dict with ``description`` and ``messages`` list per MCP spec.
    """
    args = arguments or {}

    if name == "create_contract":
        studio = args.get("studio", "<studio>")
        lane = args.get("lane", "<lane>")
        objective = args.get("objective", "<one-sentence objective>")
        return {
            "description": "Guided DoneContract authoring",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"Create a RIGForge DoneContract for studio={studio!r}, lane={lane!r}.\n"
                            f"Objective: {objective}\n\n"
                            "Requirements:\n"
                            "- Name a VerifierPackage GEV triad (generator, verifier, evaluator)\n"
                            "- List required artifacts and their gate types\n"
                            "- Define acceptance criteria with severities\n"
                            "- List any forbidden actions\n"
                            "- Set approval_required=True and approval_gate='mike'\n"
                            "Use `rigforge contract create` or gev.contract_create MCP tool."
                        ),
                    },
                }
            ],
        }

    if name == "review_phase":
        phase = args.get("phase", "<phase>")
        return {
            "description": f"Phase {phase} readiness review",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"Review RIGForge Phase {phase} for readiness.\n\n"
                            "Check:\n"
                            "1. Are all required artifacts present and hashed?\n"
                            "2. Do all gates PASS (no HARD_BLOCK failures)?\n"
                            "3. Is the ProofPacket HMAC signature valid?\n"
                            "4. Are open gaps blocking this phase?\n"
                            "5. Does the ledger show a clean run history?\n\n"
                            "Use `rigforge status`, `rigforge verify`, and `rigforge gaps` to inspect.\n"
                            "Do NOT seal unless all hard-block gates pass and human has reviewed."
                        ),
                    },
                }
            ],
        }

    if name == "plan_v10":
        repo = args.get("repo", "<repo>")
        score = args.get("current_score", "unknown")
        return {
            "description": f"V10 planning for {repo}",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"Design repo {repo!r} from its current state (V10 score: {score}) toward V10.\n\n"
                            "Produce:\n"
                            "1. V10 product promise (one paragraph)\n"
                            "2. CLI surface: first deterministic smoke command\n"
                            "3. MCP surface: tools, resources, prompts, auth boundaries\n"
                            "4. Agent roles: planner, reviewer, fixer, QA — with model routing\n"
                            "5. Weekly improvement loop: what runs automatically vs manually\n"
                            "6. Blockers: missing APIs, secrets, docs, tests\n"
                            "7. Proof paths under proof/looper-v10-cockpit/\n\n"
                            "Boundaries: no secrets, no deploy, no production features without DoneContract."
                        ),
                    },
                }
            ],
        }

    return {"description": "", "messages": [], "error": f"unknown prompt: {name}"}


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
      - ``initialize``        — MCP handshake (returns server info + capabilities)
      - ``tools/list``        — return the tool catalogue
      - ``tools/call``        — invoke a tool by name with ``arguments``
      - ``resources/list``    — return the resource catalogue
      - ``resources/read``    — read a resource by URI
      - ``prompts/list``      — return the prompt catalogue
      - ``prompts/get``       — render a prompt template by name
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
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
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
    if method == "resources/list":
        return _jsonrpc_response(id=rpc_id, result={"resources": list_resources()})
    if method == "resources/read":
        uri = params.get("uri", "")
        return _jsonrpc_response(id=rpc_id, result=read_resource(uri))
    if method == "prompts/list":
        return _jsonrpc_response(id=rpc_id, result={"prompts": list_prompts()})
    if method == "prompts/get":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        return _jsonrpc_response(id=rpc_id, result=get_prompt(name, arguments))

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

    @app.get("/tools/git_status")
    def api_git_status(cwd: str | None = None):
        return git_status_tool(cwd)

    @app.get("/mcp/tools")
    def mcp_tools_list():
        """MCP-compliant tool listing for AI agent discovery."""
        return {"tools": list_tools()}

    @app.get("/mcp/resources")
    def mcp_resources_list():
        """MCP-compliant resource listing for AI agent discovery."""
        return {"resources": list_resources()}

    @app.get("/mcp/resources/read")
    def mcp_resource_read(uri: str):
        """Read an MCP resource by URI."""
        return read_resource(uri)

    @app.get("/mcp/prompts")
    def mcp_prompts_list():
        """MCP-compliant prompt listing for AI agent discovery."""
        return {"prompts": list_prompts()}

    @app.post("/mcp/prompts/get")
    def mcp_prompt_get(body: dict):
        """Render a prompt template by name with optional arguments."""
        name = body.get("name", "")
        arguments = body.get("arguments") or {}
        return get_prompt(name, arguments)

    @app.post("/mcp/rpc")
    def mcp_rpc(body: dict):
        """JSON-RPC 2.0 endpoint, mirror of the stdio transport (G001)."""
        return handle_jsonrpc(body)

    return app
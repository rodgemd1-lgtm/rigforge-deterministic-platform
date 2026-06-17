# Extending RIGForge

RIGForge is built to be extended along three axes: **gates** (what must pass),
**contract types** (what "done" means), and **MCP tools** (what AI agents can
call). This guide walks each one step by step, with code grounded in the real
APIs in `rigforge/` and `contracts/v1/`.

Every example below uses signatures that exist in the codebase today. Where a
snippet is meant to be added to a file, the target file is named.

Prerequisites:

```bash
pip install -e ".[dev]"      # editable install + pytest
rigforge doctor              # confirm the platform is healthy first
```

---

## A. Add a custom gate

A **gate** is a side-effect-light check that returns a `GateResult`. The harness
treats a gate purely by its `severity`: `hard_block` failures block a phase,
`soft_block` and `advisory` never do.

### Step 1 — write the gate function

Gates live in `rigforge/gates.py`. The contract is: take whatever it needs
(usually the `ProjectContext`), return a `GateResult`. Keep it cheap — heavy
gates like `gate_pytest` shell out but return only summary text.

```python
# rigforge/gates.py  (add alongside the existing gates)

def gate_no_todo_markers(ctx: ProjectContext) -> GateResult:
    """Fail if any tracked Python file still contains a TODO/FIXME marker."""
    offenders: list[str] = []
    for path in (ctx.root / "rigforge").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "TODO" in text or "FIXME" in text:
            offenders.append(str(path.relative_to(ctx.root)))
    return GateResult(
        name="no_todo_markers",
        passed=not offenders,
        severity=SOFT_BLOCK,                       # warn, don't block ship
        detail="clean" if not offenders
               else f"{len(offenders)} file(s): " + ", ".join(offenders[:3]),
    )
```

Rules of a good gate, drawn from the built-ins:

- **Be honest about absence.** If a tool is missing, don't fake a pass on a
  hard block. `gate_ruff` returns `passed=True, severity=ADVISORY` when `ruff`
  is absent; `gate_pytest` returns `passed=False, severity=SOFT_BLOCK` when
  `pytest` is absent. The `detail` says exactly what happened.
- **Put the evidence in `detail`.** That string lands in the gate table, the
  ProofPacket (`GateOutcome.detail`), and the JSON output verbatim.
- **Pick severity deliberately** (see the table in `ARCHITECTURE.md` §5). Only
  `hard_block` failures stop a seal.

### Step 2 — wire it into a phase bundle

Gates only run if a phase references them. Add the gate as a **thunk**
(zero-arg callable) to the relevant phase in `gate_thunks_for_phase`. Thunks are
what let the harness run gates in parallel (G002), so return a callable, not an
already-computed `GateResult`:

```python
# rigforge/gates.py  →  gate_thunks_for_phase(ctx, phase)

    if phase == 5:
        return [
            lambda: gate_contract_schema(ctx),
            lambda: gate_pytest(ctx),
            lambda: gate_no_todo_markers(ctx),   # ← new gate joins phase 5
        ]
```

If you also want it surfaced by `rigforge doctor`, add it to the `checks` list
in `cli.doctor` (`rigforge/cli.py`) and to the import block at the top of
`cli.py`.

### Step 3 — verify it runs and blocks correctly

This is the adversarial step — prove the gate can actually go red, not just
green:

```bash
# Plant a failure
echo "x = 1  # TODO: real value" >> rigforge/_scratch.py

rigforge run 5            # expect the no_todo_markers row to show ❌ (soft)
rigforge run 5 --json | python -c \
  "import json,sys; g=[x for x in json.load(sys.stdin)['gates'] \
   if x['name']=='no_todo_markers'][0]; print(g)"

# Restore
rm rigforge/_scratch.py
rigforge run 5            # row goes green again
```

A gate whose red state you have never observed is theater. Keep a planted-failure
test:

```python
# rigforge/tests/test_gates_custom.py
from pathlib import Path
from rigforge.context import ProjectContext
from rigforge.gates import gate_no_todo_markers

def test_no_todo_markers_blocks(tmp_path: Path):
    (tmp_path / "rigforge").mkdir()
    (tmp_path / "rigforge" / "x.py").write_text("y = 1  # TODO later\n")
    result = gate_no_todo_markers(ProjectContext(root=tmp_path))
    assert result.passed is False
    assert "x.py" in result.detail

def test_no_todo_markers_clean(tmp_path: Path):
    (tmp_path / "rigforge").mkdir()
    (tmp_path / "rigforge" / "x.py").write_text("y = 1\n")
    assert gate_no_todo_markers(ProjectContext(root=tmp_path)).passed is True
```

```bash
pytest rigforge/tests/test_gates_custom.py -v
```

---

## B. Add a contract type (a new artifact / criterion / forbidden domain)

The DoneContract system (`contracts/v1/`) is Pydantic all the way down. There are
three common kinds of extension. Whichever you pick, the new value flows
automatically through `gate_contract_schema`, `rigforge contract validate`, and
the MCP `gev.contract_validate` tool, because they all call `DoneContract(**data)`.

### B.1 — Add a new artifact type

Artifact types are an enum in `contracts/v1/models/required_artifact.py`. To add,
say, an `INFRA` type:

```python
# contracts/v1/models/required_artifact.py
class ArtifactType(str, Enum):
    CODE = "code"
    DOC = "doc"
    CONFIG = "config"
    DATA = "data"
    TEST = "test"
    PROOF = "proof"
    INFRA = "infra"        # ← new: terraform, k8s manifests, etc.
```

That is the whole change for the value. `RequiredArtifact` validates `name`
against `^[a-z][a-z0-9_]*$`, defaults `gate` to `Gate.POST_BUILD`, and
`is_blocking()` returns `not optional`. A contract can now declare:

```yaml
required_artifacts:
  - name: terraform_plan
    artifact_type: infra
    gate: pre_ship
```

### B.2 — Add a new acceptance-criterion category

Categories live in `contracts/v1/models/acceptance_criterion.py`:

```python
# contracts/v1/models/acceptance_criterion.py
class CriterionCategory(str, Enum):
    STRUCTURAL = "structural"
    FUNCTIONAL = "functional"
    SECURITY = "security"
    QUALITY = "quality"
    COMPLIANCE = "compliance"
    PERFORMANCE = "performance"   # ← new: latency/throughput gates
```

`AcceptanceCriterion.is_blocking()` already keys off `severity`
(`CriterionSeverity.HARD_BLOCK`), so a `performance` criterion can be hard,
soft, or advisory independently of its category.

### B.3 — Add a new forbidden-action domain

Domains live in `contracts/v1/models/forbidden_action.py`
(`ActionDomain`: security, scope, deploy, network, data, process, memory,
approval). Add one the same way:

```python
# contracts/v1/models/forbidden_action.py
class ActionDomain(str, Enum):
    ...
    LICENSE = "license"     # ← new: no GPL deps in a permissively-licensed build
```

### B.4 — Add a whole new model field

To add a field to `DoneContract` itself, edit
`contracts/v1/models/done_contract.py`. Two things must stay in sync — the field
declaration and the `to_yaml_dict()` serializer:

```python
# contracts/v1/models/done_contract.py
class DoneContract(BaseModel):
    ...
    owner: str | None = Field(
        default=None,
        description="Engineer or studio accountable for this contract",
    )

    def to_yaml_dict(self) -> dict:
        result = {
            ...
            "approval_gate": self.approval_gate,
        }
        if self.owner is not None:
            result["owner"] = self.owner       # ← keep serializer in sync
        if self.verifier_package is not None:
            result["verifier_package"] = self.verifier_package.to_yaml_dict()
        return result
```

Note the existing invariants you must not break: `is_sealed()` requires
objective + ≥1 artifact + ≥1 criterion + ≥1 forbidden action + a verifier
package; the `approval_requires_human` model validator rejects
`approval_required=True` contracts whose verifier package has no human
(`VerifierPackage.has_human_in_chain()`); and the GEV validators forbid
self-verification and require evaluator authority ≥ generator authority.

### Step — verify the extension

```bash
# Construct + validate from Python
python - <<'PY'
from datetime import datetime, timezone
from contracts.v1 import DoneContract, VerifierPackage, RequiredArtifact, \
    AcceptanceCriterion, ForbiddenAction
from contracts.v1.models.verifier_package import AgentRole
from contracts.v1.models.required_artifact import ArtifactType, Gate
from contracts.v1.models.acceptance_criterion import CriterionCategory, CriterionSeverity
from contracts.v1.models.forbidden_action import ActionDomain

dc = DoneContract(
    studio="platform", lane="BC-RIGFORGE-DOCS",
    objective="ship buildout docs",
    created_at=datetime.now(timezone.utc),
    required_artifacts=[RequiredArtifact(
        name="architecture_md", artifact_type=ArtifactType.DOC, gate=Gate.PRE_SHIP)],
    acceptance_criteria=[AcceptanceCriterion(
        expression="docs render without dead links",
        category=CriterionCategory.QUALITY, severity=CriterionSeverity.HARD_BLOCK)],
    forbidden_actions=[ForbiddenAction(
        rule="do not edit source code", domain=ActionDomain.SCOPE)],
    verifier_package=VerifierPackage(
        generator=AgentRole.CLAUDE_CODE,
        verifier=AgentRole.CODEX,
        evaluator=AgentRole.HUMAN),
)
print("sealed:", dc.is_sealed())
print(dc.to_yaml_dict())
PY
```

Then round-trip through the CLI and the schema gate:

```bash
rigforge contract create --studio platform --lane BC-DEMO \
  --generator "Claude Code" --verifier "Codex CLI" --evaluator Human \
  --out contracts/v1/demo.yaml
rigforge contract validate contracts/v1/demo.yaml
rigforge contract inspect  contracts/v1/demo.yaml
rigforge run 4        # gate_contract_schema now validates your new YAML
```

Add a unit test next to `contracts/v1/tests/test_gev_models.py` and run
`pytest contracts/v1/tests/ -v`.

---

## C. Add an MCP tool

The MCP server (`rigforge/mcp_server.py`) exposes RIGForge to AI coding agents
over two transports — line-delimited JSON-RPC on **stdio** and **HTTP** via
FastAPI. Both transports share one dispatcher, so a tool added correctly is
reachable from both with no duplication.

A tool is added in three coordinated places: the implementation, the dispatch
table, and the catalogue.

### Step 1 — implement the tool function

A tool is a plain function that returns a JSON-serializable value. Reuse the
existing models — do not reimplement them.

```python
# rigforge/mcp_server.py  (add alongside contract_create, phase_status, ...)

def contract_inspect(contract: dict[str, Any]) -> dict[str, Any]:
    """Inspect a DoneContract dict: artifacts, criteria, GEV triad, seal state."""
    from contracts.v1 import DoneContract

    try:
        dc = DoneContract(**contract)
    except Exception as e:                 # surface validation errors to the agent
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "studio": dc.studio,
        "lane": dc.lane,
        "sealed": dc.is_sealed(),
        "required_artifacts": [a.name for a in dc.required_artifacts],
        "acceptance_criteria": len(dc.acceptance_criteria),
        "blocking_artifacts": dc.blocking_artifact_count(),
        "blocking_criteria": dc.blocking_criteria_count(),
        "forbidden_actions": len(dc.forbidden_actions),
    }
```

### Step 2 — register in the dispatch table

`TOOL_DISPATCH` maps the MCP tool name to a callable. The handler calls
`fn(**args)`, so the dispatch entry's signature must accept the JSON `arguments`
as keyword args:

```python
# rigforge/mcp_server.py
TOOL_DISPATCH: dict[str, Callable[..., Any]] = {
    "gev.contract_create":   contract_create,
    "gev.contract_validate": lambda contract: contract_validate(contract),
    "gev.contract_list":     lambda: contract_list(),
    "gev.phase_status":      lambda phase=None: phase_status(phase),
    "gev.proof_seal":        lambda phase, artifacts=None: proof_seal(phase, artifacts),
    "gev.contract_inspect":  lambda contract: contract_inspect(contract),   # ← new
}
```

This single registration is what makes the tool callable over **both** stdio and
HTTP, because `handle_jsonrpc` (used by `serve_stdio` and the `/mcp/rpc`
endpoint) dispatches through `TOOL_DISPATCH`.

### Step 3 — declare it in the catalogue

`list_tools()` is the discoverable schema agents read via `tools/list`
(JSON-RPC) or `GET /mcp/tools` (HTTP). Add an MCP-style entry with an
`inputSchema`:

```python
# rigforge/mcp_server.py  →  list_tools()
        {
            "name": "gev.contract_inspect",
            "description": "Inspect a DoneContract: artifacts, criteria, GEV triad, seal state",
            "inputSchema": {
                "type": "object",
                "properties": {"contract": {"type": "object"}},
                "required": ["contract"],
            },
        },
```

The handler already returns errors for unknown tools (`-32601`), bad argument
shapes (`-32602` on `TypeError`), and tool exceptions (`-32000`) — you do not
need to add error plumbing.

### Step 4 (optional) — add an HTTP convenience route

The JSON-RPC endpoint (`/mcp/rpc`) already exposes the tool. If you also want a
REST-style route, add it inside `create_mcp_server`, mirroring the existing
`/tools/*` handlers:

```python
# rigforge/mcp_server.py  →  inside create_mcp_server(...)
    @app.post("/tools/contract_inspect")
    def api_contract_inspect(contract: dict[str, Any]):
        return contract_inspect(contract)
```

The bearer-token auth middleware (G003) protects every path except `/`,
`/health`, and the OpenAPI/docs paths automatically — your new route inherits it.

### Step 5 — verify over both transports

**stdio** (no extra deps required — `serve_stdio` is pure stdlib):

```bash
printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"gev.contract_inspect","arguments":{"contract":{"studio":"s","lane":"l"}}}}' \
  '{"jsonrpc":"2.0","id":3,"method":"ping"}' \
  | rigforge mcp-serve --transport stdio
```

You should see `gev.contract_inspect` in the `tools/list` result and an
inspection payload (or a validation error) for the `tools/call`.

**HTTP** (needs `pip install rigforge[mcp]`):

```bash
rigforge mcp-serve --transport http --port 8765 &
curl -s localhost:8765/mcp/tools | python -m json.tool          # catalogue
curl -s -X POST localhost:8765/mcp/rpc -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"gev.contract_inspect",
                 "arguments":{"contract":{"studio":"s","lane":"l"}}}}'
```

Add a test next to `rigforge/tests/test_mcp_server.py`, exercising
`handle_jsonrpc` directly (no server needed):

```python
# rigforge/tests/test_mcp_server.py
from rigforge.mcp_server import handle_jsonrpc

def test_contract_inspect_tool():
    resp = handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "gev.contract_inspect",
                   "arguments": {"contract": {"studio": "s", "lane": "l"}}},
    })
    assert resp["id"] == 1
    assert resp["result"]["content"]["studio"] == "s"
```

```bash
pytest rigforge/tests/test_mcp_server.py -v
```

---

## D. Where to put new evidence in the ProofPacket

If your extension produces a new kind of evidence, you usually do **not** change
`ProofPacket` — you pin it as an artifact at seal time:

```bash
rigforge seal 7 \
  --artifact dist/rigforge-1.0.0-py3-none-any.whl \
  --artifact docs/ARCHITECTURE.md \
  --evidence "buildout docs + wheel sealed"
```

Each `--artifact` is hashed (SHA-256) into an `ArtifactRecord`. The packet's
self-hash and optional HMAC signature then cover that record, so anyone running
`rigforge verify` proves the artifact was exactly the bytes you sealed. Only
extend the `ProofPacket` model itself if you need a *new top-level field* on
every packet — and if you do, remember `compute_hash()` strips only
`packet_sha256`, `signature`, and `signature_algo`, so any new field is
automatically integrity-covered.

---

## Extension checklist

Before you call an extension done:

- [ ] New gate returns a `GateResult` with the right `severity` and a useful
      `detail`, and is wired into `gate_thunks_for_phase` (and `doctor` if it
      belongs there).
- [ ] You have **observed the gate go red** with a planted failure, then green
      after restore — and kept that as a test.
- [ ] New contract enum/field flows through `DoneContract(**data)` and
      `to_yaml_dict()`; `rigforge contract validate` and `rigforge run 4` pass.
- [ ] New MCP tool is in all three of: the function, `TOOL_DISPATCH`,
      `list_tools()` — and works over both stdio and HTTP.
- [ ] `pytest -v` is green across `rigforge/tests/` and `contracts/v1/tests/`.
- [ ] `rigforge doctor` reports no new hard-block failures.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full model and
[`DEPLOY.md`](./DEPLOY.md) for running the extended platform.

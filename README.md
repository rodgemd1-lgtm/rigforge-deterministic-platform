# RIGForge — Deterministic 7-Phase Agentic Engineering Platform

RIGForge is a fully deterministic, phase-gated build system with:

- **7 build phases** from bootstrap to cockpit, each sealed with integrity-hashed `ProofPacket`s
- **HMAC-SHA256 signed proof packets** (G006) — `rigforge verify --require-signature`
- **GEV (Generate-Evaluate-Verify) contract models** via `contracts/v1/`
- **Operator + agent CLI** (`rigforge init|doctor|status|run|seal|verify|contract|archon|review|resume|cockpit`) with `--json` everywhere
- **`ArchonHarness`** orchestrator with parallel multi-agent gate scheduling (G002), runtime cost/token budget enforcement (G005), and automatic resume of failed runs (G007)
- **`RunEnvelope` + `ExecutionLedger`** for deterministic, auditable runs
- **Typed `rigforge.yaml`** loader (`rigforge.config.RigForgeConfig`) wiring budgets, signing keys, MCP auth, scheduler, and cockpit settings
- **MCP server** with both HTTP (bearer-token authn, G003) and stdio JSON-RPC transports (G001) for AI coding agents (Codex, Claude Code, OpenCode)
- **Cockpit UI** (G008) — `rigforge cockpit` serves an HTML mission-control view
- **110+ tests** covering models, CLI, harness, scheduler, budgets, signing, resume, cockpit, and both MCP transports

## Phases

| Phase | Name | Description |
|-------|------|-------------|
| 1 | Bootstrap & Doctrine | Repository structure, doctrine docs, RUSR hardening |
| 2 | Environment Validation | Python, deps, config checks |
| 3 | Runtime Kernel | Core models, schemas, registries |
| 4 | Control Plane Registries | Agent catalog, build cards, intent maps |
| 5 | GEV Loop + DoneContract | Contract-based verification with proof packets |
| 6 | Archon + DeerFlow Harness | Agent orchestration and workflow engine |
| 7 | Cockpit + Retrofit Protocol | Mission-control UI and retroactive protocol |

## Quick Start — Local Installation

```bash
# Clone the repo
git clone https://github.com/rodgemd1-lgtm/rigforge-deterministic-platform.git
cd rigforge-deterministic-platform

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package (CLI + contract models)
pip install -e .

# Install MCP server dependencies (optional, for rigforge mcp-serve)
pip install -e ".[mcp]"

# Install dev dependencies (for running tests)
pip install -e ".[dev]"
```

## CLI Usage

```bash
# Scaffold a new RIGForge project (proofs/, contracts/, ledger/, docs/, rigforge.yaml)
rigforge init

# Diagnose Python version, repo layout, contracts, CI, and lint readiness
rigforge doctor

# Show phase status (add --json for machine-readable output)
rigforge status
rigforge --json status

# Run a phase's deterministic gate bundle
rigforge run 1
rigforge run 1 --dry-run         # plan only, no side effects
rigforge --json run 1            # machine-readable

# Seal a phase with a ProofPacket (artifact hashes + RunEnvelope + gate evidence)
rigforge seal 1 --artifact docs/PHASE1.md --evidence "bootstrap complete"

# Verify all sealed phases (schema + integrity hash; --strict adds phase-order check,
# --require-signature also checks the HMAC signature on each packet)
rigforge verify
rigforge verify --strict --json
rigforge verify --require-signature

# Resume the most recent failed or unfinished phase (G007)
rigforge resume

# Cockpit — Phase 7 mission-control HTML view (G008)
rigforge cockpit                # serves on 127.0.0.1:8770 by default
rigforge cockpit --print        # render the HTML to stdout (no server)

# Contract operations
rigforge contract list
rigforge contract create --studio strategy --lane BC-DEMO-V1 --out contracts/v1/demo.yaml
rigforge contract validate contracts/v1/demo.yaml
rigforge contract inspect  contracts/v1/demo.yaml

# Archon harness
rigforge archon plan 1
rigforge archon run  1
rigforge archon status

# Self-review surfaces
rigforge review        # questions + gaps + status snapshot
rigforge questions     # 20 senior-agentic-engineering questions
rigforge gaps          # tracked platform gaps

# MCP server (HTTP transport; supports --auth-token / RIGFORGE_MCP_TOKEN, G003)
rigforge mcp-serve
rigforge mcp-serve --auth-token "$RIGFORGE_MCP_TOKEN"

# MCP server (stdio JSON-RPC transport, G001 — preferred by Claude Code etc.)
rigforge mcp-serve --transport stdio

# Version
rigforge --version
```

### Global options

* `--cwd PATH` — override project-root discovery (default: walk upward from `cwd`
  looking for `rigforge.yaml`, `pyproject.toml`, or `.git/`).
* `--json` — emit machine-readable JSON where the command supports it.

## Configuration (`rigforge.yaml`)

`rigforge init` scaffolds a typed `rigforge.yaml`. The schema is defined by
`rigforge.config.RigForgeConfig` and exposes:

| Section | Keys | Purpose |
|---------|------|---------|
| `budgets` | `max_cost_usd`, `max_tokens`, `max_runtime_minutes` | Runtime ceilings enforced by `ArchonHarness.charge()` (G005) |
| `mcp` | `host`, `port`, `transport` (`http`\|`stdio`), `services`, `token`, `token_file` | MCP server transport + bearer-token auth (G001, G003) |
| `scheduler` | `max_parallel_gates`, `agents` | Multi-agent gate scheduling (G002) |
| `signing` | `enabled`, `key_file`, `require_on_verify` | HMAC-SHA256 ProofPacket signing (G006) |
| `cockpit` | `host`, `port` | Phase 7 cockpit UI (G008) |

Environment-variable overrides: `RIGFORGE_SIGNING_KEY` /
`RIGFORGE_SIGNING_KEY_FILE`, `RIGFORGE_MCP_TOKEN` /
`RIGFORGE_MCP_TOKEN_FILE`, `RIGFORGE_MAX_PARALLEL_GATES`.

`rigforge doctor` validates the file (`config_valid` gate).

## MCP Server — Use in Codex, Claude Code, OpenCode

Start the MCP server to expose RIGForge contract tools to AI coding agents:

```bash
# Start MCP server with all services
rigforge mcp-serve

# Start with specific services
rigforge mcp-serve --services recall,stitch

# Custom host/port
rigforge mcp-serve --host 127.0.0.1 --port 9000
```

### MCP Tools

| Tool | Description |
|------|-------------|
| `gev.contract_create` | Create a new DoneContract with GEV triad |
| `gev.contract_validate` | Validate a DoneContract against the schema |
| `gev.contract_list` | List all contract YAML files |
| `gev.phase_status` | Get phase seal status (all or single) |
| `gev.proof_seal` | Seal a phase with a proof packet |

### Adding to AI Agent Config

For **Codex** or **OpenCode**, add to your agent config:

```json
{
  "mcp_servers": {
    "rigforge": {
      "command": "rigforge",
      "args": ["mcp-serve", "--port", "8765"]
    }
  }
}
```

For **Claude Code**, use the MCP stdio transport:

```json
{
  "mcpServers": {
    "rigforge": {
      "command": "rigforge",
      "args": ["mcp-serve"]
    }
  }
}
```

## GEV Contract Models (contracts/v1/)

Five Pydantic models form the core contract system:

| Model | Purpose |
|-------|---------|
| `DoneContract` | Top-level build contract: objective, artifacts, gates, constraints |
| `VerifierPackage` | GEV triad: generator → verifier → evaluator |
| `RequiredArtifact` | Artifact spec with type, gate, optional flag |
| `AcceptanceCriterion` | Boolean gate with severity (hard_block/soft_block/advisory) |
| `ForbiddenAction` | Hard rule with domain (security/scope/deploy/etc.) |

### Key Invariants

- **No self-verification**: verifier must differ from generator
- **Authority ranking**: evaluator must have ≥ authority of generator
- **Human-in-chain**: `approval_required=True` contracts need at least one Human in the GEV triad
- **Sealed contracts**: must have objective, artifacts, criteria, and verifier

### Example: Creating a DoneContract

```python
from contracts.v1 import DoneContract, VerifierPackage, RequiredArtifact, AcceptanceCriterion, ForbiddenAction
from contracts.v1.models.verifier_package import AgentRole
from contracts.v1.models.required_artifact import ArtifactType, Gate
from contracts.v1.models.acceptance_criterion import CriterionCategory, CriterionSeverity
from contracts.v1.models.forbidden_action import ActionDomain

dc = DoneContract(
    studio="strategy",
    lane="BC-RIG-STRATEGY-V4",
    objective="Build RIG strategy deliverable",
    required_artifacts=[
        RequiredArtifact(name="decision_contract", artifact_type=ArtifactType.DOC),
        RequiredArtifact(name="proofpacket", artifact_type=ArtifactType.PROOF, gate=Gate.PRE_SHIP),
    ],
    acceptance_criteria=[
        AcceptanceCriterion(expression="no_source_no_number", category=CriterionCategory.COMPLIANCE),
    ],
    forbidden_actions=[
        ForbiddenAction(rule="No strategy without RIG-L score", domain=ActionDomain.APPROVAL),
    ],
    verifier_package=VerifierPackage(
        generator=AgentRole.PYCODE,
        verifier=AgentRole.CODEX,
        evaluator=AgentRole.HUMAN,
    ),
)

print(dc.to_yaml_dict())
```

## Running Tests

```bash
# Run all tests (GEV models + CLI + MCP server)
pytest

# Run only GEV model tests
pytest contracts/v1/tests/

# Run only CLI/MCP tests
pytest rigforge/tests/

# Run with verbose output
pytest -v
```

## GitHub Actions CI

A CI workflow is installed at `.github/workflows/ci.yml`. The same content is
preserved at `ci-workflow.yml` (root) as a portable template if you need to
re-install it manually (e.g. from an OAuth token without `workflow` scope):

```bash
gh api repos/OWNER/REPO/contents/.github/workflows/ci.yml \
  -X PUT -f message="ci: add CI workflow" -f content="$(base64 < ci-workflow.yml)"
```

## Project Structure

```
rigforge-deterministic-platform/
  rigforge/
    __init__.py          # Package root, version
    cli.py               # Click CLI entry-point (rigforge command)
    config.py            # Typed rigforge.yaml loader (RigForgeConfig)
    context.py           # Project-root resolver
    run_envelope.py      # RunEnvelope model (run identity + env snapshot)
    proof.py             # ProofPacket model (artifact hashes + integrity hash + HMAC signature)
    ledger.py            # ExecutionLedger (append-only JSONL audit log)
    gates.py             # Built-in quality gates + per-phase bundles
    harness.py           # ArchonHarness (plan → parallel gates → seal, budgets, resume)
    cockpit.py           # Phase 7 cockpit (HTML + FastAPI app, G008)
    questions.py         # 20 senior-agentic-engineering questions
    gaps.py              # Tracked + resolved platform gaps
    mcp_server.py        # MCP server (HTTP + stdio JSON-RPC, bearer-token auth)
    tests/
      test_cli.py         # CLI command tests
      test_mcp_server.py  # MCP server tool tests
      test_platform.py    # ProofPacket/RunEnvelope/Ledger/Harness/Gates tests
  contracts/
    v1/
      __init__.py           # Package export of 5 models
      models/               # Pydantic GEV models
      schemas/              # YAML schema references
      tests/
        test_gev_models.py  # 30 tests for all 5 models
  .github/workflows/ci.yml  # CI workflow
  ci-workflow.yml           # Portable CI template (mirror of installed workflow)
  pyproject.toml            # Build config, dependencies, CLI entry-point
  README.md                 # This file
```

## License

MIT
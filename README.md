# RIGForge — Deterministic 7-Phase Agentic Engineering Platform

RIGForge is a fully deterministic, phase-gated build system with:

- **7 build phases** from bootstrap to cockpit, each sealed with proof packets
- **GEV (Generate-Evaluate-Verify) contract models** via `contracts/v1/`
- **CLI entry-point** (`rigforge`) for status, run, seal, verify, and MCP serve
- **MCP server** exposing contract tools for AI coding agents (Codex, Claude Code, OpenCode)
- **30 tests** covering all 5 Pydantic models plus CLI and MCP server

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
# Show phase status
rigforge status

# Run a phase
rigforge run 1    # Bootstrap & Doctrine
rigforge run 5    # GEV Loop + DoneContract

# Seal a phase (creates proof packet)
rigforge seal 1

# Verify all sealed phases
rigforge verify

# List contracts
rigforge contract

# Print version
rigforge version
```

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
pytest rigforge/rigforge/tests/

# Run with verbose output
pytest -v
```

## GitHub Actions CI

A CI workflow template is included at `ci-workflow.yml`. Due to GitHub OAuth scope restrictions on workflow files, you need to manually add it:

1. Go to the repository on GitHub
2. Create `.github/workflows/ci.yml`
3. Paste the contents of `ci-workflow.yml`
4. Commit directly to `main`

Or use a personal access token with `workflow` scope:
```bash
gh api repos/OWNER/REPO/contents/.github/workflows/ci.yml \
  -X PUT -f message="ci: add CI workflow" -f content="$(base64 < ci-workflow.yml)"
```

## Project Structure

```
rigforge/
  rigforge/
    __init__.py          # Package root, version
    cli.py               # Click CLI entry-point (rigforge command)
    mcp_server.py        # MCP server (FastAPI + contract tools)
    tests/
      test_cli.py         # CLI command tests
      test_mcp_server.py  # MCP server tool tests
  contracts/
    v1/
      __init__.py           # Package export of 5 models
      models/
        done_contract.py    # Top-level build contract
        verifier_package.py # GEV triad model
        required_artifact.py
        acceptance_criterion.py
        forbidden_action.py
      schemas/
        done_contract.yaml
        verifier_package.yaml
        required_artifact.yaml
        acceptance_criterion.yaml
        forbidden_action.yaml
      tests/
        test_gev_models.py  # 30 tests for all 5 models
  pyproject.toml           # Build config, dependencies, CLI entry-point
  README.md                # This file
```

## License

MIT
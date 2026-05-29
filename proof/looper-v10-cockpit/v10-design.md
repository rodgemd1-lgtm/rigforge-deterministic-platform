# V10 Design Proof — RIGForge Deterministic Platform

**Repo**: `rodgemd1-lgtm/rigforge-deterministic-platform`  
**Phase**: Planning (V0 → V10 design)  
**Generated**: 2026-05-29  
**Status**: IN PROGRESS — planning proof only. No DoneContract sealed.

---

## V10 Product Promise

RIGForge is a deterministic 7-phase agentic engineering platform that any engineer,
agent, or CI pipeline can use to plan, run, verify, and prove software deliverables
without secrets, deployment, or self-approval.

As a V10 product it ships as:
- A **CLI** (`rigforge`) for humans and CI, with a fast `smoke` check as the entry point.
- An **MCP server** exposing tools, resources, and prompts for AI coding agents (Claude Code, Codex, OpenCode).
- An **agent substrate** with defined roles (Planner, Reviewer, Fixer, QA) and model routing.
- A **weekly self-improvement loop** that is deterministic, local, and human-gated for production actions.

---

## KPI Scorecard (baseline before V10 changes)

| Signal | Score |
|--------|-------|
| setup_git | 10/10 |
| agent_readiness | 2/10 |
| cli_readiness | 3/10 |
| mcp_readiness | 1/10 |
| quality_readiness | 0/10 |
| proof_readiness | 2/10 |
| weekly_automation_readiness | 6/10 |
| **v10_current_score** | **3/10** |

---

## Changes Implemented (this PR)

### Git Agent (`rigforge/git_agent.py`)

New read-only module:
- `git_status(cwd)` — branch, commit hash, dirty flag, remote URL (credentials scrubbed)
- `git_log(n, cwd)` — last N commit summaries
- `git_changed_files(cwd)` — files changed vs HEAD

Exposed as MCP tool `gev.git_status` and MCP resource `rigforge://git/status`.

### MCP Resources + Prompts (`rigforge/mcp_server.py`)

Added to JSON-RPC handler and FastAPI routes:
- `resources/list` → 4 resources (phases, contracts, gaps, git/status)
- `resources/read` → content delivery per URI
- `prompts/list` → 3 prompts (create_contract, review_phase, plan_v10)
- `prompts/get` → rendered prompt templates
- Updated `initialize` capabilities to declare resources + prompts

### CLI Smoke Command (`rigforge/cli.py`)

New command: `rigforge smoke`
- Runs: python_version, repo_layout, contracts_present, config_valid, mcp_catalogue, git_agent
- No network, no side effects, exits 0 on pass / 1 on hard-block failure
- Suitable for Looper/Copilot to call before any edits

### Gaps Updated (`rigforge/gaps.py`)

New open gaps:
- G010: weekly_automation — loop not yet wired to scheduler

New resolved gaps:
- G009: git_agent — read-only git introspection implemented
- G011: mcp_resources — resources + prompts added
- G012: smoke — deterministic smoke command added

### Agent Instructions (`AGENTS.md`)

New file at repo root:
- Agent roles: Planner, Reviewer, Fixer, QA
- Model routing table
- MCP surface catalogue
- Quality gates table
- No-secret boundaries
- Weekly improvement loop (auto vs manual)
- Proof paths

---

## CLI Surface Summary

| Command | Description |
|---------|-------------|
| `rigforge smoke` | **First deterministic smoke check** (V10 entry point) |
| `rigforge doctor` | Full diagnostic — all gates |
| `rigforge status` | Phase seal inventory |
| `rigforge run PHASE` | Run a phase (harness + gates) |
| `rigforge seal PHASE` | Seal a phase with proof packet |
| `rigforge verify` | Verify ProofPackets and signatures |
| `rigforge contract create/list/validate/inspect` | Contract management |
| `rigforge gaps [--show-all]` | Gap inventory |
| `rigforge review` | Questions + gaps snapshot |
| `rigforge mcp-serve` | Start MCP server (HTTP or stdio) |
| `rigforge cockpit` | Phase 7 HTML cockpit |

---

## MCP Surface Summary

**Tools** (6): `gev.contract_create`, `gev.contract_validate`, `gev.contract_list`,
`gev.phase_status`, `gev.proof_seal`, `gev.git_status`

**Resources** (4): `rigforge://phases`, `rigforge://contracts`, `rigforge://gaps`,
`rigforge://git/status`

**Prompts** (3): `create_contract`, `review_phase`, `plan_v10`

**Auth**: bearer token via `RIGFORGE_MCP_TOKEN` / `rigforge.yaml`; stdio transport is token-free.

---

## Test Commands

```bash
# Install
pip install -e ".[dev,mcp]"

# Smoke check (V10 entry point)
rigforge smoke

# Full test suite
pytest -q

# Doctor
rigforge doctor

# Gaps
rigforge gaps --show-all

# MCP catalogue (offline)
python -c "from rigforge.mcp_server import list_tools, list_resources, list_prompts; \
  print(len(list_tools()), 'tools,', len(list_resources()), 'resources,', len(list_prompts()), 'prompts')"

# Git agent
python -c "from rigforge.git_agent import git_status; import json; print(json.dumps(git_status().to_dict(), indent=2))"
```

---

## Blockers

| ID | Area | Summary | Next safe action |
|----|------|---------|-----------------|
| G004 | reproducibility | Dep lockfile not hashed | Advisory; document in RunEnvelope |
| G010 | weekly_automation | Weekly loop not wired | Add GitHub Actions schedule trigger (human approval required) |

---

## What Must Never Run Without Human Approval

- `rigforge seal PHASE` — phase sealing
- Deploying, publishing, or activating new schedules
- Any contract with `approval_required: true`
- Merge or rebase on main branch
- Any action that modifies the ledger in production

---

## Status

- [x] Git agent implemented and tested
- [x] MCP resources and prompts implemented and tested
- [x] CLI smoke command implemented and tested
- [x] Gaps updated (G009, G010, G011, G012)
- [x] Agent instructions file (AGENTS.md) created
- [x] Proof document created (this file)
- [ ] Weekly automation loop wired to scheduler (G010 — deferred, requires DoneContract)
- [ ] Dep lockfile hashing in RunEnvelope (G004 — deferred)

**RESULT**: Planning proof PASS (design phase only). Not sealed. No DoneContract required for planning.

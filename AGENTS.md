# RIGForge Agent Instructions (V10)

> **Scope**: planning, proof, and safe setup work only.  
> Production features require a sealed DoneContract with `approval_gate: mike`.

---

## V10 Product Promise

RIGForge is a deterministic 7-phase agentic engineering platform.  
As a V10 product it is usable as a **CLI tool**, **MCP server**, and **agent substrate** — locally, in CI, and as a weekly self-improving capability — without secrets, deployment, or self-approval.

---

## Agent Roles

| Role | Responsibility | Allowed actions |
|------|---------------|-----------------|
| **Planner** | Decompose issues into DoneContracts; identify blockers | Read repo, call `rigforge smoke`, `rigforge doctor`, `rigforge status` |
| **Reviewer** | Gate-check artifacts; validate ProofPackets | Read files, call `rigforge verify`, `rigforge gaps` |
| **Fixer** | Resolve hard-block gates; patch code/config | Edit files, run tests (`pytest -q`), run lint (`ruff check`) |
| **QA** | Score quality; approve or deny ship | Run full suite, call `rigforge verify --strict`, human-in-the-loop approval |

---

## Model Routing

| Role | Preferred model | Fallback |
|------|----------------|---------|
| Planner | Copilot Sonnet (reasoning) | GPT-4o |
| Reviewer | Copilot Haiku (fast, cheap) | GPT-4o-mini |
| Fixer | Copilot Sonnet | GPT-4o |
| QA | Human or Copilot Sonnet with human review | — |

Agents **must not** route to a model that requires an unavailable secret.  
Agents **must not** self-approve QA gates.

---

## First Deterministic Smoke Command

```bash
rigforge smoke
```

Runs: Python version · repo layout · contracts present · config valid · MCP catalogue · git agent.  
No network, no side effects, exits 0 on pass.  
Safe for Looper/Copilot to call before any edits.

---

## MCP Surface

| Category | Name | Description |
|----------|------|-------------|
| **Tool** | `gev.contract_create` | Create a DoneContract |
| **Tool** | `gev.contract_validate` | Validate a DoneContract |
| **Tool** | `gev.contract_list` | List contract files |
| **Tool** | `gev.phase_status` | Phase seal status |
| **Tool** | `gev.proof_seal` | Seal a phase |
| **Tool** | `gev.git_status` | Read-only git state |
| **Resource** | `rigforge://phases` | All phase seal statuses |
| **Resource** | `rigforge://contracts` | Contract file list |
| **Resource** | `rigforge://gaps` | Open + resolved gaps |
| **Resource** | `rigforge://git/status` | Git repo state |
| **Prompt** | `create_contract` | Guided contract authoring |
| **Prompt** | `review_phase` | Phase readiness review |
| **Prompt** | `plan_v10` | V10 planning template |

Auth boundary: bearer token via `RIGFORGE_MCP_TOKEN` or `rigforge.yaml`.  
Stdio transport (no token): for local Claude Code / Codex use.

---

## Quality Gates

| Gate | Severity | When it runs |
|------|----------|-------------|
| `python_version` | HARD_BLOCK | smoke, doctor, phase 1–2 |
| `repo_layout` | HARD_BLOCK | smoke, doctor, phase 1–7 |
| `contracts_present` | SOFT_BLOCK | smoke, doctor, phase 3–6 |
| `contract_schema` | SOFT_BLOCK | doctor, phase 4–5 |
| `config_valid` | SOFT_BLOCK | smoke, doctor |
| `ci_workflow` | SOFT_BLOCK | doctor, phase 2 |
| `pytest` | SOFT_BLOCK | phase 5–6 |
| `ruff` | ADVISORY | doctor |
| `mcp_catalogue` | ADVISORY | smoke |
| `git_agent` | ADVISORY | smoke |

Agents **must not** seal a phase that has unfixed HARD_BLOCK or SOFT_BLOCK failures.

---

## No-Secret Boundaries

- Do **not** print, log, or expose `RIGFORGE_SIGNING_KEY`, `RIGFORGE_MCP_TOKEN`, or any credential.
- Do **not** embed secrets in contracts, proof files, or ledger entries.
- Remote URLs returned by `gev.git_status` have credentials scrubbed automatically.
- The `.gitignore` excludes `*.key`, `*.pem`, `.env`, `secrets/`.

---

## Weekly Improvement Loop

**Runs automatically (no human approval needed):**
- `rigforge smoke` — fast health check
- `rigforge doctor` — full diagnostic
- `rigforge status` — phase inventory
- `rigforge gaps --all` — gap review
- `pytest -q` — test suite

**Requires human approval before running:**
- `rigforge seal PHASE` — sealing a phase
- `rigforge mcp-serve` — starting the MCP server in CI
- Any contract with `approval_required: true`
- Any deployment, publish, or schedule activation

---

## Proof Paths

Proof output lives under `proof/looper-v10-cockpit/`.  
Rerun weekly; compare without relying on memory or chat context.

```
proof/
  looper-v10-cockpit/
    v10-design.md          ← this V10 design proof (planning phase)
```

---

## Blockers

| ID | Area | Summary |
|----|------|---------|
| G004 | reproducibility | Dep lockfile not hashed in RunEnvelope |
| G010 | weekly_automation | Weekly loop not yet wired to a scheduler |

See `rigforge gaps` for the full list.

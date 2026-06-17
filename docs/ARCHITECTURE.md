# RIGForge Architecture

RIGForge is a deterministic, 7-phase agentic engineering platform. It turns a
build into a sequence of phases, each guarded by quality gates, and seals each
completed phase into a tamper-evident **ProofPacket**. The operating law is
simple: *no gate, no seal; no ProofPacket, it did not happen.*

This document describes the real moving parts — the 7-phase model, the harness
that drives it, the gates that guard it, the proof packets that record it, the
ledger that audits it, and the contracts that bound what "done" means. Every
class and function named here exists in `rigforge/` or `contracts/v1/`.

---

## 1. The big picture

```
                         rigforge.yaml  (typed: RigForgeConfig)
                               │
         ┌─────────────────────┼──────────────────────────────────┐
         │                     │                                    │
   ProjectContext        ArchonHarness                         contracts/v1
   (root + layout)   (plan → run → seal → verify)            (DoneContract +
         │                     │                               GEV triad)
         │            ┌────────┼─────────┐                         │
         │         gates   ExecutionLedger  ProofPacket            │
         │        (quality   (append-only    (sha256 +             │
         │         checks)    JSONL audit)    HMAC sig)            │
         │                     │                                    │
         └──────── CLI (rigforge ...) ── MCP server ── Cockpit ─────┘
                       click            (stdio/http)   (HTML view)
```

Three entry surfaces sit on top of the same core:

| Surface | Module | Purpose |
| --- | --- | --- |
| **CLI** | `rigforge/cli.py` | `rigforge` command — human + CI driver |
| **MCP server** | `rigforge/mcp_server.py` | tool surface for AI coding agents (stdio + HTTP) |
| **Cockpit** | `rigforge/cockpit.py` | read-only HTML mission-control over phases + ledger |

All three read the same `ProjectContext`, drive the same `ArchonHarness`, and
emit/consume the same `ProofPacket` files on disk. There is exactly one source
of truth per phase: `proofs/phase{N}_proof.json`.

---

## 2. The 7-phase model

The phases are declared once, in `rigforge/harness.py` as `PHASES`, and mirrored
in `rigforge/__init__.py` and `rigforge/mcp_server.py`:

| Phase | Name | What it establishes |
| --- | --- | --- |
| 1 | Bootstrap & Doctrine | Python version + repo layout are sane |
| 2 | Environment Validation | layout + CI workflow present |
| 3 | Runtime Kernel | repo layout + contracts directory exist |
| 4 | Control Plane Registries | contracts present + every contract validates against schema |
| 5 | GEV Loop + DoneContract | contract schema valid + test suite green |
| 6 | Archon + DeerFlow Harness | contracts present + test suite green |
| 7 | Cockpit + Retrofit Protocol | test suite green + CI workflow present |

Phases are **monotonic but not auto-chained**: you run and seal them one at a
time. `rigforge verify --strict` enforces *phase-order continuity* — if phases 1
and 3 are sealed but 2 is not, strict verify reports a gap (`cli.py`,
`verify`, the `phase-order gaps (strict)` error).

Each phase maps to a **gate bundle** — the deterministic set of checks that must
pass before the phase can be sealed. The bundles live in
`gates.gate_thunks_for_phase(ctx, phase)`:

```python
# rigforge/gates.py
def gate_thunks_for_phase(ctx, phase):
    if phase == 1: return [gate_python_version, lambda: gate_repo_layout(ctx)]
    if phase == 2: return [gate_python_version, lambda: gate_repo_layout(ctx),
                           lambda: gate_ci_workflow(ctx)]
    if phase == 3: return [lambda: gate_repo_layout(ctx),
                           lambda: gate_contracts_present(ctx)]
    if phase == 4: return [lambda: gate_contracts_present(ctx),
                           lambda: gate_contract_schema(ctx)]
    if phase == 5: return [lambda: gate_contract_schema(ctx),
                           lambda: gate_pytest(ctx)]
    if phase == 6: return [lambda: gate_contracts_present(ctx),
                           lambda: gate_pytest(ctx)]
    if phase == 7: return [lambda: gate_pytest(ctx),
                           lambda: gate_ci_workflow(ctx)]
    return []
```

Gates are returned as **thunks** (zero-arg callables) rather than already-run
results. This is what lets the harness schedule them concurrently (see §4)
without changing the calling contract.

---

## 3. ProjectContext — where everything lives

`rigforge/context.py` resolves "where is this project?" deterministically by
walking upward from a start directory looking for markers, first match wins:

1. `rigforge.yaml` (explicit RIGForge config)
2. `pyproject.toml` (Python project root)
3. `.git/` (repo root)

`ProjectContext.discover(start)` never raises — if no marker is found it falls
back to `start` resolved to an absolute path, so `rigforge init` works in an
empty directory. The CLI's global `--cwd` flag feeds this directly
(`cli.py`, `main`).

The context exposes the **canonical layout** as properties so nothing hard-codes
relative paths:

| Property | Path |
| --- | --- |
| `proofs_dir` | `proofs/` |
| `contracts_dir` | `contracts/` |
| `ledger_dir` / `ledger_file` | `ledger/` / `ledger/execution.jsonl` |
| `docs_dir` | `docs/` |
| `config_file` | `rigforge.yaml` |
| `workflows_dir` | `.github/workflows/` |
| `proof_file(phase)` | `proofs/phase{N}_proof.json` |

`rigforge init` creates `proofs/`, `contracts/v1/`, `ledger/`, `docs/`, and a
default `rigforge.yaml` (rendered by `config.default_config_yaml`).

---

## 4. ArchonHarness — the orchestrator

`rigforge/harness.py` holds the engine. The harness is *intentionally
synchronous and in-process*. It exposes a small, honest surface:

```
plan(phase)        → list[PlanStep]      # the gate sequence + a final "seal" step
run(phase)         → HarnessResult       # execute gates, classify blockers
seal(phase, ...)   → ProofPacket         # write the sealed proof to disk
status()           → dict[int, dict]     # per-phase sealed/integrity snapshot
find_resumable()   → dict | None         # crashed or failed run detection (G007)
resume()           → HarnessResult|None  # re-run the last failed/unfinished phase
charge(...)        → dict                 # budget accounting (G005)
```

### 4.1 The run loop

`run(phase, dry_run, verifier)`:

1. Constructs a `RunEnvelope` (see §6) capturing identity, command, environment,
   and control surface.
2. Appends a `run.start` event to the ledger.
3. If `dry_run`, finishes the envelope and returns an empty-gates `HarnessResult`
   with `ok=True` — **no state mutates**.
4. Otherwise calls `_run_gates(phase)`, computes blockers via
   `gates.all_blocking_failed`, and finishes the envelope.
5. Appends a `run.finish` event (with `ok`, blocker count, cost, tokens).

A `HarnessResult` is `ok` iff there are **no hard-block gate failures**. Soft
blocks and advisories never flip `ok` to `False`.

### 4.2 Parallel gates (G002)

`_run_gates` honours `scheduler.max_parallel_gates` from config (or the
`RIGFORGE_MAX_PARALLEL_GATES` env override, via `config.resolve_parallelism()`).
When parallelism is > 1 and there is more than one gate, gates dispatch to a
`ThreadPoolExecutor`; **result order is preserved by index** so proofs stay
deterministic regardless of completion order:

```python
results: list[GateResult | None] = [None] * len(thunks)
with ThreadPoolExecutor(max_workers=min(parallel, len(thunks))) as pool:
    futures = {pool.submit(thunk): i for i, thunk in enumerate(thunks)}
    for fut in futures:
        results[futures[fut]] = fut.result()
```

### 4.3 Budgets (G005)

`BudgetTracker` enforces `max_cost_usd` and `max_tokens` from config. `charge()`
adds cost/tokens, logs a `budget.charge` ledger event (including the
*attempted* charge on overflow), and raises `BudgetExceeded` if a ceiling would
be breached. The tracker is constructed in `ArchonHarness.__init__` from
`config.budgets`.

### 4.4 Resume (G007)

`find_resumable()` reads the full ledger and looks for either: a `run.start`
with no matching `run.finish` (a crashed run), or the latest `run.finish` for a
phase with `ok=False` (a failed run). Crashed runs win (newest first). `resume()`
logs a `run.resume` event and re-runs that phase.

---

## 5. Gates — the quality layer

`rigforge/gates.py`. A **gate** is a small, mostly side-effect-free check that
returns a structured `GateResult`:

```python
@dataclass
class GateResult:
    name: str
    passed: bool
    severity: str = HARD_BLOCK   # "hard_block" | "soft_block" | "advisory"
    detail: str = ""
```

Severity is the contract between a gate and the harness:

| Severity | Constant | Effect on `run`/`seal` |
| --- | --- | --- |
| Hard block | `HARD_BLOCK` | A failure blocks the phase. `seal` refuses without `--force`. |
| Soft block | `SOFT_BLOCK` | Recorded, never blocks `ok`. |
| Advisory | `ADVISORY` | Informational only. |

Built-in gates:

| Gate | Severity | Checks |
| --- | --- | --- |
| `gate_python_version` | hard | `sys.version_info >= 3.11` |
| `gate_repo_layout` | hard | `pyproject.toml`, `rigforge/`, `contracts/` exist |
| `gate_ci_workflow` | soft | `.github/workflows/ci.yml` exists |
| `gate_contracts_present` | soft | at least one `contracts/**/*.yaml` |
| `gate_contract_schema` | hard | every non-schema contract YAML validates against `DoneContract` |
| `gate_config_valid` | hard | `rigforge.yaml` parses via the typed loader |
| `gate_pytest` | hard | `pytest -q` exit 0 (soft if pytest missing) |
| `gate_ruff` | advisory | `ruff check` clean (passes if ruff absent) |

Two design choices worth noting:

- **`gate_contract_schema` skips templates.** YAMLs under
  `contracts/v1/schemas/` are schema *templates*, not instances, so they are
  excluded (`"schemas" in path.parts`).
- **Missing tools degrade gracefully.** `gate_ruff` passes as advisory if `ruff`
  is not installed; `gate_pytest` soft-blocks if `pytest` is missing. An honest
  SKIP is never a fake PASS — the `detail` says exactly what happened.

`doctor` (the CLI health command) runs a *superset* of gates —
`gate_python_version`, `gate_repo_layout`, `gate_ci_workflow`,
`gate_contracts_present`, `gate_contract_schema`, `gate_config_valid`,
`gate_ruff` — and exits non-zero only if a **hard-block** check fails.

---

## 6. RunEnvelope — what was actually executed

`rigforge/run_envelope.py`. Every state-mutating run constructs an immutable
`RunEnvelope` so the proof, the ledger entry, and the verifier all reference the
same description of the run. It captures the four evidence categories:

- **identity** — `run_id` (`run_<hex>`), `phase`, `started_at`, `finished_at`
- **command** — `argv` (a transcript of `sys.argv`)
- **environment** — `python_version`, `platform`, `env_fingerprint`
- **control surface** — `dry_run`, `mode` (`local`/`ci`/`agent`), `verifier`

`env_fingerprint` is a privacy-preserving hash: it never stores raw env values
(which may hold secrets). It hashes the sorted `name=len(value)` pairs of
CI/runtime-relevant variables (`CI*`, `GITHUB_*`, `RUNNER_*`, `PYTHON*`,
`VIRTUAL_ENV`). `mode` is auto-detected: `ci` when `CI` is truthy, `agent` when
`RIGFORGE_AGENT_MODE` is set, else `local`.

`finish()` returns a copy with `finished_at` set; `duration_seconds()` derives
elapsed time. The envelope is embedded into the ProofPacket on seal.

---

## 7. ProofPacket — sealed, tamper-evident evidence

`rigforge/proof.py`. A ProofPacket is the canonical, hashable record of a sealed
phase (schema version `1.1.0`). It upgrades the old `{phase, name, sealed_at,
status, artifacts}` shape with real integrity guarantees:

```
ProofPacket
├── schema_version, phase, name, status, sealed_at
├── verifier            (no anonymous seals — who sealed it)
├── evidence            (human-readable "why this phase is done")
├── artifacts: [ArtifactRecord]   (path, sha256, size_bytes, exists, kind)
├── gates:     [GateOutcome]      (name, passed, severity, detail, kind, model?)
├── run_envelope: RunEnvelope
├── packet_sha256       (self-integrity hash)
└── signature           (HMAC-SHA256 over packet_sha256, optional, G006)
```

### 7.1 Artifact pinning

`ArtifactRecord.from_path(path, base)` hashes the file with SHA-256 at seal time
and records its size. Paths are stored relative to the project root. A missing
artifact is recorded as `exists=False` with an empty hash rather than crashing —
the absence is itself evidence.

### 7.1a Determinism honesty qualifier (G012)

Every artifact and gate carries a `kind`: `deterministic` or `llm-stochastic`.
This is the platform refusing to overstate its own guarantee. RIGForge's hashes
are **byte-identical for deterministic steps; llm-stochastic steps are recorded
with model+seed for reproducibility** — not bit-for-bit. A deterministic step
(Python version, repo layout, ruff) re-runs to the same hash. A step that calls
an LLM does not: re-running it can produce a different output and therefore a
different hash even with identical inputs. So a `llm-stochastic` `GateOutcome`
pins a `ModelMetadata` (`model_id`, `version`, `temperature`, `seed`) instead of
pretending the hash is reproducible. The exported constant
`rigforge.proof.BYTE_IDENTICAL_CLAIM` carries this exact one-line qualifier so
docs and code never silently claim a bare "byte-identical".

### 7.2 Integrity & signing

The packet hash is computed over the JSON payload with `packet_sha256`,
`signature`, and `signature_algo` removed, then serialized with
`sort_keys=True` and compact separators — a **canonical** encoding so the hash is
reproducible:

```python
def compute_hash(self):
    data = self.model_dump(mode="json")
    for k in ("packet_sha256", "signature", "signature_algo"):
        data.pop(k, None)
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
```

- `verify_integrity()` recomputes the hash and compares — detects any tampering.
- `sealed(signing_key)` populates `packet_sha256` and, if a key is supplied, an
  HMAC-SHA256 `signature` over the hash.
- `verify_signature(key)` uses `hmac.compare_digest` (constant-time) to confirm
  authenticity.

`write()` always seals before writing. `load()` tolerates the **v0 legacy
shape** (no `schema_version`) by best-effort upgrading it to `schema_version
"0.0.0"`, so old seals still read.

### 7.3 The seal path

`ArchonHarness.seal(phase, verifier, evidence, artifacts, gates, envelope)`:

1. Builds `ArtifactRecord`s (hashing each path) and `GateOutcome`s.
2. Resolves a signing key from config **only if `signing.enabled`**.
3. Writes `proofs/phase{N}_proof.json`.
4. Appends a `phase.seal` ledger event (verifier, proof path, artifact/gate
   counts, `signed`).
5. Reloads and returns the packet from disk (round-trips through persistence).

The CLI `seal` command refuses to seal a phase with blocking gate failures
unless `--force` is passed; `--force` still records the failures in the packet.

---

## 8. ExecutionLedger — the audit spine

`rigforge/ledger.py`. An append-only JSONL log at `ledger/execution.jsonl`. Every
meaningful action writes one line; the format is deliberately simple so humans
can `tail` it, agents can parse it, and CI can diff it.

`append(kind, actor, **fields)` writes a record stamped with `event_id`
(`evt_<hex>`), ISO `ts`, `kind`, and `actor` (falling back to `$USER`). Fields
are JSON-serialized with `sort_keys=True`. `read(kind, limit)` returns events
newest-last, optionally filtered by kind and tail-limited.

Event kinds emitted by the platform:

| Kind | Emitted by |
| --- | --- |
| `run.start`, `run.finish`, `run.resume` | `ArchonHarness.run` / `resume` |
| `phase.seal` | `ArchonHarness.seal` |
| `budget.charge` | `ArchonHarness.charge` |

The ledger is the data source for `find_resumable()` (§4.4), the cockpit's
event table, and `rigforge archon status`.

---

## 9. Contracts — what "done" means

`contracts/v1/`. The contract system is the GEV (Generate-Evaluate-Verify) model
of a build. **Hard rule: no DoneContract → no build.** Five Pydantic models:

```
DoneContract
├── studio, lane, objective, non_goals, run_id, created_at
├── required_artifacts:  [RequiredArtifact]    (name, type, gate, optional)
├── acceptance_criteria: [AcceptanceCriterion] (expression, category, severity)
├── forbidden_actions:   [ForbiddenAction]     (rule, domain, consequence)
├── budgets: max_iterations / max_runtime_minutes / max_cost_usd
├── approval_required, approval_gate ("mike")
└── verifier_package:    VerifierPackage       (generator/verifier/evaluator)
```

### 9.1 The GEV triad

`VerifierPackage` names three `AgentRole`s and enforces separation of concerns
at validation time:

- **`generator`** produces artifacts.
- **`verifier`** checks artifacts exist and pass gates — **must differ from the
  generator** (no self-verification; enforced by a `field_validator`).
- **`evaluator`** scores quality and approves/denies ship — **must have ≥ the
  generator's authority** (authority ranking: `HUMAN(5) > CLAUDE_CODE(4) >
  HERMES/JAKE/CODEX(3) > OPENCODE(2) > PYCODE(1)`).

`AgentRole` values: `Codex CLI`, `PyCode`, `Claude Code`, `OpenCode`, `Hermes`,
`Jake`, `Human`.

### 9.2 Sealing a contract

A `DoneContract.is_sealed()` is true only when it has an objective, ≥1 required
artifact, ≥1 acceptance criterion, ≥1 forbidden action, and a verifier package.
A model validator additionally enforces: if `approval_required=True` and a
verifier package is present, a **human must be in the chain**
(`has_human_in_chain()`), or construction raises.

### 9.3 Artifacts, criteria, forbidden actions

- `RequiredArtifact` — `name` (snake_case), `artifact_type` (code/doc/config/
  data/test/proof), `gate` (pre_build/mid_build/post_build/pre_ship/always),
  `optional`. `is_blocking()` is `not optional`.
- `AcceptanceCriterion` — a single boolean assertion with `category`
  (structural/functional/security/quality/compliance) and `severity`
  (hard_block/soft_block/advisory). `is_blocking()` is true for hard blocks.
- `ForbiddenAction` — a hard rule with a `domain` (security/scope/deploy/
  network/data/process/memory/approval); violation aborts. `matches()` is a
  keyword-overlap heuristic, explicitly *not a substitute for human review*.

The `gate_contract_schema` gate (§5) loads every contract YAML and runs it
through `DoneContract(**data)` — so a malformed contract fails phases 4–6.

---

## 10. How data flows: a full phase, end to end

A representative phase-5 seal, tracing the call graph:

```
operator/CI/agent
   │  rigforge run 5
   ▼
cli.run ─► ArchonHarness.run(5)
   │           │  RunEnvelope(phase=5) constructed
   │           │  ledger.append("run.start")
   │           ▼
   │        _run_gates(5)  ──► gate_contract_schema(ctx)   [hard]
   │           │             └► gate_pytest(ctx)            [hard]
   │           │  (parallel if scheduler.max_parallel_gates > 1, order preserved)
   │           ▼
   │        all_blocking_failed(gates) → blockers
   │        envelope.finish(); ledger.append("run.finish", ok=...)
   ▼
cli.run renders gate table; exits 1 if blockers
   │
   │  rigforge seal 5 --artifact dist/x.whl --evidence "tests green"
   ▼
cli.seal ─► harness.run(5) again ─► refuse if blockers and not --force
   │     ─► harness.seal(5, verifier, evidence, artifacts, gates, envelope)
   │           │  ArtifactRecord.from_path(...)   (sha256 each)
   │           │  ProofPacket(...).write(proofs/phase5_proof.json,
   │           │                         signing_key if signing.enabled)
   │           │  ledger.append("phase.seal")
   │           ▼
   │        proofs/phase5_proof.json  (sha256 + optional HMAC signature)
   ▼
rigforge verify [--strict] [--require-signature]
   │  loads every phaseN_proof.json
   │  verify_integrity() per packet; strict → phase-order continuity
   │  --require-signature → verify_signature() with the resolved key
   ▼
exit 0 = all phases provably sealed and intact
```

The same `ProofPacket` files are what `status`, `archon status`, the cockpit,
and the MCP `gev.phase_status` tool all read. There is no hidden state — the
filesystem (`proofs/`, `ledger/`, `contracts/`, `rigforge.yaml`) **is** the
system of record.

---

## 11. Configuration

`rigforge/config.py` is the typed view of `rigforge.yaml` (schema `1.1.0`).
Sections: `budgets`, `mcp`, `scheduler`, `signing`, `cockpit`. The loader is
tolerant — a missing file resolves to defaults — but a malformed file raises
`ValueError` so `gate_config_valid` and the CLI surface a precise error.

Secrets and tunables resolve through env-var overrides first, then config:

| Resolved value | Resolution order |
| --- | --- |
| Signing key | `RIGFORGE_SIGNING_KEY` → `RIGFORGE_SIGNING_KEY_FILE` → `signing.key_file` |
| MCP bearer token | `RIGFORGE_MCP_TOKEN` → `RIGFORGE_MCP_TOKEN_FILE` → `mcp.token` → `mcp.token_file` |
| Gate parallelism | `RIGFORGE_MAX_PARALLEL_GATES` → `scheduler.max_parallel_gates` |

Keys are read from files as bytes (stripped) and never echoed back. This is the
local-first, secret-by-reference posture the platform expects.

---

## 12. Gap registry — honesty as data

`rigforge/gaps.py` encodes platform gaps as data, not prose, so the CLI itself
(`rigforge gaps --all`) can tell an operator or agent exactly what is and is not
hardened. Most original gaps are resolved (G001 stdio MCP, G002 parallel gates,
G003 MCP auth, G005 budgets, G006 signing, G007 resume, G008 cockpit). One
remains open: **G004** — the `RunEnvelope` fingerprints Python/platform but does
not yet hash a full dependency lockfile. This is the platform telling the truth
about its own reproducibility tier.

---

## See also

- [`EXTENDING.md`](./EXTENDING.md) — add a custom gate, a contract type, or an MCP tool.
- [`DEPLOY.md`](./DEPLOY.md) — run the CLI, serve MCP securely, run the cockpit, wire CI.

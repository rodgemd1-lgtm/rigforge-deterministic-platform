# Deploying & Running RIGForge

RIGForge is a local-first, deterministic platform. There is no cloud control
plane to provision — the filesystem (`proofs/`, `ledger/`, `contracts/`,
`rigforge.yaml`) is the system of record. "Deploying" means: install the CLI,
optionally serve the MCP and cockpit surfaces, and wire it into CI.

This guide covers all four, with the exact commands and the security posture for
each. Everything here maps to real code in `rigforge/cli.py`,
`rigforge/mcp_server.py`, `rigforge/cockpit.py`, and `rigforge/config.py`.

---

## 1. Install

RIGForge requires **Python ≥ 3.11** (enforced by `gate_python_version`). The
core is pure-Python with four small dependencies (`pydantic`, `click`, `pyyaml`,
`rich`). The MCP server and cockpit need the optional `mcp` extra
(`fastapi`, `uvicorn`, `mcp`).

```bash
# Core CLI only (no server surfaces)
pip install .

# CLI + MCP server + cockpit
pip install ".[mcp]"

# Development (adds pytest)
pip install -e ".[dev]"
```

The package installs a console entry point named `rigforge`
(`[project.scripts]` in `pyproject.toml`). Confirm:

```bash
rigforge --version          # rigforge, version 1.0.0
rigforge --help
```

### Scaffold a project

```bash
cd my-project
rigforge init
```

`init` creates `proofs/`, `contracts/v1/`, `ledger/`, `docs/`, and a default
`rigforge.yaml`. It is idempotent — re-running it only creates what is missing.

`ProjectContext` discovers the project root by walking up for `rigforge.yaml`,
then `pyproject.toml`, then `.git/`. Every command accepts a global `--cwd DIR`
to override discovery, and `--json` for machine-readable output.

---

## 2. Run the CLI (the core workflow)

The deterministic build loop is **plan → run → seal → verify**, one phase at a
time across the 7 phases (see `ARCHITECTURE.md` §2).

```bash
# 0. Health check — Python version, layout, contracts, CI, config, lint
rigforge doctor             # exits non-zero on a hard-block failure

# 1. See what a phase would do (no state change)
rigforge archon plan 5
rigforge run 5 --dry-run

# 2. Run a phase's gate bundle
rigforge run 5              # exits 1 if any hard-block gate fails

# 3. Seal the phase into a ProofPacket (hashes artifacts, writes proof JSON)
rigforge seal 5 \
  --artifact dist/app.whl \
  --evidence "phase 5 gates green; tests pass"

# 4. Verify every sealed phase
rigforge verify                 # schema + integrity hash per packet
rigforge verify --strict        # + phase-order continuity (no gaps)
rigforge verify --require-signature   # + HMAC signature (needs a signing key)

# Status & audit
rigforge status                 # per-phase sealed/integrity + blockers
rigforge archon status          # status + ledger tail
rigforge gaps --all             # tracked + resolved platform gaps
rigforge review                 # questions + gaps + phase status
```

Key behaviors to rely on:

- **`seal` re-runs the phase first** and *refuses* to seal if blocking gates
  fail. Override with `--force` (the failures are still recorded in the packet).
- **Verifier identity is never anonymous.** It resolves from `--verifier`, then
  `RIGFORGE_VERIFIER`, `GITHUB_ACTOR`, `USER`, finally `"unknown"`.
- **`--json` everywhere.** Pipe `rigforge run 5 --json`, `verify --json`,
  `status --json` into CI assertions or agents.

### Signing proof packets (G006)

Signing produces an HMAC-SHA256 signature over each packet hash, so a verifier
with the key can prove a seal was authentic and untampered. Enable it in config
and provide a key by reference (never inline a real key):

```yaml
# rigforge.yaml
signing:
  enabled: true
  key_file: .secrets/signing.key      # relative paths resolve against project root
```

```bash
# Resolution order: RIGFORGE_SIGNING_KEY → RIGFORGE_SIGNING_KEY_FILE → signing.key_file
export RIGFORGE_SIGNING_KEY="$(openssl rand -hex 32)"   # for a quick local run

rigforge seal 5 --evidence "signed seal"
rigforge verify --require-signature     # exits 2 if no key is configured
```

Keys are read as raw bytes (stripped) and never echoed. Keep `.secrets/` out of
git.

---

## 3. Run the MCP server securely

The MCP server exposes RIGForge's contract tools to AI coding agents. It has two
transports; pick by audience.

| Transport | Command | Use when |
| --- | --- | --- |
| **stdio** | `rigforge mcp-serve --transport stdio` | An agent spawns the server as a child process and speaks JSON-RPC over its pipes. Pure stdlib — no extra deps. |
| **HTTP** | `rigforge mcp-serve --transport http` | A networked agent or service calls it. Needs `rigforge[mcp]`. Supports bearer-token auth. |

The transport, host, port, and service list default from `rigforge.yaml`'s `mcp`
section and can be overridden per-invocation.

### stdio transport (recommended for local agents)

```bash
rigforge mcp-serve --transport stdio
```

Each stdin line is one JSON-RPC 2.0 request; each response is one JSON line on
stdout. Supported methods: `initialize`, `ping`, `tools/list`, `tools/call`.
Smoke test:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | rigforge mcp-serve --transport stdio
```

stdio is the most secure default: there is no open port. The trust boundary is
the OS process boundary — only whoever can spawn the process can call it.

### HTTP transport — lock it down

The HTTP server binds to `0.0.0.0:8765` by default. **Do not expose it
unauthenticated.** Three things to set:

**(a) Require a bearer token (G003).** When a token is configured, every request
except `/`, `/health`, and the OpenAPI/docs paths must send
`Authorization: Bearer <token>`. Resolution order:
`--auth-token` → `RIGFORGE_MCP_TOKEN` → `RIGFORGE_MCP_TOKEN_FILE` → `mcp.token`
→ `mcp.token_file`.

```bash
export RIGFORGE_MCP_TOKEN="$(openssl rand -hex 32)"
rigforge mcp-serve --transport http --host 127.0.0.1 --port 8765
# startup banner prints "Auth: bearer-token required (G003)"
```

**(b) Bind to localhost (or LAN), not the world.** Per RIG doctrine, LAN-first.
Override the default bind:

```bash
rigforge mcp-serve --transport http --host 127.0.0.1 --port 8765
```

or in config:

```yaml
# rigforge.yaml
mcp:
  host: 127.0.0.1
  port: 8765
  transport: http
  token_file: .secrets/mcp.token        # by reference, not inline
  services: [recall, stitch, archon, deerflow]
```

**(c) Put TLS in front for anything off-host.** The server speaks plain HTTP;
terminate TLS at a reverse proxy (nginx/Caddy/Traefik) and forward to
`127.0.0.1:8765`. The bearer token then rides an encrypted channel.

Verify auth works:

```bash
curl -s -o /dev/null -w '%{http_code}\n' localhost:8765/mcp/tools          # 401
curl -s localhost:8765/mcp/tools -H "Authorization: Bearer $RIGFORGE_MCP_TOKEN" \
  | python -m json.tool                                                     # 200
curl -s localhost:8765/health                                              # open, 200
```

HTTP endpoints:

| Path | Method | Purpose |
| --- | --- | --- |
| `/` | GET | service banner (open) |
| `/health` | GET | liveness (open) |
| `/mcp/tools` | GET | MCP tool catalogue |
| `/mcp/rpc` | POST | JSON-RPC 2.0 (mirror of stdio) |
| `/tools/contract_create` `_validate` `_list` | POST/GET | REST convenience routes |
| `/tools/phase_status` `/tools/proof_seal` | GET/POST | REST convenience routes |

The tools available over both transports: `gev.contract_create`,
`gev.contract_validate`, `gev.contract_list`, `gev.phase_status`,
`gev.proof_seal`.

> Security note: `gev.proof_seal` (and `/tools/proof_seal`) writes a proof file.
> Treat any HTTP server that exposes it as a privileged surface — token-gate it,
> bind it to localhost/LAN, and front it with TLS.

### Run HTTP under a process manager

For an always-on local service, supervise it (systemd shown; pm2/supervisord
work the same way). Inject the token from a file the unit can read, not the
environment of a shared shell:

```ini
# /etc/systemd/system/rigforge-mcp.service
[Unit]
Description=RIGForge MCP server
After=network.target

[Service]
WorkingDirectory=/srv/my-project
Environment=RIGFORGE_MCP_TOKEN_FILE=/srv/my-project/.secrets/mcp.token
ExecStart=/srv/my-project/.venv/bin/rigforge mcp-serve \
  --transport http --host 127.0.0.1 --port 8765
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now rigforge-mcp
```

---

## 4. Run the cockpit

The cockpit (`rigforge/cockpit.py`) is a **read-only** mission-control view over
phase status + the ledger tail. The HTML renderer is pure Python (dep-free); the
served version needs `rigforge[mcp]` (FastAPI + uvicorn).

```bash
# Print HTML to stdout — no server, works over SSH, pipe to a file or less
rigforge cockpit --print > cockpit.html
rigforge cockpit --print | less

# Serve it (default bind 127.0.0.1:8770 from cockpit config)
rigforge cockpit
rigforge cockpit --host 127.0.0.1 --port 8770
```

Served endpoints: `/` (HTML), `/api/status` (JSON: phases + ledger tail),
`/healthz`.

```yaml
# rigforge.yaml
cockpit:
  host: 127.0.0.1     # default — keep it local
  port: 8770
```

Security: the cockpit has **no auth middleware** and is read-only. Keep it bound
to `127.0.0.1` (the default) or a trusted LAN interface; if it must be reachable
remotely, put it behind an authenticating reverse proxy. The `--print` mode is
the safest distribution path — it leaks nothing but the rendered snapshot you
choose to share.

---

## 5. CI integration

The repo ships a GitHub Actions workflow at `.github/workflows/ci.yml`.
`gate_ci_workflow` checks for exactly this file (it is a soft block in phases 2
and 7). The workflow has two jobs:

**`test`** — matrix across Python 3.11 / 3.12 / 3.13:
1. `pip install -e ".[dev]"`
2. `ruff check rigforge/ contracts/` (non-blocking)
3. `pytest contracts/v1/tests/ -v` (GEV model tests)
4. `pytest rigforge/tests/ -v` (CLI tests)
5. `pytest -v` (full suite)
6. `rigforge doctor` (smoke, non-blocking)

**`build`** — needs `test`:
1. `python -m build`
2. `pip install dist/*.whl` then `rigforge --version`

### Wire RIGForge phase gates into your own CI

Beyond the shipped workflow, make a PR prove phases are sealed and intact. Use
`--json` and parse it deterministically:

```yaml
# .github/workflows/rigforge-gates.yml
name: RIGForge Gates
on: [pull_request]
permissions:
  contents: read
jobs:
  forge:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install -e ".[dev]"

      - name: Doctor (hard gate)
        run: rigforge doctor            # non-zero exit fails the job

      - name: Run phase 5 gates
        run: rigforge run 5             # non-zero exit on hard-block failure

      - name: Verify all sealed phases (strict + signed)
        if: ${{ env.RIGFORGE_SIGNING_KEY != '' }}
        run: rigforge verify --strict --require-signature
        env:
          RIGFORGE_SIGNING_KEY: ${{ secrets.RIGFORGE_SIGNING_KEY }}
```

CI-friendly facts:

- In CI, `RunEnvelope.mode` auto-detects `ci` (when `CI` is truthy) and the
  verifier resolves from `GITHUB_ACTOR` — seals are attributed to the actor with
  no extra config.
- Run gates in parallel by setting `RIGFORGE_MAX_PARALLEL_GATES` (or
  `scheduler.max_parallel_gates`); output order stays deterministic, so JSON
  assertions are stable.
- The `ledger/execution.jsonl` and `proofs/*.json` files are designed to be
  committed and diffed — a PR that seals a phase shows the new ProofPacket and
  ledger lines in the diff.

---

## 6. Resume after a failed or crashed run (G007)

If a run crashes mid-flight (a `run.start` with no `run.finish`) or the latest
run for a phase failed (`ok=False`), resume re-runs exactly that phase:

```bash
rigforge resume         # finds the last unfinished/failed phase from the ledger
```

It logs a `run.resume` event and re-executes the target phase, exiting non-zero
if blockers remain. Safe to call when there is nothing to resume (it reports so
and exits 0).

---

## 7. Operational posture summary

| Surface | Default bind | Auth | Mutates state? | Hardening |
| --- | --- | --- | --- | --- |
| CLI | n/a | local user | yes (`seal`, `init`, ledger) | filesystem perms; sign packets |
| MCP stdio | none (pipes) | process boundary | yes (`proof_seal`) | spawn-only; no open port |
| MCP HTTP | `0.0.0.0:8765` | bearer token (G003) | yes (`proof_seal`) | set token, bind localhost/LAN, front with TLS |
| Cockpit | `127.0.0.1:8770` | none (read-only) | no | keep local; reverse-proxy if remote; prefer `--print` |

Local-first, deterministic, and proof-bound. The system never claims a phase is
done without a ProofPacket on disk that `rigforge verify` can independently
re-check.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the model and
[`EXTENDING.md`](./EXTENDING.md) for adding gates, contract types, and MCP tools.

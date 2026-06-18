# Contributing to RIGForge

Thanks for considering it. RIGForge is a proof-of-integrity tool, so the bar is
simple: **every change keeps the tool honest and the suite green.**

## Setup

```bash
git clone https://github.com/rodgemd1-lgtm/rigforge-deterministic-platform.git
cd rigforge-deterministic-platform
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # add ".[dev,mcp]" to run the MCP/web tests too
pytest                       # should be all green before you start
```

## The one rule that matters here: tests must be non-vacuous

This project ships a `ProofPacket` whose whole job is to catch tampering, so a test
that can't fail is worse than no test. When you add a guarantee, **plant the failure
first**: confirm the test fails on the broken/old code, then make it pass with your
fix. (The bug that made `rigforge benchmark` hang forever shipped *because the suite
was green without ever running it* — don't recreate that.)

If you add a feature with a runnable surface (a CLI command, a benchmark, a loop),
add a test that **actually executes it**, not just imports it.

## Workflow

1. Open an issue first for anything non-trivial — especially changes to the trust
   model (see [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md)).
2. Branch, make focused commits (conventional commits: `fix:`, `feat:`, `chore:`…).
3. `pytest` green + `ruff check rigforge/ contracts/` clean.
4. Open a PR describing what you changed and how you proved it.

## Good first contributions

- New benchmark attack scenarios (more forgery classes → a stronger honesty gate).
- More OTel span attributes / exporter coverage.
- MCP tool surface for additional agents.
- Docs: sharper examples in `examples/`.

## Security

Found a way to forge a proof that `verify --require-signature` accepts? That's a real
vulnerability — please report it privately to **security@rodgersintelligence.com**
rather than opening a public issue, and we'll credit you.

MIT-licensed. By contributing you agree your work ships under the same license.

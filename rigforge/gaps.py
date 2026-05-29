"""
Known platform gaps.

``rigforge gaps`` enumerates the deliberate, tracked gaps in the current
implementation. Encoding them as data (rather than burying them in docs)
means the CLI itself can tell an operator — or an agent — exactly what is
not yet hardened.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class GapFinding:
    id: str
    area: str
    severity: str
    summary: str


GAPS: tuple[GapFinding, ...] = (
    GapFinding("G004", "reproducibility", "advisory", "RunEnvelope captures Python/platform fingerprint but does not hash full dep lockfile."),
    GapFinding("G010", "weekly_automation", "advisory", "Weekly improvement loop is not yet wired to a scheduler or GitHub Actions schedule trigger."),
)


RESOLVED_GAPS: tuple[GapFinding, ...] = (
    GapFinding("G001", "mcp", "soft", "MCP stdio transport — implemented in rigforge.mcp_server.serve_stdio (JSON-RPC 2.0)."),
    GapFinding("G002", "harness", "soft", "Multi-agent scheduling — ArchonHarness runs gates in parallel via scheduler.max_parallel_gates."),
    GapFinding("G003", "auth", "soft", "MCP HTTP authn — bearer-token middleware; token via RIGFORGE_MCP_TOKEN or rigforge.yaml."),
    GapFinding("G005", "cost", "advisory", "Runtime token/cost enforcement — BudgetTracker + ArchonHarness.charge() raises BudgetExceeded."),
    GapFinding("G006", "signing", "soft", "ProofPacket HMAC-SHA256 signing; verify via `rigforge verify --require-signature`."),
    GapFinding("G007", "resume", "soft", "ArchonHarness.find_resumable / resume + `rigforge resume` re-runs the last failed/unfinished phase."),
    GapFinding("G008", "ui", "advisory", "Cockpit UI — FastAPI HTML view served by `rigforge cockpit` (HTML renderer is dep-free)."),
    GapFinding("G009", "git_agent", "soft", "Read-only git introspection — rigforge.git_agent (git_status, git_log, git_changed_files) + gev.git_status MCP tool."),
    GapFinding("G011", "mcp_resources", "soft", "MCP resources (rigforge://phases, contracts, gaps, git/status) + prompts (create_contract, review_phase, plan_v10) added."),
    GapFinding("G012", "smoke", "soft", "Deterministic phase-0 smoke command: `rigforge smoke` — cheap, local, non-agentic, CI-ready."),
)


def as_dicts() -> list[dict]:
    return [asdict(g) for g in GAPS]


def resolved_as_dicts() -> list[dict]:
    return [asdict(g) for g in RESOLVED_GAPS]

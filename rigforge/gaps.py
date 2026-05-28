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
    GapFinding("G001", "mcp", "soft", "MCP server exposes HTTP only; full MCP stdio transport not yet implemented."),
    GapFinding("G002", "harness", "soft", "ArchonHarness is a minimal in-process orchestrator; no multi-agent scheduling yet."),
    GapFinding("G003", "auth", "soft", "MCP tools have no authn/authz; assume trusted network."),
    GapFinding("G004", "reproducibility", "advisory", "RunEnvelope captures Python/platform fingerprint but does not hash full dep lockfile."),
    GapFinding("G005", "cost", "advisory", "Token/cost budgets in DoneContract are declared but not yet enforced at runtime."),
    GapFinding("G006", "signing", "soft", "Proof packets are checksummed but not cryptographically signed."),
    GapFinding("G007", "resume", "soft", "No automatic resume of failed runs from the last ledger checkpoint."),
    GapFinding("G008", "ui", "advisory", "Cockpit UI (Phase 7) is not implemented."),
)


def as_dicts() -> list[dict]:
    return [asdict(g) for g in GAPS]

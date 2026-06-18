"""Benchmark leaderboard (Move #4 — own the metric).

The benchmark proves RIGForge catches forged "done" claims. The *leaderboard*
makes ``false-done-caught rate`` a category metric by scoring **verification
strategies** against the same forgery scenarios:

  * ``naive integrity``       — only the self-hash; fooled by a re-forged hash.
  * ``signed (RIGForge core)``— integrity + HMAC signature; catches tampering.
  * ``spec-bound (RIGForge)`` — + the spec's acceptance criteria; also catches a
                                 validly-signed claim that simply skipped a
                                 required criterion.

Every number is computed from real ``verify_*`` / ``verify_spec`` verdicts over
the same seeded, offline suite the benchmark uses — re-run it and check.
"""

from __future__ import annotations

import secrets

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from rigforge.benchmark import DEFAULT_SEED, Scenario, _artifact_bytes, _RNG, build_scenarios
from rigforge.proof import ArtifactRecord, GateOutcome, ProofPacket
from rigforge.spec import SpecBinding, verify_spec

STRATEGIES = ("naive integrity", "signed (RIGForge core)", "spec-bound (RIGForge)")


def _spec_gap_scenarios(seed: int, base: Path, n: int = 4) -> list[Scenario]:
    """Forged claims that pass signature but skip a required spec criterion.

    The artifact is real and the packet is validly signed — so integrity AND
    signature both pass. But the bound spec demands a criterion ('security
    review') that no gate satisfies. Ground truth: forged (the agent claimed
    done without doing the required work).
    """
    rng = _RNG(seed ^ 0x5EC)
    out: list[Scenario] = []
    for i in range(n):
        sid = f"sg{i:02d}"
        artifact = base / f"{sid}.bin"
        artifact.write_bytes(_artifact_bytes(rng, sid, 256 + rng.uint(512)))
        key = secrets.token_bytes(32)
        packet = ProofPacket(
            phase=1,
            name=f"build {sid}",
            verifier="ai-agent@leaderboard",
            evidence="agent reported BUILD COMPLETE",
            artifacts=[ArtifactRecord.from_path(artifact, base=base)],
            gates=[GateOutcome(name="tests pass", passed=True)],  # 'security review' is missing
            spec=SpecBinding(
                spec_id=f"spec-{sid}",
                spec_sha256="0" * 64,
                criteria=["tests pass", "security review"],
            ),
        ).sealed(signing_key=key)
        out.append(
            Scenario(
                sid=sid,
                phase=1,
                kind="spec_gap",
                is_honest=False,
                artifact_size=0,
                agent_action="signed a real artifact but skipped a required spec criterion",
                packet=packet,
                signing_key=key,
            )
        )
    return out


def _accepts(scenario: Scenario, strategy: str) -> bool:
    """Would ``strategy`` ACCEPT this claim?"""
    p = scenario.packet
    key = scenario.signing_key
    integrity = p.verify_integrity()
    if strategy == "naive integrity":
        return integrity
    signature = bool(key and p.signature and p.verify_signature(key))
    if strategy == "signed (RIGForge core)":
        return integrity and signature
    # spec-bound
    return integrity and signature and verify_spec(p).ok


def run_leaderboard(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Score each strategy's false-done-caught rate over the forgery suite."""
    with TemporaryDirectory(prefix="rigforge-lb-") as td:
        base = Path(td)
        scenarios = build_scenarios(seed, base) + _spec_gap_scenarios(seed, base)
        forged = [s for s in scenarios if not s.is_honest]
        honest = [s for s in scenarios if s.is_honest]

        rows: dict[str, dict[str, Any]] = {}
        for strat in STRATEGIES:
            caught = sum(1 for s in forged if not _accepts(s, strat))
            wrongly_blocked = sum(1 for s in honest if not _accepts(s, strat))
            rows[strat] = {
                "false_done_caught_rate": round(caught / len(forged), 4) if forged else 0.0,
                "forged_caught": caught,
                "forged_total": len(forged),
                "honest_wrongly_blocked": wrongly_blocked,
            }
        return {
            "seed": seed,
            "scenario_count": len(scenarios),
            "forged_total": len(forged),
            "strategies": rows,
        }


def render_leaderboard(result: dict[str, Any]) -> None:
    """Pretty-print the leaderboard (rich if available, else plain)."""
    rows = result["strategies"]
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        tbl = Table(title=f"RIGForge · false-done-caught leaderboard (seed {hex(result['seed'])})")
        tbl.add_column("verification strategy", style="bold")
        tbl.add_column("false-done-caught", justify="right")
        tbl.add_column("caught / total", justify="right", style="dim")
        tbl.add_column("honest blocked", justify="right", style="dim")
        for strat in STRATEGIES:
            r = rows[strat]
            pct = f"{r['false_done_caught_rate'] * 100:.0f}%"
            style = "green" if r["false_done_caught_rate"] == 1.0 else (
                "red" if r["false_done_caught_rate"] == 0.0 else "yellow"
            )
            tbl.add_row(
                strat,
                f"[{style}]{pct}[/{style}]",
                f"{r['forged_caught']}/{r['forged_total']}",
                str(r["honest_wrongly_blocked"]),
            )
        console.print(tbl)
    except ImportError:  # pragma: no cover — rich is a core dep
        for strat in STRATEGIES:
            r = rows[strat]
            print(f"{strat:28s} {r['false_done_caught_rate'] * 100:5.0f}%")


def leaderboard_markdown(result: dict[str, Any]) -> str:
    """Render the leaderboard as a public-facing Markdown table."""
    rows = result["strategies"]
    lines = [
        "| Verification strategy | False-done-caught rate | Caught / total |",
        "|---|---|---|",
    ]
    for strat in STRATEGIES:
        r = rows[strat]
        lines.append(
            f"| {strat} | **{r['false_done_caught_rate'] * 100:.0f}%** | "
            f"{r['forged_caught']}/{r['forged_total']} |"
        )
    return "\n".join(lines)

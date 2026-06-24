"""
forge() — RIG Forge execution engine.

Routes natural-language intent -> lattice cell -> troika -> artifact.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from typing import Optional

from rigforge.intents.intent_parser import IntentParser
from rigforge.intents.troika import Troika, TroikaReport
from rigforge.lattice.router import LatticeRouter


@dataclass
class ForgeResult:
    ok: bool
    cell_id: str
    bms_score: float
    archetype: str
    tools: list[str]
    troika_ok: bool
    troika_passed: int
    artifact: Optional[str] = None
    proof_hash: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def forge(
    intent: str,
    *,
    dry_run: bool = False,
    context: Optional[dict] = None,
) -> ForgeResult:
    """
    Execute a natural-language intent through the RIG lattice + troika.

    Parameters
    ----------
    intent: Natural-language task description.
    dry_run: If True, parse and route without executing.
    context: Optional context dict for the troika.

    Returns
    -------
    ForgeResult
    """
    router = LatticeRouter()
    parser = IntentParser()
    route = router.route(intent)
    parsed = parser.parse(intent)

    troika_report: Optional[TroikaReport] = None
    if not dry_run:
        troika = Troika()
        troika_report = troika.run(intent, context or {})

    return ForgeResult(
        ok=troika_report.ok if troika_report else True,
        cell_id=route.cell.cell_id,
        bms_score=route.bms_score,
        archetype=parsed.archetype(),
        tools=route.tools_enabled,
        troika_ok=troika_report.ok if troika_report else True,
        troika_passed=troika_report.passed_count() if troika_report else 0,
    )


def main() -> None:
    """CLI: python -m rigforge.forge <intent text>"""
    intent = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "forge status"
    result = forge(intent, dry_run=True)
    print(json.dumps(result.as_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()

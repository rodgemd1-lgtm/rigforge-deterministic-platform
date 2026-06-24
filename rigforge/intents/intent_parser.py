"""
Intent parser: NL text \u2192 structured intent + BMS score + lattice cell.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from rigforge.triple_diamond import Cell, mode_from_bms


@dataclass
class Intent:
    """Parsed natural-language intent."""
    raw: str = ""
    action: str = "unknown"
    domain: str = ""
    target: str = ""
    constraints: dict = field(default_factory=dict)
    bms_score: float = 0.20
    cell: Optional[Cell] = None

    def archetype(self) -> str:
        m = mode_from_bms(self.bms_score)
        return f"{m.value}_archetype"


_ACTION_PATTERNS = [
    (re.compile(r"\bbuild\b", re.I), "build"),
    (re.compile(r"\bdeploy\b", re.I), "deploy"),
    (re.compile(r"\bscaffold\b", re.I), "scaffold"),
    (re.compile(r"\bresearch\b", re.I), "research"),
    (re.compile(r"\bverify\b", re.I), "verify"),
    (re.compile(r"\btest\b", re.I), "test"),
    (re.compile(r"\bfix\b", re.I), "fix"),
    (re.compile(r"\broute\b", re.I), "route"),
    (re.compile(r"\bswarm\b", re.I), "swarm"),
    (re.compile(r"\bdeviate\b", re.I), "deviate"),
    (re.compile(r"\bseal\b", re.I), "seal"),
    (re.compile(r"\brun\b", re.I), "run"),
    (re.compile(r"\binspect\b", re.I), "inspect"),
]


_DOMAIN_KEYWORDS = [
    "construction", "healthcare", "dental", "law", "cpa",
    "manufacturing", "pe_cfo", "services", "general",
    "support", "sales", "revenue", "product", "engineering",
]


class IntentParser:
    """Parse NL text \u2192 structured Intent + BMS score."""

    def __init__(self) -> None:
        self._cache: dict[str, Intent] = {}

    def parse(self, nl_text: str) -> Intent:
        """Parse natural-language text into a structured Intent."""
        cached = self._cache.get(nl_text)
        if cached:
            return cached

        intent = Intent(raw=nl_text)
        intent.action = self._extract_action(nl_text)
        intent.domain = self._extract_domain(nl_text)
        intent.target = self._extract_target(nl_text)
        intent.bms_score = self._score_bms(intent)
        intent.cell = self._route_cell(intent)

        self._cache[nl_text] = intent
        return intent

    def _extract_action(self, text: str) -> str:
        for pattern, action in _ACTION_PATTERNS:
            if pattern.search(text):
                return action
        return "unknown"

    def _extract_domain(self, text: str) -> str:
        lowered = text.lower()
        for domain in _DOMAIN_KEYWORDS:
            if domain in lowered:
                return domain
        return ""

    def _extract_target(self, text: str) -> str:
        import shlex
        try:
            tokens = shlex.split(text)
            if len(tokens) > 1:
                return tokens[-1]
        except Exception:
            pass
        return ""

    def _score_bms(self, intent: Intent) -> float:
        score = 0.20
        if intent.action in ("build", "deploy", "scaffold"):
            score = 0.35
        elif intent.action in ("research", "deviate", "route"):
            score = 0.15
        elif intent.action in ("verify", "test"):
            score = 0.65
        elif intent.action in ("fix", "inspect"):
            score = 0.55
        if intent.domain:
            score = min(score + 0.05, 1.0)
        return score

    def _route_cell(self, intent: Intent) -> Cell:
        from rigforge.lattice.router import LatticeRouter
        router = LatticeRouter()
        result = router.route(intent.raw)
        return result.cell

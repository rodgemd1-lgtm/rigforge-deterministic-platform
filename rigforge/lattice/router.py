"""
Lattice router: intent → cell mapping.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from rigforge.triple_diamond import _IntentHint


from rigforge.triple_diamond import (
    Cell,
    Diamond,
    Level,
    Mode,
    Step,
    LATTICE,
    CHIEF_BLOCK,
)


@dataclass
class RouteResult:
    """Router output."""
    cell: Cell
    bms_score: float
    archetype_hint: str
    tools_enabled: list[str]
    mcp_hint: Optional[str] = None
    evidence_required: bool = True
    confidence: str = "high"


# ── Intent keywords → (level, diamond, mode, bms_score) ─────────────────


_KEYWORD_MAP: dict[str, tuple[Level, Diamond, Mode, float]] = {
    # ── A1: Deterministic Python ────────────────────────────────────────
    "database":          (Level.L1, Diamond.D1, Mode.A1, 0.85),
    "postgres":          (Level.L1, Diamond.D1, Mode.A1, 0.85),
    "sql":              (Level.L1, Diamond.D1, Mode.A1, 0.90),
    "sqlite":           (Level.L1, Diamond.D1, Mode.A1, 0.90),
    "twenty crm":       (Level.L1, Diamond.D1, Mode.A1, 0.75),
    "rig-crm":          (Level.L1, Diamond.D1, Mode.A1, 0.75),
    "doctor":           (Level.L2, Diamond.D1, Mode.A1, 0.80),
    "sentinel":         (Level.L3, Diamond.D1, Mode.A1, 0.80),
    "cursor":           (Level.L1, Diamond.D1, Mode.A1, 0.90),
    "ollama":           (Level.L1, Diamond.D1, Mode.A1, 0.80),
    # ── A2: Hybrid / structured ──────────────────────────────────────────
    "api":              (Level.L2, Diamond.D1, Mode.A2, 0.65),
    "rest":             (Level.L2, Diamond.D1, Mode.A2, 0.65),
    "mqtt":             (Level.L2, Diamond.D1, Mode.A2, 0.70),
    "mq":               (Level.L2, Diamond.D1, Mode.A2, 0.70),
    "proof":            (Level.L2, Diamond.D2, Mode.A2, 0.65),
    "gate":             (Level.L2, Diamond.D2, Mode.A2, 0.70),
    "verify":           (Level.L2, Diamond.D1, Mode.A2, 0.65),
    "html":             (Level.L2, Diamond.D1, Mode.A2, 0.65),
    "write doc":        (Level.L2, Diamond.D2, Mode.A2, 0.60),
    "markdown":         (Level.L2, Diamond.D1, Mode.A1, 0.80),
    "robust":           (Level.L4, Diamond.D1, Mode.A2, 0.55),
    # ── A3: Bounded agent ──────────────────────────────────────────────
    "build app":        (Level.L3, Diamond.D1, Mode.A3, 0.40),
    "build executable": (Level.L3, Diamond.D1, Mode.A3, 0.35),
    "tauri":            (Level.L3, Diamond.D1, Mode.A3, 0.30),
    "deploy":           (Level.L3, Diamond.D1, Mode.A3, 0.25),
    "vertical scaffold": (Level.L3, Diamond.D2, Mode.A3, 0.35),
    "new domain":       (Level.L3, Diamond.D2, Mode.A3, 0.35),
    "domain new":       (Level.L3, Diamond.D2, Mode.A3, 0.35),
    "troika":           (Level.L3, Diamond.D2, Mode.A3, 0.35),
    "goal":             (Level.L3, Diamond.D2, Mode.A3, 0.35),
    "iqrsqpi":          (Level.L3, Diamond.D2, Mode.A3, 0.35),
    "compose":          (Level.L3, Diamond.D1, Mode.A3, 0.40),
    "code":             (Level.L3, Diamond.D1, Mode.A3, 0.40),
    "benchmark":        (Level.L3, Diamond.D1, Mode.A3, 0.40),
    "archetype":        (Level.L4, Diamond.D2, Mode.A3, 0.30),
    "stress test":      (Level.L4, Diamond.D1, Mode.A3, 0.30),
    "vibe code":        (Level.L3, Diamond.D1, Mode.A3, 0.35),
    "route intent":     (Level.L3, Diamond.D2, Mode.A3, 0.40),
    # ── A4: Strategic / novel ──────────────────────────────────────────
    "rig forge":        (Level.L3, Diamond.D2, Mode.A4, 0.20),
    "forge":            (Level.L3, Diamond.D2, Mode.A4, 0.20),
    "lattice":          (Level.L4, Diamond.D3, Mode.A4, 0.25),
    "triple diamond":   (Level.L4, Diamond.D3, Mode.A4, 0.20),
    "research":         (Level.L4, Diamond.D2, Mode.A4, 0.15),
    "swarm":            (Level.L5, Diamond.D2, Mode.A4, 0.15),
    "deviate":          (Level.L4, Diamond.D2, Mode.A4, 0.20),
    "28 principle":     (Level.L6, Diamond.D3, Mode.A4, 0.15),
    "first principle":   (Level.L6, Diamond.D3, Mode.A4, 0.20),
    "antifragile":      (Level.L5, Diamond.D3, Mode.A4, 0.20),
    "thermodynamic":    (Level.L5, Diamond.D3, Mode.A4, 0.20),
    "deep dive":        (Level.L5, Diamond.D2, Mode.A4, 0.10),
    "x-ray":           (Level.L4, Diamond.D2, Mode.A4, 0.20),
    "28":              (Level.L6, Diamond.D3, Mode.A4, 0.15),
    # ── Agents ────────────────────────────────────────────────────────────
    "codex":            (Level.L1, Diamond.D1, Mode.A1, 0.85),
    "claude":           (Level.L1, Diamond.D1, Mode.A1, 0.85),
    "hermes":           (Level.L1, Diamond.D1, Mode.A1, 0.85),
}


_ARCHETYPE_TOOLS: dict[str, list[str]] = {
    "A1": ["terminal", "file"],
    "A2": ["terminal", "file", "web", "search"],
    "A3": ["terminal", "file", "browser", "web", "search", "delegation"],
    "A4": ["terminal", "file", "web", "search", "delegation", "cronjob", "code"],
}


_MCP_HINTS: dict[str, Optional[str]] = {
    "A1": "codex",
    "A2": "github_mcp",
    "A3": "supabase_mcp",
    "A4": None,
}


def _confidence(bms: float) -> str:
    if bms >= 0.75:
        return "high"
    elif bms >= 0.45:
        return "medium"
    else:
        return "low"


def _resolve_hint(intent: str) -> tuple[Level, Diamond, Mode, float]:
    """Best-match keyword → (L, D, M, score). Falls back to A4 strategic."""
    lowered = intent.lower()
    best: tuple[str, int] = ("", -1)
    for kw, hint in _KEYWORD_MAP.items():
        if kw in lowered and len(kw) > best[1]:
            best = (kw, len(kw))
    if best[1] > 0:
        return _KEYWORD_MAP[best[0]]
    return (Level.L3, Diamond.D2, Mode.A4, 0.20)


# ── Main router ────────────────────────────────────────────────────────────


class LatticeRouter:
    """Route natural-language intent to a RIG lattice cell."""

    def __init__(self) -> None:
        self._cache: dict[str, RouteResult] = {}

    def route(
        self,
        intent: str,
        *,
        step: Step = Step.I1,
        domain: str = "",
    ) -> RouteResult:
        """
        Route ``intent`` text to a RIG lattice cell.

        Parameters
        ----------
        intent:
            Natural-language description of what the user wants to do.
        step:
            IQRSQPI loop step (default I1 = Intent).
        domain:
            Optional domain label (e.g. "construction", "healthcare").

        Returns
        -------
        RouteResult
            cell, bms_score, archetype_hint, tools_enabled, mcp_hint
        """
        cached = self._cache.get(intent)
        if cached:
            return cached

        level, diamond, mode, bms_score = _resolve_hint(intent)
        archetype_hint = f"{mode.value}_archetype"
        tools_enabled = _ARCHETYPE_TOOLS.get(mode.value, [])
        mcp_hint = _MCP_HINTS.get(mode.value)

        cell = Cell(level=level, diamond=diamond, mode=mode, step=step)
        if cell.cell_id not in LATTICE:
            cell = Cell(level=Level.L3, diamond=Diamond.D2, mode=Mode.A4, step=step)

        result = RouteResult(
            cell=cell,
            bms_score=bms_score,
            archetype_hint=archetype_hint,
            tools_enabled=tools_enabled,
            mcp_hint=mcp_hint,
            evidence_required=bms_score < 0.75,
            confidence=_confidence(bms_score),
        )
        self._cache[intent] = result
        return result

    def chief_block(self) -> set[str]:
        """Return the ~10 core cells for the mission."""
        return CHIEF_BLOCK.copy()

    def cell_is_strategic(self, cell_id: str) -> bool:
        """True if cell is in the chief block."""
        return cell_id in CHIEF_BLOCK

    def tools_for_cell(self, cell: Cell) -> list[str]:
        """Return the tool allow-list for a given cell."""
        return _ARCHETYPE_TOOLS.get(cell.mode.value, [])
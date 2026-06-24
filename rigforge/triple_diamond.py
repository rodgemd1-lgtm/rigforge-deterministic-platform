"""
RIG Triple Diamond — Core 588-cell lattice model.

3 dimensions × 7 levels × 3 diamonds × 4 modes = 588 cells
cell_id: L{level}-D{diamond}-{mode}-{step}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated

# ── Enumerations ───────────────────────────────────────────────────────


class Level(str, Enum):
    """L1–L7 intention levels (X-axis)."""
    L1 = "L1"  # Deterministic facts
    L2 = "L2"  # Structured operations
    L3 = "L3"  # Bounded agent
    L4 = "L4"  # Strategic planning
    L5 = "L5"  # Domain knowledge
    L6 = "L6"  # Meta-cognition
    L7 = "L7"  # Self-modifying


class Diamond(str, Enum):
    """Triple Diamond (Y-axis)."""
    D1 = "D1"  # Physical — code, infra, data
    D2 = "D2"  # Cognitive — reasoning, strategy, planning
    D3 = "D3"  # Nature — n-body, emergence, touch


class Mode(str, Enum):
    """BMS build mode (Z-axis)."""
    A1 = "A1"  # Python-only, >= 0.75 determinism
    A2 = "A2"  # Hybrid LLM + typed schema, 0.45–0.74
    A3 = "A3"  # Bounded agent, tool allowlist, 0.25–0.44
    A4 = "A4"  # LLM-free / adversarial, <0.25


# ── Step (IQRSQPI sub-cell, Z-sub-axis) ────────────────────────────────


class Step(str, Enum):
    """IQRSQPI loop steps."""
    I1 = "I1"  # Intent
    Q1 = "Q1"  # Question
    R  = "R"   # Research
    S  = "S"   # Solution
    Q2 = "Q2"  # Quality
    P  = "P"   # Proof
    I2 = "I2"  # Integrate


# ── Dataclasses ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Cell:
    """One cell in the RIG 3D lattice."""
    level: Level
    diamond: Diamond
    mode: Mode
    # IQRSQPI default step
    step: Step = Step.I1

    @property
    def cell_id(self) -> str:
        return f"{self.level.value}-{self.diamond.value}-{self.mode.value}-{self.step.value}"

    def __str__(self) -> str:
        return self.cell_id

    def with_step(self, step: Step) -> Cell:
        return Cell(level=self.level, diamond=self.diamond, mode=self.mode, step=step)


@dataclass
class LatticeConfig:
    """Per-cell configuration."""
    tools: list[str] = field(default_factory=list)
    model_preference: str = "blackwell-minimax"
    gate_threshold: float = 0.8
    requires_proof: bool = True
    human_override: bool = False
    poles: dict[str, str] = field(default_factory=dict)


# ── Cartesian product: all 588 cells ───────────────────────────────────


def _build_lattice() -> dict[str, Cell]:
    cells: dict[str, Cell] = {}
    for lv in Level:
        for di in Diamond:
            for mo in Mode:
                # Each (L,D,M) has 7 IQRSQPI steps
                for st in Step:
                    c = Cell(level=lv, diamond=di, mode=mo, step=st)
                    cells[c.cell_id] = c
    return cells


LATTICE: dict[str, Cell] = _build_lattice()


def cell(identifier: str) -> Cell:
    """Parse a cell_id string into a Cell."""
    return LATTICE[identifier]


def parse_cell_id(cid: str) -> Cell | None:
    """Safe parse — returns None on bad format."""
    return LATTICE.get(cid)


# ── BMS score → Mode mapping ─────────────────────────────────────────────


def mode_from_bms(score: float) -> Mode:
    """Map a determinism/BMS score [0,1] to BMS Mode."""
    if score >= 0.75:
        return Mode.A1
    elif score >= 0.45:
        return Mode.A2
    elif score >= 0.25:
        return Mode.A3
    else:
        return Mode.A4


# ── Block coordinate (the ~10 most-used cells) ────────────────────────


CHIEF_BLOCK: set[str] = {
    "L3-D2-A4-I1",  # Strategic planning (current mission)
    "L4-D1-A3-Q2",  # Physics deviation audit
    "L3-D2-A3-S",   # Solution generation
    "L1-D1-A1-R",   # Research
    "L2-D1-A2-P",   # Proof sealing
}


# ── Function shortcuts ───────────────────────────────────────────────────


def i1(level: Level, diamond: Diamond, mode: Mode) -> Cell:
    """L{n}-D{n}-A{n}-I1  (canonical intent cell)."""
    return Cell(level=level, diamond=diamond, mode=mode, step=Step.I1)


def l3d2a4() -> Cell:
    """L3-D2-A4-I1 — the current mission cell."""
    return Cell(level=Level.L3, diamond=Diamond.D2, mode=Mode.A4, step=Step.I1)
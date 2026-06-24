"""
RIG Lattice — lattice router, archetypes, and 3-D cell mesh.
"""
from __future__ import annotations

from rigforge.triple_diamond import (
    CHIEF_BLOCK,
    Cell,
    Diamond,
    Level,
    LatticeConfig,
    Mode,
    Step,
    LATTICE,
    parse_cell_id,
    i1,
    l3d2a4,
)
from rigforge.lattice.router import RouteResult, LatticeRouter

__all__ = [
    "CHIEF_BLOCK",
    "Cell",
    "Diamond",
    "Level",
    "LatticeConfig",
    "LatticeRouter",
    "LATTICE",
    "Mode",
    "parse_cell_id",
    "RouteResult",
    "Step",
    "i1",
    "l3d2a4",
]
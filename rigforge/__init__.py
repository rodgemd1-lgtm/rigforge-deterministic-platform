"""
RIGForge — Deterministic 7-Phase Agentic Engineering Platform.

Phases:
  1. Bootstrap & Doctrine
  2. Environment Validation
  3. Runtime Kernel
  4. Control Plane Registries
  5. GEV Loop + DoneContract
  6. Archon + DeerFlow Harness
  7. Cockpit + Retrofit Protocol

CLI:
  rigforge status   — show current phase, sealed status
  rigforge run N    — run phase N (1-7)
  rigforge seal N   — seal phase N with proof packet
  rigforge verify   — verify all sealed phases
  rigforge mcp-serve — boot MCP servers (Recall, Stitch, Archon, DeerFlow)

MCP:
  rigforge mcp-serve — starts an MCP server exposing contract tools
"""

__version__ = "1.0.0"
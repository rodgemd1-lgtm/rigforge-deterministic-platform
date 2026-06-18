"""The shipped examples must actually run (and prove what they claim)."""

from __future__ import annotations

import importlib.util

from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _load(name: str):
    path = EXAMPLES / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_verify_agent_done_example_runs():
    assert _load("verify_agent_done.py").main() == 0


def test_swarm_demo_catches_exactly_the_liars():
    # main() returns 0 only when the rejected set == the planted liars (its own
    # receipts self-check). If detection regresses, this fails loudly.
    assert _load("swarm_demo.py").main() == 0

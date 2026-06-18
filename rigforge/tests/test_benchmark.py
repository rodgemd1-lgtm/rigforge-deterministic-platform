"""Non-vacuous tests for the honesty-gate benchmark.

These exist because the benchmark shipped with an infinite loop in the seeded
RNG (`_refill` checked `self._buf` length while appending to a local list, so
the buffer never grew → `uint()`/`bytes()` spun forever). The full suite stayed
green because *nothing ran the benchmark end-to-end*. These tests close that
gap: they execute the real benchmark and assert the invariants, so a regression
to the hang (or to faked numbers) fails loudly instead of hiding.
"""

from __future__ import annotations

import pytest

from rigforge.benchmark import DEFAULT_SEED, _RNG, run_benchmark


# ── The unit that actually broke ────────────────────────────────────────────


def test_rng_bytes_returns_exact_length():
    """The bug: `_refill` never grew `self._buf`, so this hung forever.

    Pre-fix this call does not return. Post-fix it returns exactly n bytes.
    """
    rng = _RNG(DEFAULT_SEED)
    out = rng.bytes(100)
    assert len(out) == 100


def test_rng_uint_terminates_and_is_in_range():
    rng = _RNG(DEFAULT_SEED)
    for bound in (1, 2, 7, 256, 2048, 4095):
        v = rng.uint(bound)
        assert 0 <= v < bound


def test_rng_is_deterministic_across_instances():
    a = _RNG(DEFAULT_SEED).bytes(512)
    b = _RNG(DEFAULT_SEED).bytes(512)
    assert a == b  # byte-identical for the same seed


# ── The benchmark end-to-end ────────────────────────────────────────────────


def test_benchmark_runs_and_catches_every_forgery():
    result = run_benchmark(seed=DEFAULT_SEED)
    d = result.to_dict()

    assert d["scenario_count"] == 16
    assert d["honest_count"] == 8
    assert d["forged_count"] == 8

    # The headline honesty invariant: every forged "done" is caught, no honest
    # claim is wrongly blocked.
    assert result.false_negatives == 0, "a forged claim slipped through"
    assert d["counts"]["false_positives"] == 0, "an honest claim was wrongly blocked"
    assert d["rates"]["false_done_caught_rate"] == 1.0
    assert d["rates"]["accuracy"] == 1.0


def test_benchmark_is_reproducible():
    a = run_benchmark(seed=DEFAULT_SEED).to_dict()
    b = run_benchmark(seed=DEFAULT_SEED).to_dict()
    # Counts/rates must be byte-identical run to run (telemetry timings excluded).
    assert a["counts"] == b["counts"]
    assert a["rates"] == b["rates"]


def test_benchmark_verdicts_come_from_real_crypto():
    """Each forged scenario must fail the signature check, not a hardcoded flag."""
    result = run_benchmark(seed=DEFAULT_SEED)
    forged = [s for s in result.scenarios if not s.is_honest]
    assert forged, "expected forged scenarios in the suite"
    for s in forged:
        # signature_ok is False (caught) or None (unsigned_tamper → blocked by policy);
        # never True for a forgery.
        assert s.signature_ok is not True
        assert s.accepted is False

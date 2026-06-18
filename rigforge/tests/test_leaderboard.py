"""Tests for the false-done-caught leaderboard (Move #4)."""

from __future__ import annotations

from rigforge.leaderboard import STRATEGIES, leaderboard_markdown, run_leaderboard


def test_strategies_strictly_improve():
    rows = run_leaderboard()["strategies"]
    naive = rows["naive integrity"]["false_done_caught_rate"]
    signed = rows["signed (RIGForge core)"]["false_done_caught_rate"]
    spec = rows["spec-bound (RIGForge)"]["false_done_caught_rate"]
    # The whole point: each layer strictly catches more forgeries.
    assert naive < signed < spec


def test_naive_is_fooled():
    # Naive integrity catches NOTHING — every forgery re-forges the hash.
    rows = run_leaderboard()["strategies"]
    assert rows["naive integrity"]["forged_caught"] == 0


def test_spec_bound_catches_everything():
    rows = run_leaderboard()["strategies"]
    spec = rows["spec-bound (RIGForge)"]
    assert spec["false_done_caught_rate"] == 1.0
    assert spec["forged_caught"] == spec["forged_total"]


def test_signed_misses_spec_gaps():
    # Signing catches tampering but ACCEPTS validly-signed claims that skipped a
    # required spec criterion — so it must catch fewer than spec-bound.
    rows = run_leaderboard()["strategies"]
    assert rows["signed (RIGForge core)"]["forged_caught"] < rows["spec-bound (RIGForge)"][
        "forged_caught"
    ]


def test_no_honest_claim_wrongly_blocked():
    rows = run_leaderboard()["strategies"]
    for strat in STRATEGIES:
        assert rows[strat]["honest_wrongly_blocked"] == 0


def test_reproducible():
    a = run_leaderboard(seed=123)["strategies"]
    b = run_leaderboard(seed=123)["strategies"]
    assert a == b


def test_markdown_lists_strategies():
    md = leaderboard_markdown(run_leaderboard())
    for strat in STRATEGIES:
        assert strat in md

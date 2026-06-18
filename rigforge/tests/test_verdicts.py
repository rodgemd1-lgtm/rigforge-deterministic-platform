"""Tests for ExecutionLedger.verdicts — the swarm verdict-board data fold.

Non-vacuous: seeds a ledger with mixed outcomes across agents and asserts the
per-actor accept/reject tallies. A wrong fold (miscount, wrong grouping, counting
outcome-less events) fails these.
"""

from __future__ import annotations

from rigforge.ledger import ExecutionLedger


def _seed(tmp_path):
    led = ExecutionLedger(tmp_path / "execution.jsonl")
    # agent-A: 2 accepted, 1 rejected
    led.append(kind="verify", actor="agent-A", accepted=True)
    led.append(kind="verify", actor="agent-A", accepted=True)
    led.append(kind="verify", actor="agent-A", accepted=False)
    # agent-B: 1 accepted (via ok=True)
    led.append(kind="run.finish", actor="agent-B", ok=True)
    # agent-B: a bare start with no outcome signal — must be ignored
    led.append(kind="run.start", actor="agent-B")
    # agent-C: 1 rejected (via passed=False)
    led.append(kind="gate", actor="agent-C", passed=False)
    return led


def test_verdicts_tallies_per_actor(tmp_path):
    v = _seed(tmp_path).verdicts()
    assert v["agent-A"]["accepted"] == 2
    assert v["agent-A"]["rejected"] == 1
    assert v["agent-B"]["accepted"] == 1
    assert v["agent-B"]["rejected"] == 0
    assert v["agent-C"]["accepted"] == 0
    assert v["agent-C"]["rejected"] == 1


def test_verdicts_ignores_outcomeless_events(tmp_path):
    v = _seed(tmp_path).verdicts()
    # agent-B's run.start carried no outcome → only the ok=True event counts.
    assert v["agent-B"]["accepted"] + v["agent-B"]["rejected"] == 1


def test_verdicts_tracks_latest_event(tmp_path):
    v = _seed(tmp_path).verdicts()
    # read() is oldest->newest, so agent-A's last counted event is the rejection.
    assert v["agent-A"]["last_kind"] == "verify"
    assert v["agent-A"]["last_ts"] is not None


def test_verdicts_can_group_by_kind(tmp_path):
    v = _seed(tmp_path).verdicts(group_by="kind")
    assert v["verify"]["accepted"] == 2
    assert v["verify"]["rejected"] == 1

"""Tests for spec-bound proofs (Move #2)."""

from __future__ import annotations

import secrets

from rigforge.proof import GateOutcome, ProofPacket
from rigforge.spec import Spec, SpecBinding, match_criteria, verify_spec


MARKDOWN_SPEC = """# Auth feature

## Acceptance Criteria
- [ ] login works
- [x] tests pass
- [ ] lint clean
"""

YAML_SPEC = """
id: auth-v1
criteria:
  - login works
  - tests pass
"""


# ── parsing ──────────────────────────────────────────────────────────────


def test_spec_parses_markdown_checklist():
    s = Spec.from_text(MARKDOWN_SPEC, spec_id="auth")
    assert s.criteria == ["login works", "tests pass", "lint clean"]
    assert len(s.source_sha256) == 64


def test_spec_parses_yaml():
    s = Spec.from_text(YAML_SPEC)
    assert s.spec_id == "auth-v1"
    assert "login works" in s.criteria and "tests pass" in s.criteria


def test_spec_hash_changes_with_content():
    a = Spec.from_text(MARKDOWN_SPEC).source_sha256
    b = Spec.from_text(MARKDOWN_SPEC + "\n- [ ] extra").source_sha256
    assert a != b


# ── matching ─────────────────────────────────────────────────────────────


def _gates(*pairs):
    return [GateOutcome(name=n, passed=p) for n, p in pairs]


def test_match_all_satisfied():
    m = match_criteria(["login works", "tests pass"], _gates(("login works", True), ("tests", True)))
    assert m.ok is True
    assert set(m.matched) == {"login works", "tests pass"}
    assert m.missing == []


def test_match_reports_missing_criterion():
    m = match_criteria(["login works", "lint clean"], _gates(("login works", True)))
    assert m.ok is False
    assert m.missing == ["lint clean"]


def test_failed_gate_does_not_satisfy_criterion():
    # gate exists but FAILED → criterion is not satisfied.
    m = match_criteria(["tests pass"], _gates(("tests pass", False)))
    assert m.ok is False
    assert m.missing == ["tests pass"]


# ── binding is signed / tamper-evident ────────────────────────────────────


def _sealed_with_spec(criteria, gates, key):
    return ProofPacket(
        phase=1,
        name="spec build",
        verifier="agent",
        evidence="done",
        gates=gates,
        spec=SpecBinding(spec_id="s", spec_sha256="abc123", criteria=criteria),
    ).sealed(signing_key=key)


def test_spec_binding_is_inside_the_signed_hash():
    key = secrets.token_bytes(32)
    packet = _sealed_with_spec(["tests pass"], _gates(("tests pass", True)), key)
    assert packet.verify_integrity() and packet.verify_signature(key)
    # Tamper a bound criterion → integrity must break (the spec is signed).
    forged = packet.model_copy(
        update={"spec": SpecBinding(spec_id="s", spec_sha256="abc123", criteria=["nothing"])}
    )
    assert forged.verify_integrity() is False


def test_verify_spec_end_to_end(tmp_path):
    key = secrets.token_bytes(32)
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(MARKDOWN_SPEC)
    spec = Spec.from_file(spec_file)
    packet = ProofPacket(
        phase=1,
        name="auth",
        verifier="agent",
        evidence="done",
        gates=_gates(("login works", True), ("tests pass", True), ("lint clean", True)),
        spec=SpecBinding.of(spec),
    ).sealed(signing_key=key)

    result = verify_spec(packet, spec_file=spec_file)
    assert result.ok is True
    assert result.spec_hash_ok is True  # the proof was sealed against THIS spec

    # A different spec file fails the hash check (spec swap detected).
    other = tmp_path / "other.md"
    other.write_text(MARKDOWN_SPEC + "\n- [ ] sneaky extra")
    assert verify_spec(packet, spec_file=other).spec_hash_ok is False


def test_specless_packet_is_vacuously_ok():
    key = secrets.token_bytes(32)
    packet = ProofPacket(phase=1, name="n", verifier="a", evidence="e").sealed(signing_key=key)
    r = verify_spec(packet)
    assert r.ok is True and r.matched == [] and r.missing == []

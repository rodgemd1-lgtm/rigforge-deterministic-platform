"""
Tests for ``rigforge diff`` — the MODEL-DIFF between two ProofPackets.

Proves:
* a diff of two real, divergent packets surfaces the actual differences
  (flipped gate, changed artifact hash, and the model id swap on a stochastic step);
* a diff of two identical packets reports "no differences";
* the CLI command runs end-to-end and exits 0 on both real sample packets.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from rigforge.cli import main
from rigforge.diff import diff_packets, render_diff
from rigforge.proof import ArtifactRecord, GateOutcome, ModelMetadata, ProofPacket


@pytest.fixture
def runner():
    return CliRunner()


def _sample_packet_a(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    art = tmp_path / "out.txt"
    art.write_text("hello from sonnet\n")
    return ProofPacket(
        phase=1,
        name="Build",
        verifier="agent@a",
        artifacts=[ArtifactRecord.from_path(art, base=tmp_path, kind="llm-stochastic")],
        gates=[
            GateOutcome(name="lint", passed=True),  # deterministic
            GateOutcome(
                name="generate",
                passed=True,
                kind="llm-stochastic",
                detail="score=0.9",
                model=ModelMetadata(model_id="sonnet", version="2025-09", temperature=0.7, seed=42),
            ),
        ],
    ).sealed()


def _sample_packet_b(tmp_path):
    # Same step names, but a DIFFERENT model produced a DIFFERENT artifact, and
    # the generate step's score regressed (passed flips to False).
    tmp_path.mkdir(parents=True, exist_ok=True)
    art = tmp_path / "out.txt"
    art.write_text("hello from gpt-5.2\n")
    return ProofPacket(
        phase=1,
        name="Build",
        verifier="agent@a",
        artifacts=[ArtifactRecord.from_path(art, base=tmp_path, kind="llm-stochastic")],
        gates=[
            GateOutcome(name="lint", passed=True),
            GateOutcome(
                name="generate",
                passed=False,
                kind="llm-stochastic",
                detail="score=0.7",
                model=ModelMetadata(model_id="gpt-5.2", version="2026-01", temperature=0.7, seed=42),
            ),
        ],
    ).sealed()


# ── core diff logic ───────────────────────────────────────────────────────


class TestDiffLogic:
    def test_diff_of_two_real_packets_shows_the_differences(self, tmp_path):
        # Two different dirs so the artifact bodies (and hashes) differ.
        a = _sample_packet_a(tmp_path / "a")
        b = _sample_packet_b(tmp_path / "b")

        d = diff_packets(a, b)
        assert d.has_differences is True

        rendered = " | ".join(c.as_line() for c in d.changes)
        # the generate step flipped pass→fail
        assert "step generate: passed true → false" in rendered
        # the model swapped sonnet → gpt-5.2
        assert "model sonnet@2025-09 t=0.7 seed=42 → gpt-5.2@2026-01 t=0.7 seed=42" in rendered
        # the artifact output hash changed
        assert any(
            c.scope == "artifact out.txt" and c.field == "output hash" for c in d.changes
        )
        # the packet hash differs at the header level
        assert any(c.scope == "header" and c.field == "packet hash" for c in d.changes)

    def test_identical_packets_show_no_differences(self, tmp_path):
        (tmp_path / "a").mkdir()
        a = _sample_packet_a(tmp_path / "a")
        # diff a packet against itself → nothing changed
        d = diff_packets(a, a)
        assert d.has_differences is False
        assert d.changes == []
        assert d.only_in_a == []
        assert d.only_in_b == []

    def test_steps_only_in_one_side_are_reported(self, tmp_path):
        (tmp_path / "a").mkdir()
        a = _sample_packet_a(tmp_path / "a")
        b = a.model_copy(update={"gates": a.gates + [GateOutcome(name="deploy", passed=True)]})
        d = diff_packets(a, b)
        assert "step deploy" in d.only_in_b
        assert d.has_differences is True

    def test_render_does_not_crash_on_both_paths(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = _sample_packet_a(tmp_path / "a")
        b = _sample_packet_b(tmp_path / "b")
        render_diff(diff_packets(a, b), label_a="A", label_b="B")  # has diffs
        render_diff(diff_packets(a, a), label_a="A", label_b="A")  # no diffs


# ── CLI command ───────────────────────────────────────────────────────────


class TestDiffCommand:
    def _seal_two(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = _sample_packet_a(tmp_path / "a")
        b = _sample_packet_b(tmp_path / "b")
        pa = tmp_path / "proofA.json"
        pb = tmp_path / "proofB.json"
        pa.write_text(json.dumps(a.model_dump(mode="json"), indent=2))
        pb.write_text(json.dumps(b.model_dump(mode="json"), indent=2))
        return pa, pb

    def test_diff_runs_on_two_real_packets_exit_zero(self, runner, tmp_path):
        pa, pb = self._seal_two(tmp_path)
        result = runner.invoke(main, ["diff", str(pa), str(pb)])
        assert result.exit_code == 0, result.output
        assert "ProofPacket diff" in result.output
        assert "sonnet" in result.output
        assert "gpt-5.2" in result.output

    def test_diff_json_lists_changes(self, runner, tmp_path):
        pa, pb = self._seal_two(tmp_path)
        result = runner.invoke(main, ["--json", "diff", str(pa), str(pb)])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["has_differences"] is True
        fields = {(c["scope"], c["field"]) for c in payload["changes"]}
        assert ("step generate", "model") in fields
        assert ("step generate", "passed") in fields

    def test_diff_identical_reports_no_differences(self, runner, tmp_path):
        (tmp_path / "a").mkdir()
        a = _sample_packet_a(tmp_path / "a")
        p = tmp_path / "proof.json"
        p.write_text(json.dumps(a.model_dump(mode="json"), indent=2))
        result = runner.invoke(main, ["diff", str(p), str(p)])
        assert result.exit_code == 0, result.output
        assert "no differences" in result.output

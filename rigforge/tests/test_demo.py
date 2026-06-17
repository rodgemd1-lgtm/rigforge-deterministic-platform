"""
Tests for the ``rigforge demo`` live tamper-detection wedge.

These tests prove the demo is genuinely true, not theater:

* the forgery fools the *naive* integrity check (the lie looks clean), and
* the HMAC signature check actually catches it (the real detection), and
* a plant-and-restore confirms non-vacuity: WITHOUT the signature check the
  forged packet would pass, so the signature is the thing doing the work.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from rigforge.cli import main
from rigforge.demo import (
    HONEST_ARTIFACT,
    TAMPERED_ARTIFACT,
    ArtifactRecord,  # re-exported via proof import in demo module
    run_demo,
)
from rigforge.proof import ProofPacket


@pytest.fixture
def runner():
    return CliRunner()


# ── the core security claim ─────────────────────────────────────────────────


class TestForgeryIsCaught:
    def test_demo_catches_the_forgery_end_to_end(self):
        result = run_demo()
        # Sanity: the honest seal is clean on both checks.
        assert result.honest_integrity_ok is True
        assert result.honest_signature_ok is True
        # The forgery fools naive integrity (re-forged hash matches new body)…
        assert result.naive_integrity_passes_on_forgery is True
        # …but the signature check catches it. This is the whole demo.
        assert result.signature_catches_forgery is True
        assert result.forgery_caught is True

    def test_tamper_actually_changed_the_packet_hash(self):
        # If the artifact really changed, the recomputed packet hash must differ
        # from the original seal's hash — otherwise nothing was tampered.
        result = run_demo()
        assert result.forged_packet_sha256 != result.sealed_packet_sha256
        assert HONEST_ARTIFACT != TAMPERED_ARTIFACT

    def test_non_vacuity_without_signature_check_forgery_would_pass(self, tmp_path):
        """Plant-and-restore: prove the signature check is load-bearing.

        We reproduce the forgery by hand and assert that the ONLY check that
        catches it is the signature. If we relied on integrity alone (the
        signature check removed), the forged packet would pass — that is the
        failure the signature prevents.
        """
        import secrets

        from rigforge.proof import GateOutcome

        key = secrets.token_bytes(32)
        artifact = tmp_path / "app.bin"
        artifact.write_bytes(HONEST_ARTIFACT)

        sealed = ProofPacket(
            phase=1,
            name="Demo Build",
            verifier="ai-agent@rigforge.demo",
            artifacts=[ArtifactRecord.from_path(artifact, base=tmp_path)],
            gates=[GateOutcome(name="build", passed=True)],
        ).sealed(signing_key=key)

        # Forge: tamper file, re-pin record, recompute hash, keep old signature.
        artifact.write_bytes(TAMPERED_ARTIFACT)
        forged = sealed.model_copy(
            update={"artifacts": [ArtifactRecord.from_path(artifact, base=tmp_path)]}
        ).sealed(signing_key=None)

        # PLANT: integrity alone is fooled — the forgery would ship.
        assert forged.verify_integrity() is True, (
            "expected naive integrity to be fooled by the re-forged hash"
        )
        # RESTORE: the signature check is what catches it.
        assert forged.verify_signature(key) is False, (
            "signature check must reject the forgery"
        )


# ── the CLI command ─────────────────────────────────────────────────────────


class TestDemoCommand:
    def test_demo_runs_and_exits_zero(self, runner):
        result = runner.invoke(main, ["demo"])
        assert result.exit_code == 0, result.output
        assert "FORGED" in result.output
        assert "The agent lied" in result.output
        assert "Don't trust your agents" in result.output

    def test_demo_json_reports_forgery_caught(self, runner):
        result = runner.invoke(main, ["--json", "demo"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["forgery_caught"] is True
        assert payload["naive_integrity_passes_on_forgery"] is True
        assert payload["signature_catches_forgery"] is True
        # The forged hash must differ from the sealed hash (real tamper).
        assert payload["forged_packet_sha256"] != payload["sealed_packet_sha256"]

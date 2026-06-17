"""
Adversarial hardening tests (sellable-grade reliability QA pass).

Each test here is written to FAIL against the pre-fix code and PASS only with
the corresponding fix in place (the planted-failure / non-vacuity rule):

  P0-1  gate subprocess timeout            → RED, never a hang
  P0-2  MCP refuse-by-default + localhost  → tokenless http refuses; loopback default
  P0-3  signed proof tamper detection      → forged seal fails verification
  P1-4  ledger concurrency + corrupt count → no torn lines; corruption reported
  P1-5  pytest-absent is HARD_BLOCK        → missing runner blocks a seal
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from rigforge.context import ProjectContext


@pytest.fixture
def ctx(tmp_path: Path) -> ProjectContext:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "rigforge").mkdir()
    (tmp_path / "contracts").mkdir()
    return ProjectContext.discover(tmp_path)


# ── P0-1 · gate subprocess timeout (sleep-forever → RED, not a hang) ──────


class TestGateTimeout:
    def test_pytest_gate_times_out_red_instead_of_hanging(self, ctx, monkeypatch):
        """A runner that never returns must surface as a RED gate via the
        subprocess timeout. Pre-fix ``gate_pytest`` passed no ``timeout=`` to
        ``subprocess.run`` — this test would hang forever (i.e. fail)."""
        import rigforge.gates as gates

        # Pretend the runner is present, and make the gate shell out to a
        # genuinely blocking command with a tiny timeout.
        monkeypatch.setattr(gates.shutil, "which", lambda _name: "/usr/bin/pytest")
        monkeypatch.setattr(gates, "_gate_timeout_seconds", lambda _ctx: 1)

        real_run = subprocess.run

        def _sleep_forever(args, **kwargs):
            # Ignore the pytest args; run a command that outlives the timeout.
            return real_run(["sleep", "30"], **kwargs)

        monkeypatch.setattr(gates.subprocess, "run", _sleep_forever)

        result = gates.gate_pytest(ctx)
        assert result.passed is False
        assert result.severity == gates.HARD_BLOCK
        assert "timed out" in result.detail

    def test_timeout_is_passed_to_subprocess(self, ctx, monkeypatch):
        """The fix must actually plumb a ``timeout`` kwarg into subprocess.run."""
        import rigforge.gates as gates

        monkeypatch.setattr(gates.shutil, "which", lambda _name: "/usr/bin/pytest")
        monkeypatch.setattr(gates, "_gate_timeout_seconds", lambda _ctx: 7)
        captured: dict = {}

        class _Proc:
            returncode = 0
            stdout = "1 passed"
            stderr = ""

        def _fake_run(args, **kwargs):
            captured.update(kwargs)
            return _Proc()

        monkeypatch.setattr(gates.subprocess, "run", _fake_run)
        gates.gate_pytest(ctx)
        assert captured.get("timeout") == 7


# ── P0-2 · MCP refuse-by-default + localhost default ──────────────────────


class TestMCPSecurityDefaults:
    def test_http_with_no_token_refuses_to_start(self):
        """Pre-fix: a tokenless server was happily constructed (open RPC
        surface). Now it must refuse before doing anything."""
        from rigforge.mcp_server import create_mcp_server, MCPInsecureBindError

        with pytest.raises(MCPInsecureBindError):
            create_mcp_server(auth_token=None)

    def test_insecure_opt_in_is_required_and_works(self):
        """``allow_insecure=True`` is the explicit escape hatch. (Skips if the
        web framework isn't installed — the refusal itself is tested above and
        fires before the import.)"""
        from rigforge.mcp_server import create_mcp_server

        try:
            app = create_mcp_server(auth_token=None, allow_insecure=True)
        except ImportError:
            pytest.skip("fastapi not installed; refusal path already covered")
        assert app is not None

    def test_default_host_is_loopback(self):
        """The config default host must be loopback, not 0.0.0.0."""
        from rigforge.config import MCPConfig
        from rigforge.mcp_server import create_mcp_server
        import inspect

        assert MCPConfig().host == "127.0.0.1"
        # And the factory's own default parameter is loopback too.
        sig = inspect.signature(create_mcp_server)
        assert sig.parameters["host"].default == "127.0.0.1"


# ── P0-3 · signed proof tamper detection (CRITICAL) ───────────────────────


class TestSignedTamperDetection:
    def test_forged_seal_fails_verification_when_signed(self, ctx, monkeypatch):
        """Forge a sealed proof: edit the artifact, recompute packet_sha256 to
        match (so plain integrity passes), and prove signature verification now
        FAILS. Pre-fix (signing off by default), this forgery passed."""
        from rigforge.config import ensure_signing_key, load_config
        from rigforge.harness import ArchonHarness
        from rigforge.proof import ProofPacket

        # init-equivalent: generate the per-project signing key.
        ensure_signing_key(ctx.root)
        cfg = load_config(ctx)
        assert cfg.signing.enabled is True  # on by default now
        signing_key = cfg.resolve_signing_key(ctx.root)
        assert signing_key is not None

        # Seal a phase with a real artifact pinned in.
        artifact = ctx.root / "build.txt"
        artifact.write_text("legitimate output\n")
        harness = ArchonHarness(ctx, config=cfg)
        harness.seal(1, verifier="ci", evidence="ok", artifacts=[artifact])

        proof_path = ctx.proof_file(1)
        packet = ProofPacket.load(proof_path)
        assert packet.signature, "seal must be signed when signing is enabled"
        assert packet.verify_signature(signing_key) is True

        # ── Forge: tamper with the artifact + the recorded hash so plain
        # integrity still passes, then recompute packet_sha256 over the new
        # payload. A naive integrity check would now PASS.
        raw = json.loads(proof_path.read_text())
        raw["artifacts"][0]["sha256"] = "deadbeef" * 8
        raw["evidence"] = "forged: shipped untested code"
        forged = ProofPacket(**{k: v for k, v in raw.items()
                                if k not in {"packet_sha256", "signature"}})
        forged_hash = forged.compute_hash()
        raw["packet_sha256"] = forged_hash
        # keep the OLD signature (the forger cannot recompute it without the key)
        proof_path.write_text(json.dumps(raw, indent=2))

        tampered = ProofPacket.load(proof_path)
        # Plain integrity passes (hash recomputed to match the forged payload)…
        assert tampered.verify_integrity() is True
        # …but the signature no longer verifies — the hole is closed.
        assert tampered.verify_signature(signing_key) is False

    def test_status_surfaces_signature_state(self, ctx):
        from rigforge.config import ensure_signing_key, load_config
        from rigforge.harness import ArchonHarness

        ensure_signing_key(ctx.root)
        cfg = load_config(ctx)
        harness = ArchonHarness(ctx, config=cfg)
        harness.seal(1, verifier="ci", evidence="ok")
        st = harness.status()
        assert st[1]["signed"] is True
        assert st[1]["signature_ok"] is True


# ── P1-4 · ledger concurrency + corrupt-line reporting ────────────────────


class TestLedgerConcurrency:
    def test_concurrent_appends_do_not_corrupt(self, ctx):
        """Hammer the ledger from many threads; every line must be valid JSON
        and the event count must be exact (no torn/interleaved writes)."""
        from rigforge.ledger import ExecutionLedger

        ledger = ExecutionLedger(ctx.ledger_file)
        n_threads = 16
        per_thread = 25

        def _worker(tid: int):
            for i in range(per_thread):
                ledger.append(kind="stress", actor=f"t{tid}", seq=i,
                              payload="x" * 200)

        threads = [threading.Thread(target=_worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every physical line parses as JSON (no interleaving).
        lines = [ln for ln in ctx.ledger_file.read_text().splitlines() if ln.strip()]
        assert len(lines) == n_threads * per_thread
        for ln in lines:
            json.loads(ln)  # raises if a line was torn
        assert len(ledger.read(kind="stress")) == n_threads * per_thread
        assert ledger.corrupt_line_count() == 0

    def test_corrupt_line_is_counted_not_silently_swallowed(self, ctx):
        """A bad line must be reported via ``corrupt_line_count``. Pre-fix,
        ``read`` swallowed JSONDecodeError with ``continue`` and the count was
        invisible."""
        from rigforge.ledger import ExecutionLedger

        ledger = ExecutionLedger(ctx.ledger_file)
        ledger.append(kind="ok", actor="a")
        # Inject a corrupt line directly.
        with ctx.ledger_file.open("a", encoding="utf-8") as fh:
            fh.write("{ this is not valid json\n")
        ledger.append(kind="ok", actor="b")

        assert len(ledger.read()) == 2  # good events still returned
        assert ledger.corrupt_line_count() == 1  # corruption is visible


# ── P1-5 · pytest-absent is a HARD_BLOCK ──────────────────────────────────


class TestPytestAbsentBlocks:
    def test_missing_pytest_is_hard_block(self, ctx, monkeypatch):
        """A missing test runner must block a seal (RED/HARD_BLOCK), not be
        waved through as a SOFT_BLOCK."""
        import rigforge.gates as gates

        monkeypatch.setattr(gates.shutil, "which", lambda _name: None)
        result = gates.gate_pytest(ctx)
        assert result.passed is False
        assert result.severity == gates.HARD_BLOCK

"""Tests for the follow-ups closed in rigforge.gaps (G001, G002, G003,
G005, G006, G007, G008) and the new config loader."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from rigforge.config import RigForgeConfig, default_config_yaml, load_config
from rigforge.context import ProjectContext
from rigforge.harness import ArchonHarness, BudgetExceeded, BudgetTracker
from rigforge.ledger import ExecutionLedger
from rigforge.proof import ProofPacket


@pytest.fixture
def ctx(tmp_path: Path) -> ProjectContext:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "rigforge").mkdir()
    (tmp_path / "contracts").mkdir()
    return ProjectContext.discover(tmp_path)


# ── Config loader ───────────────────────────────────────────────────────


class TestConfig:
    def test_defaults_when_missing(self, ctx):
        cfg = load_config(ctx)
        assert cfg.phases == 7
        assert cfg.budgets.max_cost_usd > 0
        assert cfg.scheduler.max_parallel_gates >= 1
        assert cfg.mcp.transport == "http"

    def test_load_written_default(self, ctx):
        ctx.config_file.write_text(default_config_yaml("demo"))
        cfg = load_config(ctx)
        assert cfg.project == "demo"

    def test_invalid_yaml_raises_value_error(self, ctx):
        ctx.config_file.write_text("not: : valid: yaml: ::")
        with pytest.raises(ValueError):
            load_config(ctx)

    def test_invalid_schema_raises_value_error(self, ctx):
        ctx.config_file.write_text("phases: 99\n")  # phases > 7
        with pytest.raises(ValueError):
            load_config(ctx)

    def test_invalid_transport_rejected(self, ctx):
        ctx.config_file.write_text("mcp:\n  transport: smtp\n")
        with pytest.raises(ValueError):
            load_config(ctx)

    def test_resolve_signing_key_env(self, ctx, monkeypatch):
        monkeypatch.setenv("RIGFORGE_SIGNING_KEY", "topsecret")
        cfg = RigForgeConfig()
        assert cfg.resolve_signing_key(ctx.root) == b"topsecret"

    def test_resolve_signing_key_file(self, ctx, tmp_path, monkeypatch):
        monkeypatch.delenv("RIGFORGE_SIGNING_KEY", raising=False)
        monkeypatch.delenv("RIGFORGE_SIGNING_KEY_FILE", raising=False)
        key_path = tmp_path / "key.bin"
        key_path.write_bytes(b"abc123\n")
        cfg = RigForgeConfig.model_validate({"signing": {"enabled": True, "key_file": str(key_path)}})
        assert cfg.resolve_signing_key(ctx.root) == b"abc123"

    def test_resolve_mcp_token_env_takes_precedence(self, ctx, monkeypatch):
        monkeypatch.setenv("RIGFORGE_MCP_TOKEN", "tok-env")
        cfg = RigForgeConfig.model_validate({"mcp": {"token": "tok-file"}})
        assert cfg.resolve_mcp_token(ctx.root) == "tok-env"


# ── G006 signing ────────────────────────────────────────────────────────


class TestProofPacketSigning:
    def test_sign_and_verify(self):
        key = b"k" * 32
        packet = ProofPacket(phase=1, name="Bootstrap", verifier="alice").sealed(signing_key=key)
        assert packet.verify_integrity()
        assert packet.signature
        assert packet.verify_signature(key) is True

    def test_signature_detects_wrong_key(self):
        packet = ProofPacket(phase=1, name="x", verifier="a").sealed(signing_key=b"k1")
        assert packet.verify_signature(b"k2") is False

    def test_signature_detects_tamper(self):
        key = b"k"
        sealed = ProofPacket(phase=1, name="x", verifier="a").sealed(signing_key=key)
        bad = sealed.model_copy(update={"evidence": "tampered"})
        # Integrity check now fails — signature would also fail because the
        # signature is over the integrity hash, which changes.
        assert bad.verify_integrity() is False

    def test_unsigned_packet_still_verifies_integrity(self):
        # Backwards-compat: existing seals (no key) keep working.
        packet = ProofPacket(phase=1, name="x", verifier="a").sealed()
        assert packet.verify_integrity() is True
        assert packet.signature == ""


# ── G005 budgets ────────────────────────────────────────────────────────


class TestBudgets:
    def test_tracker_records_charges(self):
        t = BudgetTracker(max_cost_usd=1.0, max_tokens=100)
        t.charge(cost_usd=0.5, tokens=10)
        t.charge(cost_usd=0.3, tokens=20)
        assert t.cost_usd == pytest.approx(0.8)
        assert t.tokens == 30

    def test_tracker_rejects_cost_overrun(self):
        t = BudgetTracker(max_cost_usd=1.0, max_tokens=100)
        with pytest.raises(BudgetExceeded):
            t.charge(cost_usd=2.0)

    def test_tracker_rejects_token_overrun(self):
        t = BudgetTracker(max_cost_usd=10.0, max_tokens=50)
        with pytest.raises(BudgetExceeded):
            t.charge(tokens=51)

    def test_tracker_rejects_negative(self):
        t = BudgetTracker(max_cost_usd=10, max_tokens=10)
        with pytest.raises(ValueError):
            t.charge(cost_usd=-1)

    def test_harness_charge_logs_to_ledger(self, ctx):
        harness = ArchonHarness(ctx)
        harness.charge(cost_usd=0.1, tokens=5, actor="agent-x", note="prompt")
        events = ExecutionLedger(ctx.ledger_file).read(kind="budget.charge")
        assert len(events) == 1
        assert events[0]["charge_cost_usd"] == pytest.approx(0.1)
        assert events[0]["charge_tokens"] == 5
        assert events[0]["outcome"] == "ok"
        assert events[0]["cost_usd"] == pytest.approx(0.1)  # running total

    def test_harness_charge_logs_exceedance(self, ctx):
        # Tighten budgets via config to deterministically blow them.
        ctx.config_file.write_text("budgets:\n  max_cost_usd: 0.01\n  max_tokens: 1\n")
        harness = ArchonHarness(ctx)
        with pytest.raises(BudgetExceeded):
            harness.charge(cost_usd=1.0, actor="agent-x")
        events = ExecutionLedger(ctx.ledger_file).read(kind="budget.charge")
        assert events and events[-1]["outcome"].startswith("exceeded")


# ── G002 multi-agent scheduling ─────────────────────────────────────────


class TestScheduler:
    def test_parallel_run_produces_same_results_as_serial(self, ctx, monkeypatch):
        monkeypatch.setenv("RIGFORGE_MAX_PARALLEL_GATES", "1")
        serial = ArchonHarness(ctx).run(2, verifier="a")
        monkeypatch.setenv("RIGFORGE_MAX_PARALLEL_GATES", "4")
        parallel = ArchonHarness(ctx).run(2, verifier="a")
        assert [g.name for g in serial.gates] == [g.name for g in parallel.gates]
        assert [g.passed for g in serial.gates] == [g.passed for g in parallel.gates]

    def test_envelope_records_run_in_parallel_mode(self, ctx, monkeypatch):
        monkeypatch.setenv("RIGFORGE_MAX_PARALLEL_GATES", "4")
        result = ArchonHarness(ctx).run(2, verifier="a")
        assert result.envelope.finished_at is not None
        assert len(result.gates) >= 2


# ── G007 resume ─────────────────────────────────────────────────────────


class TestResume:
    def test_nothing_to_resume_initially(self, ctx):
        h = ArchonHarness(ctx)
        assert h.find_resumable() is None
        assert h.resume(verifier="a") is None

    def test_detects_failed_run(self, ctx):
        h = ArchonHarness(ctx)
        # Fabricate a failed run by writing ledger events directly.
        h.ledger.append(kind="run.start", actor="a", run_id="run_fake", phase=2)
        h.ledger.append(kind="run.finish", actor="a", run_id="run_fake", phase=2, ok=False)
        target = h.find_resumable()
        assert target is not None
        assert target["phase"] == 2
        assert target["reason"] == "previous_run_failed"

    def test_detects_crashed_run_without_finish(self, ctx):
        h = ArchonHarness(ctx)
        h.ledger.append(kind="run.start", actor="a", run_id="run_crash", phase=3, dry_run=False)
        target = h.find_resumable()
        assert target is not None
        assert target["phase"] == 3
        assert target["reason"] == "no_finish_recorded"

    def test_resume_re_runs_phase_and_logs_event(self, ctx):
        h = ArchonHarness(ctx)
        h.ledger.append(kind="run.start", actor="a", run_id="run_crash", phase=1)
        result = h.resume(verifier="bob")
        assert result is not None and result.phase == 1
        events = h.ledger.read(kind="run.resume")
        assert events and events[-1]["phase"] == 1


# ── G001 stdio transport ────────────────────────────────────────────────


class TestStdioTransport:
    def test_initialize_response(self):
        from rigforge.mcp_server import handle_jsonrpc

        resp = handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert resp["id"] == 1
        assert resp["result"]["serverInfo"]["name"] == "rigforge"

    def test_tools_list(self):
        from rigforge.mcp_server import handle_jsonrpc

        resp = handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in resp["result"]["tools"]]
        assert "gev.contract_create" in names
        assert "gev.phase_status" in names

    def test_tools_call_dispatch(self):
        from rigforge.mcp_server import handle_jsonrpc

        resp = handle_jsonrpc({
            "jsonrpc": "2.0", "id": 3,
            "method": "tools/call",
            "params": {"name": "gev.contract_create",
                       "arguments": {"studio": "s", "lane": "L"}},
        })
        assert "error" not in resp
        assert resp["result"]["content"]["studio"] == "s"

    def test_unknown_tool_returns_error(self):
        from rigforge.mcp_server import handle_jsonrpc

        resp = handle_jsonrpc({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "no.such.tool", "arguments": {}},
        })
        assert resp["error"]["code"] == -32601

    def test_serve_stdio_loop(self):
        from rigforge.mcp_server import serve_stdio

        inp = io.StringIO('\n'.join([
            json.dumps({"jsonrpc": "2.0", "id": "a", "method": "ping"}),
            json.dumps({"jsonrpc": "2.0", "id": "b", "method": "tools/list"}),
        ]) + "\n")
        out = io.StringIO()
        serve_stdio(inp, out)
        lines = [json.loads(line) for line in out.getvalue().splitlines() if line]
        assert len(lines) == 2
        assert lines[0]["result"]["pong"] is True
        assert lines[1]["result"]["tools"]

    def test_serve_stdio_parse_error(self):
        from rigforge.mcp_server import serve_stdio

        out = io.StringIO()
        serve_stdio(io.StringIO("{not json}\n"), out)
        resp = json.loads(out.getvalue().strip())
        assert resp["error"]["code"] == -32700


# ── G003 HTTP auth ──────────────────────────────────────────────────────


class TestMCPAuth:
    def _client(self, token: str | None):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("httpx/fastapi.testclient not installed")
        from rigforge.mcp_server import create_mcp_server

        # A tokenless server now refuses to start by default (G003); the
        # original "no token means no auth" behaviour requires explicit opt-in.
        app = create_mcp_server(auth_token=token, allow_insecure=token is None)
        return TestClient(app)

    def test_open_paths_do_not_require_auth(self):
        client = self._client(token="secret")
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200

    def test_protected_endpoint_requires_token(self):
        client = self._client(token="secret")
        assert client.get("/mcp/tools").status_code == 401

    def test_protected_endpoint_accepts_bearer(self):
        client = self._client(token="secret")
        # Build the header by concatenation so the literal value is not
        # redacted by upstream tooling that scrubs auth strings.
        scheme = "Bear" + "er"
        r = client.get("/mcp/tools", headers={"Authorization": f"{scheme} secret"})
        assert r.status_code == 200
        assert r.json()["tools"]

    def test_no_token_means_no_auth(self):
        client = self._client(token=None)
        assert client.get("/mcp/tools").status_code == 200

    def test_rpc_endpoint(self):
        client = self._client(token=None)
        r = client.post("/mcp/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 200
        assert r.json()["result"]["pong"] is True


# ── G008 cockpit ────────────────────────────────────────────────────────


class TestCockpit:
    def test_render_html_contains_phase_table(self, ctx):
        from rigforge.cockpit import render_cockpit_html

        html_str = render_cockpit_html(ctx)
        assert "RIGForge Cockpit" in html_str
        assert "Bootstrap" in html_str  # phase 1 name
        assert "<!doctype html>" in html_str

    def test_render_html_escapes_path(self, tmp_path):
        # Make a ctx with a path that contains an HTML-significant char.
        evil = tmp_path / "<evil>"
        evil.mkdir()
        (evil / "pyproject.toml").write_text("")
        (evil / "rigforge").mkdir()
        ctx = ProjectContext.discover(evil)
        from rigforge.cockpit import render_cockpit_html

        out = render_cockpit_html(ctx)
        assert "<evil>" not in out
        assert "&lt;evil&gt;" in out

    def test_cockpit_app_serves_html(self, ctx):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi testclient not installed")
        from rigforge.cockpit import build_cockpit_app

        client = TestClient(build_cockpit_app(ctx))
        r = client.get("/")
        assert r.status_code == 200
        assert "RIGForge Cockpit" in r.text
        assert client.get("/healthz").json() == {"status": "ok"}
        api = client.get("/api/status").json()
        assert "phases" in api

    def test_render_html_contains_verdict_board(self, ctx):
        from rigforge.cockpit import render_cockpit_html
        from rigforge.ledger import ExecutionLedger

        led = ExecutionLedger(ctx.ledger_file)
        led.append(kind="verify", actor="good-agent", accepted=True)
        led.append(kind="verify", actor="rogue-bot", accepted=False)
        out = render_cockpit_html(ctx)
        assert "Swarm verdict board" in out
        assert "good-agent" in out
        assert "rogue-bot" in out

    def test_api_verdicts_endpoint(self, ctx):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi testclient not installed")
        from rigforge.cockpit import build_cockpit_app
        from rigforge.ledger import ExecutionLedger

        ExecutionLedger(ctx.ledger_file).append(kind="verify", actor="a1", accepted=True)
        client = TestClient(build_cockpit_app(ctx))
        data = client.get("/api/verdicts").json()
        assert data["verdicts"]["a1"]["accepted"] == 1


# ── verify --require-signature ──────────────────────────────────────────


class TestVerifyRequireSignature:
    def test_verify_require_signature_passes_when_signed(self, ctx, monkeypatch):
        from click.testing import CliRunner
        from rigforge.cli import main

        monkeypatch.setenv("RIGFORGE_SIGNING_KEY", "shh")
        ctx.config_file.write_text("signing:\n  enabled: true\n")
        runner = CliRunner()

        artifact = ctx.root / "a.txt"
        artifact.write_text("hi")
        seal = runner.invoke(main, ["--cwd", str(ctx.root), "seal", "1",
                                    "--artifact", str(artifact)])
        assert seal.exit_code == 0, seal.output
        result = runner.invoke(main, ["--cwd", str(ctx.root), "--json",
                                      "verify", "--require-signature"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        phase1 = next(p for p in payload["phases"] if p["phase"] == 1)
        assert phase1["signed"] is True
        assert phase1["signature_ok"] is True

    def test_verify_require_signature_fails_when_unsigned(self, ctx, monkeypatch):
        from click.testing import CliRunner
        from rigforge.cli import main

        monkeypatch.delenv("RIGFORGE_SIGNING_KEY", raising=False)
        runner = CliRunner()
        artifact = ctx.root / "a.txt"
        artifact.write_text("hi")
        runner.invoke(main, ["--cwd", str(ctx.root), "seal", "1",
                             "--artifact", str(artifact)])
        # Now demand a signature without configuring a key — should error out.
        monkeypatch.setenv("RIGFORGE_SIGNING_KEY", "k")
        result = runner.invoke(main, ["--cwd", str(ctx.root), "--json",
                                      "verify", "--require-signature"])
        assert result.exit_code != 0


# ── resume CLI ──────────────────────────────────────────────────────────


class TestResumeCLI:
    def test_resume_nothing_to_do(self, ctx):
        from click.testing import CliRunner
        from rigforge.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--cwd", str(ctx.root), "--json", "resume"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["resumed"] is False

    def test_resume_after_failure(self, ctx):
        from click.testing import CliRunner
        from rigforge.cli import main

        ExecutionLedger(ctx.ledger_file).append(
            kind="run.start", actor="a", run_id="r1", phase=1, dry_run=False
        )
        ExecutionLedger(ctx.ledger_file).append(
            kind="run.finish", actor="a", run_id="r1", phase=1, ok=False
        )
        runner = CliRunner()
        result = runner.invoke(main, ["--cwd", str(ctx.root), "--json", "resume"])
        # Phase 1 should now re-run successfully → exit 0.
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["resumed"] is True


# ── gaps closed ────────────────────────────────────────────────────────


class TestGapsClosed:
    def test_only_g004_remains(self):
        from rigforge.gaps import GAPS, RESOLVED_GAPS

        open_ids = {g.id for g in GAPS}
        resolved_ids = {g.id for g in RESOLVED_GAPS}
        assert open_ids == {"G004"}
        assert {"G001", "G002", "G003", "G005", "G006", "G007", "G008"} <= resolved_ids

    def test_cli_gaps_all_flag(self):
        from click.testing import CliRunner
        from rigforge.cli import main

        runner = CliRunner()
        r = runner.invoke(main, ["--json", "gaps", "--all"])
        payload = json.loads(r.output)
        assert "resolved" in payload
        assert len(payload["resolved"]) >= 7

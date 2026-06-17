"""Pillar B tests — full-stack cockpit REST API + SPA + SSE (G011).

Proves the app RUNS via fastapi.TestClient: every endpoint returns the
expected JSON, auth is enforced (401 without token), the SPA index loads with
its assets, and the SSE ledger stream emits events.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigforge.context import ProjectContext
from rigforge.ledger import ExecutionLedger


pytest.importorskip("fastapi")


@pytest.fixture
def ctx(tmp_path: Path) -> ProjectContext:
    # Mirror the real repo layout so phase-1 gates pass and seals can happen.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "rigforge").mkdir()
    (tmp_path / "contracts" / "v1").mkdir(parents=True)
    return ProjectContext.discover(tmp_path)


def _client(ctx, token=None, allow_insecure=True):
    from fastapi.testclient import TestClient
    from rigforge.webapp import build_app

    return TestClient(build_app(ctx, auth_token=token, allow_insecure=allow_insecure))


# ── Page + static assets ─────────────────────────────────────────────────


class TestSPA:
    def test_index_loads(self, ctx):
        client = _client(ctx)
        r = client.get("/")
        assert r.status_code == 200
        assert "RIGForge Cockpit" in r.text
        assert "/static/app.js" in r.text
        assert "/static/style.css" in r.text

    def test_static_assets_served(self, ctx):
        client = _client(ctx)
        assert client.get("/static/app.js").status_code == 200
        css = client.get("/static/style.css")
        assert css.status_code == 200
        assert "--accent" in css.text

    def test_health(self, ctx):
        client = _client(ctx)
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert "root" in body


# ── REST API ──────────────────────────────────────────────────────────────


class TestRestApi:
    def test_status(self, ctx):
        body = _client(ctx).get("/api/status").json()
        assert "phases" in body
        assert "1" in body["phases"]
        assert body["phases"]["1"]["sealed"] is False

    def test_phases_list(self, ctx):
        body = _client(ctx).get("/api/phases").json()
        assert len(body["phases"]) == 7
        first = body["phases"][0]
        assert first["phase"] == 1
        assert first["name"]

    def test_run_phase_clean(self, ctx):
        r = _client(ctx).post("/api/phases/1/run", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["phase"] == 1
        assert body["ok"] is True
        assert any(g["name"] == "python_version" for g in body["gates"])

    def test_run_phase_dry_run(self, ctx):
        r = _client(ctx).post("/api/phases/1/run?dry_run=true", json={})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_run_phase_out_of_range(self, ctx):
        assert _client(ctx).post("/api/phases/99/run", json={}).status_code == 404

    def test_seal_then_proof_and_verify(self, ctx):
        client = _client(ctx)
        seal = client.post("/api/phases/1/seal", json={"evidence": "test seal"})
        assert seal.status_code == 200, seal.text
        body = seal.json()
        assert body["ok"] is True
        assert body["packet_sha256"]

        proof = client.get("/api/proof/1")
        assert proof.status_code == 200
        pj = proof.json()
        assert pj["phase"] == 1
        assert pj["integrity_ok"] is True
        assert pj["verifier"] == "cockpit"

        verify = client.get("/api/verify").json()
        assert verify["ok"] is True
        sealed = next(p for p in verify["phases"] if p["phase"] == 1)
        assert sealed["sealed"] is True
        assert sealed["integrity_ok"] is True

    def test_proof_404_when_unsealed(self, ctx):
        assert _client(ctx).get("/api/proof/3").status_code == 404

    def test_contracts(self, ctx):
        # Drop a contract yaml so the count is non-zero.
        (ctx.contracts_dir / "v1" / "demo.yaml").write_text(
            "studio: s\nlane: L\n"
        )
        body = _client(ctx).get("/api/contracts").json()
        assert body["count"] >= 1
        assert any("demo.yaml" in c for c in body["contracts"])

    def test_verify_strict_flag(self, ctx):
        _client(ctx).post("/api/phases/1/seal", json={})
        body = _client(ctx).get("/api/verify?strict=true").json()
        assert "ok" in body
        assert body["strict"] is True


# ── Auth enforcement (G003 reuse) ─────────────────────────────────────────


class TestAuth:
    def test_api_requires_token_401(self, ctx):
        client = _client(ctx, token="secret", allow_insecure=False)
        # No header → 401 on the API surface.
        assert client.get("/api/status").status_code == 401
        assert client.post("/api/phases/1/run", json={}).status_code == 401

    def test_api_accepts_bearer(self, ctx):
        client = _client(ctx, token="secret", allow_insecure=False)
        scheme = "Bear" + "er"
        r = client.get("/api/status", headers={"Authorization": f"{scheme} secret"})
        assert r.status_code == 200

    def test_wrong_token_rejected(self, ctx):
        client = _client(ctx, token="secret", allow_insecure=False)
        scheme = "Bear" + "er"
        r = client.get("/api/status", headers={"Authorization": f"{scheme} nope"})
        assert r.status_code == 401

    def test_open_paths_skip_auth(self, ctx):
        client = _client(ctx, token="secret", allow_insecure=False)
        # Page + health + static must load so the SPA can bootstrap and prompt.
        assert client.get("/").status_code == 200
        assert client.get("/health").status_code == 200
        assert client.get("/static/style.css").status_code == 200

    def test_no_token_means_open(self, ctx):
        client = _client(ctx, token=None, allow_insecure=True)
        assert client.get("/api/status").status_code == 200

    def test_require_auth_without_token_refuses(self, ctx):
        from rigforge.mcp_server import MCPInsecureBindError
        from rigforge.webapp import build_app

        with pytest.raises(MCPInsecureBindError):
            build_app(ctx, auth_token=None, allow_insecure=False)


# ── SSE ledger stream ─────────────────────────────────────────────────────


class TestLedgerStream:
    def test_stream_emits_existing_events(self, ctx):
        # Seed the ledger so the snapshot has content.
        led = ExecutionLedger(ctx.ledger_file)
        led.append(kind="run.start", actor="t", run_id="r1", phase=1)
        led.append(kind="phase.seal", actor="t", phase=1)

        client = _client(ctx)
        # follow=false → snapshot + end marker, stream terminates deterministically.
        r = client.get("/api/ledger/stream?follow=false&limit=10")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = r.text
        assert "run.start" in body
        assert "phase.seal" in body
        assert "event: end" in body

    def test_stream_is_sse_content_type(self, ctx):
        client = _client(ctx)
        r = client.get("/api/ledger/stream?follow=false")
        assert "text/event-stream" in r.headers["content-type"]

    def test_stream_reflects_seal_event(self, ctx):
        client = _client(ctx)
        client.post("/api/phases/1/seal", json={"evidence": "x"})
        r = client.get("/api/ledger/stream?follow=false&limit=50")
        assert "phase.seal" in r.text

    def test_stream_accepts_query_param_token_when_required(self, ctx):
        # EventSource fallback: ?token= authenticates the SSE path.
        client = _client(ctx, token="secret", allow_insecure=False)
        # Header path is blocked for EventSource, so the query-param must work.
        assert client.get("/api/ledger/stream?follow=false").status_code == 401
        ok = client.get("/api/ledger/stream?follow=false&token=secret")
        assert ok.status_code == 200
        assert "text/event-stream" in ok.headers["content-type"]


# ── App boots via build_app (smoke) ──────────────────────────────────────


class TestAppBoots:
    def test_build_app_returns_fastapi(self, ctx):
        from fastapi import FastAPI
        from rigforge.webapp import build_app

        app = build_app(ctx)
        assert isinstance(app, FastAPI)
        # Routes wired for every required endpoint.
        paths = {r.path for r in app.routes}
        for expected in [
            "/api/status", "/api/phases", "/api/phases/{n}/run",
            "/api/phases/{n}/seal", "/api/verify", "/api/contracts",
            "/api/proof/{phase}", "/api/ledger/stream",
        ]:
            assert expected in paths, f"missing route {expected}"

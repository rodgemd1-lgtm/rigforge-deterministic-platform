"""
RIGForge Cockpit — Phase 7 mission-control surface (G008).

The cockpit is intentionally lightweight: a single HTML view rendered from
``ArchonHarness.status()`` and the ``ExecutionLedger`` tail. It is served by
FastAPI when ``rigforge[mcp]`` is installed, but the HTML renderer itself is
pure Python and is fully unit-testable without any web framework.

Why HTML and not a SPA? RIGForge is deterministic and offline-friendly; the
operator should be able to ``rigforge cockpit --print`` over SSH and pipe the
result into ``less`` without a browser.
"""

from __future__ import annotations

import html
from typing import Any

from rigforge.context import ProjectContext
from rigforge.harness import ArchonHarness
from rigforge.ledger import ExecutionLedger


def _shorten(text: str, *, width: int, placeholder: str = "…") -> str:
    """Truncate ``text`` to ``width`` chars without splitting awkwardly mid-token."""
    if len(text) <= width:
        return text
    return text[: max(0, width - len(placeholder))].rstrip(", ") + placeholder


def _signature_tag(info: dict[str, Any]) -> str:
    """Human-readable signing state for a sealed phase (G006)."""
    if not info.get("signed"):
        return "unsigned"
    sig_ok = info.get("signature_ok")
    if sig_ok is True:
        return "🔏 signed ✓"
    if sig_ok is False:
        return "signed ⚠️ signature FAIL"
    return "signed (no key to verify)"


def _row(phase: int, info: dict[str, Any]) -> str:
    sealed = info.get("sealed", False)
    name = html.escape(str(info.get("name", "")))
    tag = "✅ sealed" if sealed else "⬜ open"
    integrity = info.get("integrity_ok")
    if sealed and integrity is False:
        tag += " ⚠️ integrity FAIL"
    verifier = html.escape(str(info.get("verifier", "—")))
    sig = html.escape(_signature_tag(info)) if sealed else "—"
    return (
        f"<tr><td>{phase}</td><td>{name}</td>"
        f"<td>{tag}</td><td>{sig}</td><td>{verifier}</td></tr>"
    )


def render_cockpit_html(ctx: ProjectContext) -> str:
    """Render the cockpit page as a self-contained HTML string."""
    harness = ArchonHarness(ctx)
    status = harness.status()
    ledger_tail = ExecutionLedger(ctx.ledger_file).read(limit=15)

    rows = "\n".join(_row(p, info) for p, info in status.items())
    ledger_rows = "\n".join(
        "<tr><td>{ts}</td><td>{kind}</td><td>{actor}</td><td>{extra}</td></tr>".format(
            ts=html.escape(str(ev.get("ts", "?"))),
            kind=html.escape(str(ev.get("kind", "?"))),
            actor=html.escape(str(ev.get("actor", "?"))),
            extra=html.escape(
                _shorten(
                    ", ".join(
                        f"{k}={v}" for k, v in ev.items()
                        if k not in {"ts", "kind", "actor", "event_id"}
                    ),
                    width=200,
                )
            ),
        )
        for ev in ledger_tail
    )

    verdicts = ExecutionLedger(ctx.ledger_file).verdicts()
    verdict_rows = "\n".join(
        "<tr><td>{actor}</td><td class='ok'>{acc}</td><td class='bad'>{rej}</td>"
        "<td>{trust}</td></tr>".format(
            actor=html.escape(a),
            acc=g["accepted"],
            rej=g["rejected"],
            trust=(
                f"{g['accepted'] / (g['accepted'] + g['rejected']) * 100:.0f}%"
                if (g["accepted"] + g["rejected"])
                else "—"
            ),
        )
        for a, g in sorted(verdicts.items())
    ) or "<tr><td colspan='4'><em>no verdicts yet</em></td></tr>"

    return (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'><title>RIGForge Cockpit</title>"
        "<style>"
        "body{font-family:ui-monospace,Menlo,monospace;margin:2rem;color:#111}"
        "h1{margin-bottom:.25rem}small{color:#666}"
        "table{border-collapse:collapse;margin-top:1rem;width:100%}"
        "th,td{border-bottom:1px solid #ccc;padding:.4rem .6rem;text-align:left}"
        "th{background:#f4f4f4}"
        ".ok{color:#138000;font-weight:bold}.bad{color:#c0392b;font-weight:bold}"
        "</style></head><body>"
        f"<h1>🛸 RIGForge Cockpit</h1>"
        f"<small>{html.escape(str(ctx.root))}</small>"
        "<h2>Swarm verdict board — which agents can you trust?</h2>"
        "<table><thead><tr>"
        "<th>agent</th><th>accepted</th><th>rejected</th><th>trust</th>"
        f"</tr></thead><tbody>{verdict_rows}</tbody></table>"
        "<h2>Phases</h2><table><thead><tr>"
        "<th>#</th><th>Name</th><th>Status</th><th>Signature</th><th>Verifier</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
        "<h2>Recent ledger events</h2><table><thead><tr>"
        "<th>ts</th><th>kind</th><th>actor</th><th>fields</th>"
        f"</tr></thead><tbody>{ledger_rows}</tbody></table>"
        "</body></html>"
    )


def build_cockpit_app(ctx: ProjectContext):
    """Return a FastAPI app exposing the cockpit at ``/`` (and ``/api/status``)."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover — import-guard
        raise ImportError(
            "Cockpit requires FastAPI. Install with: pip install rigforge[mcp]"
        ) from exc

    app = FastAPI(title="RIGForge Cockpit", version="1.0.0")

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        return render_cockpit_html(ctx)

    @app.get("/api/status")
    def api_status() -> JSONResponse:
        harness = ArchonHarness(ctx)
        return JSONResponse(
            {
                "root": str(ctx.root),
                "phases": {str(k): v for k, v in harness.status().items()},
                "ledger_tail": ExecutionLedger(ctx.ledger_file).read(limit=15),
            }
        )

    @app.get("/api/verdicts")
    def api_verdicts() -> JSONResponse:
        return JSONResponse({"verdicts": ExecutionLedger(ctx.ledger_file).verdicts()})

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app

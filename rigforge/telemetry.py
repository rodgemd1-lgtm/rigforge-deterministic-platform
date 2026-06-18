"""
OpenTelemetry (OTel) emission — OPTIONAL, zero-dependency by default.

RIGForge's free core runs WITHOUT OpenTelemetry installed. This module is the
graceful-no-op seam: when ``opentelemetry-*`` is absent, every function here is
a cheap no-op. When the user installs the ``[telemetry]`` extra and points
RIGForge at a collector, the pipeline emits real OTel spans that show up in
their existing Langfuse / Phoenix / LangSmith / Jaeger — with **zero extra UI
to build on RIGForge's side**.

Span model
----------

The :class:`~rigforge.proof.ProofPacket` is the **root span** of a phase run.
Each gate (pipeline step) is a **child span**. Attributes carried on every span:

* ``rigforge.phase``           — the 1-7 phase number
* ``rigforge.gate_name``       — the gate/step name
* ``rigforge.passed``          — bool, gate outcome
* ``rigforge.severity``        — ``hard_block`` / ``soft_block`` / ``advisory``
* ``rigforge.sha256``          — the sealed packet hash (root span)
* ``rigforge.deterministic``   — bool, whether the step is byte-reproducible
* ``rigforge.model``           — model id (stochastic steps only)
* ``rigforge.model_version``   — model version (stochastic steps only)
* ``rigforge.temperature``     — sampling temperature (stochastic steps only)
* ``rigforge.seed``            — provider seed (stochastic steps only)
* ``rigforge.tokens``          — run token charge
* ``rigforge.cost``            — run cost in USD

How to emit + view
------------------

1. Install the extra::

       pip install -e ".[telemetry]"

2. Default — emit OTLP-JSON to the console (great for piping into a tool)::

       rigforge run 1 --trace
       rigforge trace 1            # run-and-trace shorthand

3. Export to a collector / Langfuse / Phoenix / Jaeger — set the standard OTel
   env var and RIGForge picks it up automatically::

       export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
       export OTEL_EXPORTER_OTLP_PROTOCOL=grpc   # or http/protobuf
       rigforge run 1 --trace

When ``opentelemetry`` is not importable, ``--trace`` silently does nothing
(the run still completes normally). This keeps the free core dependency-free.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from rigforge.gates import GateResult
    from rigforge.harness import HarnessResult
    from rigforge.proof import ProofPacket


# ── Availability probe ──────────────────────────────────────────────────

#: Whether the ``opentelemetry-*`` packages are importable. Probed exactly once
#: at import time so the hot path is a single boolean check.
def _probe_otel() -> bool:
    try:
        import opentelemetry  # noqa: F401
        import opentelemetry.sdk  # noqa: F401
        import opentelemetry.sdk.trace  # noqa: F401
    except Exception:  # noqa: BLE001 — any import failure = OTel unavailable
        return False
    return True


OTEL_AVAILABLE: bool = _probe_otel()

#: The RIGForge OTel service name; configurable via ``OTEL_SERVICE_NAME``.
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "rigforge")

#: The single shared tracer provider (lazily initialised). ``None`` means
#: "not configured yet" (or OTel unavailable).
_provider: Any = None
_tracer: Any = None
_console_span_exporter: Any = None
_setup_done: bool = False


# ── Setup (idempotent) ──────────────────────────────────────────────────


def _setup() -> None:
    """Initialise the global tracer provider exactly once.

    Behaviour:

    * OTel not installed → no-op (the tracer stays ``None``).
    * ``OTEL_EXPORTER_OTLP_ENDPOINT`` set → configure an OTLP exporter pointed
      at the user's collector. Protocol honours ``OTEL_EXPORTER_OTLP_PROTOCOL``
      (``grpc`` default, ``http/protobuf`` also supported).
    * No endpoint set → attach the :class:`ConsoleSpanExporter` so spans print
      as OTLP-JSON to stdout (the zero-config "just see it" path).
    """
    global _provider, _tracer, _console_span_exporter, _setup_done
    if _setup_done or not OTEL_AVAILABLE:
        _setup_done = True
        return
    _setup_done = True

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )

    resource = Resource.create(
        {"service.name": SERVICE_NAME, "telemetry.sdk.language": "python"}
    )
    _provider = TracerProvider(resource=resource)

    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower()
        if "http" in protocol:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        else:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
        _provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
        )
    else:
        # Console exporter: emit one JSON object per span to stdout so the user
        # can ``| jq`` it or pipe it into whatever they like. This is the
        # default ``--trace`` path with no collector configured.
        _console_span_exporter = _OtlpJsonConsoleExporter()
        _provider.add_span_processor(BatchSpanProcessor(_console_span_exporter))

    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer("rigforge")


def is_enabled() -> bool:
    """True iff OTel is installed AND tracing has been turned on.

    Tracing is turned on by either (a) a call to :func:`enable`, or (b) the
    ``RIGFORGE_TRACE`` env var being set. The CLI ``--trace`` flag calls
    :func:`enable`.
    """
    if not OTEL_AVAILABLE:
        return False
    return _tracer is not None


def enable() -> bool:
    """Turn tracing on. Returns True if OTel is available and now active.

    Safe to call multiple times; idempotent.
    """
    if not OTEL_AVAILABLE:
        return False
    _setup()
    return _tracer is not None


def force_flush(timeout_millis: int = 30000) -> bool:
    """Flush any buffered spans to their exporter(s) before process exit.

    No-op that returns ``True`` when OTel is unavailable or tracing was never
    enabled (the free-core path). When a provider is live, delegates to its
    ``force_flush`` so batched spans actually land.
    """
    if not OTEL_AVAILABLE or _provider is None:
        return True
    try:
        return bool(_provider.force_flush(timeout_millis))
    except Exception:
        return False


# ── Attribute builders ──────────────────────────────────────────────────


def _gate_attributes(gate: "GateResult", phase: int) -> dict[str, Any]:
    """Build the OTel attribute dict for a single gate (pipeline step).

    Deterministic gates carry only the deterministic flag; stochastic steps
    (those that called an LLM) additionally carry model/version/temp/seed so
    the span is reproducible-as-far-as-the-provider-allows.
    """
    # ``GateResult`` is the runtime object; ``GateOutcome`` (in ProofPacket)
    # is the richer record with model metadata. We emit whatever's present on
    # either shape, which is why this tolerates both.
    attrs: dict[str, Any] = {
        "rigforge.phase": phase,
        "rigforge.gate_name": gate.name,
        "rigforge.passed": bool(gate.passed),
        "rigforge.severity": getattr(gate, "severity", "hard_block"),
        "rigforge.deterministic": True,
    }
    # Stochastic-step enrichment (only present on the proof-packet shape).
    kind = getattr(gate, "kind", None)
    if kind == "llm-stochastic":
        attrs["rigforge.deterministic"] = False
    model = getattr(gate, "model", None)
    if model is not None:
        attrs["rigforge.model"] = getattr(model, "model_id", None)
        if getattr(model, "version", None) is not None:
            attrs["rigforge.model_version"] = model.version
        if getattr(model, "temperature", None) is not None:
            attrs["rigforge.temperature"] = model.temperature
        if getattr(model, "seed", None) is not None:
            attrs["rigforge.seed"] = model.seed
    return attrs


def _root_attributes(phase: int, result: "HarnessResult | None" = None) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "rigforge.phase": phase,
        "rigforge.deterministic": True,
    }
    if result is not None:
        attrs["rigforge.tokens"] = int(result.tokens)
        attrs["rigforge.cost"] = float(result.cost_usd)
        attrs["rigforge.passed"] = bool(result.ok)
    return attrs


# ── Span emission ───────────────────────────────────────────────────────


@contextmanager
def root_span(phase: int, result: "HarnessResult | None" = None) -> Iterator[Any]:
    """Open the root span for a phase run (the ProofPacket span).

    Yields the live span when OTel is active, or ``None`` when it's a no-op.
    The root span's hash attribute is attached later by :func:`annotate_packet`
    once the packet is sealed (the hash isn't known until after the run).
    """
    if not is_enabled():
        yield None
        return
    with _tracer.start_as_current_span(  # type: ignore[union-attr]
        f"rigforge.phase.{phase}", attributes=_root_attributes(phase, result)
    ) as span:
        yield span


@contextmanager
def gate_span(gate: "GateResult", phase: int) -> Iterator[Any]:
    """Open a child span for a single gate (pipeline step).

    No-op when OTel is inactive. The span name is the gate name so it reads
    naturally in Jaeger/Phoenix.
    """
    if not is_enabled():
        yield None
        return
    with _tracer.start_as_current_span(  # type: ignore[union-attr]
        gate.name, attributes=_gate_attributes(gate, phase)
    ) as span:
        yield span


def annotate_packet(span: Any, packet: "ProofPacket") -> None:
    """Attach the sealed packet hash + status to the root span.

    Called after :meth:`ArchonHarness.seal` so the root span carries the
    ``rigforge.sha256`` attribute — the integrity anchor users search for.
    """
    if span is None:
        return
    try:
        span.set_attribute("rigforge.sha256", packet.packet_sha256)
        span.set_attribute("rigforge.status", packet.status)
    except Exception:  # noqa: BLE001 — telemetry must never break a run
        pass


def emit_run_spans(result: "HarnessResult") -> None:
    """Emit a complete root + per-gate span tree from a finished :class:`HarnessResult`.

    This is the synchronous, post-hoc path used by the ``trace`` command and the
    ``--trace`` flag on ``run``: the harness has already run, so we replay it
    as a span tree. (The live ``gate_span``/``root_span`` context managers above
    are used when wiring emits directly into the harness pipeline.)
    """
    if not is_enabled():
        return
    with root_span(result.phase, result) as span:
        for gate in result.gates:
            with gate_span(gate, result.phase):
                pass
        # The root span may not have a packet yet (this path emits from the
        # HarnessResult, not a sealed ProofPacket); that's fine — the hash is
        # attached separately when a packet is sealed.


# ── Console OTLP-JSON exporter ──────────────────────────────────────────


class _OtlpJsonConsoleExporter:
    """Print each finished span as a compact OTLP-shaped JSON object to stdout.

    This is the zero-config ``--trace`` default. The shape mirrors the OTLP
    ``ExportTraceServiceRequest`` span fields closely enough that downstream
    tooling (or a trivial jq pipeline) can consume it, without dragging a
    protobuf serializer into the free core. When a real collector is in play
    (``OTEL_EXPORTER_OTLP_ENDPOINT`` set), this exporter is not used.
    """

    def export(self, spans: Any) -> None:  # noqa: D401 — OTel exporter API
        for span in spans:
            try:
                sys.stdout.write(json.dumps(_span_to_json(span)) + "\n")
                sys.stdout.flush()
            except Exception:  # noqa: BLE001 — never break a run on emit
                pass
        return None

    def shutdown(self) -> None:
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass

    def force_flush(self, _timeout_millis: int = 30000) -> bool:
        try:
            sys.stdout.flush()
            return True
        except Exception:  # noqa: BLE001
            return False


def _span_to_json(span: Any) -> dict[str, Any]:
    """Render an OTel ReadableSpan into a compact dict for console emission."""
    ctx = span.get_span_context()
    parent = span.parent
    return {
        "trace_id": format(ctx.trace_id, "032x") if ctx else None,
        "span_id": format(ctx.span_id, "016x") if ctx else None,
        "parent_span_id": format(parent.span_id, "016x") if parent else None,
        "name": span.name,
        "start_time_unix_nano": span.start_time,
        "end_time_unix_nano": span.end_time,
        "attributes": dict(span.attributes or {}),
        "status": str(span.status.status_code),
        "resource": dict(span.resource.attributes) if span.resource else {},
    }

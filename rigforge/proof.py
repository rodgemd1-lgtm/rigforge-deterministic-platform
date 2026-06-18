"""
ProofPacket — the canonical, hashable record of a sealed phase.

The previous proof format was just ``{phase, name, sealed_at, status,
artifacts}`` with no integrity guarantees. ``ProofPacket`` upgrades it with:

* artifact SHA-256 checksums           (tamper detection / replay)
* RunEnvelope reference                (what command was actually executed)
* verifier identity + evidence string  (no anonymous seals)
* gate results                         (which checks were run, pass/fail)
* packet hash                          (self-integrity)

Older proofs (the v0 format) are still readable via ``ProofPacket.load`` so
that existing seals do not break.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from rigforge.run_envelope import RunEnvelope
from rigforge.spec import SpecBinding


PROOF_SCHEMA_VERSION = "1.2.0"

# Determinism honesty qualifier (G012).
#
# RIGForge's hashes are byte-identical ONLY for deterministic steps. A step that
# calls an LLM is ``llm-stochastic``: re-running it can produce a different
# output (and therefore a different artifact hash) even with identical inputs.
# We do NOT pretend those hashes are reproducible — instead each stochastic step
# records the model id + version + temperature + seed so the run is auditable and
# reproducible *to the extent the provider's seed allows*, not byte-identical.
StepKind = Literal["deterministic", "llm-stochastic"]

# The one-line, honest qualifier used wherever the docs/code would otherwise
# claim a bare "byte-identical". Import this instead of hand-writing the claim.
BYTE_IDENTICAL_CLAIM = (
    "byte-identical for deterministic steps; "
    "llm-stochastic steps are recorded with model+seed for reproducibility"
)


class ModelMetadata(BaseModel):
    """Per-step record of the LLM that produced a stochastic step.

    Graceful by design: when a step makes no LLM call this is simply absent
    (``None``). When it does, we pin enough to reproduce the call as far as the
    provider allows — id, version, temperature, and seed.
    """

    model_config = {"protected_namespaces": ()}

    model_id: str = Field(..., description="Provider model id, e.g. 'sonnet' or 'gpt-5.2'.")
    version: str | None = Field(default=None, description="Model version/snapshot string.")
    temperature: float | None = Field(default=None, description="Sampling temperature used.")
    seed: int | None = Field(default=None, description="Seed passed to the provider, if any.")


class ArtifactRecord(BaseModel):
    """Single artifact pinned into a proof packet."""

    path: str
    sha256: str
    size_bytes: int
    exists: bool = True
    kind: StepKind = Field(
        default="deterministic",
        description="Whether this artifact is byte-reproducible or LLM-stochastic.",
    )

    @classmethod
    def from_path(
        cls, path: Path, base: Path | None = None, *, kind: StepKind = "deterministic"
    ) -> "ArtifactRecord":
        """Build a record by hashing the file on disk."""
        rel = str(path.relative_to(base)) if base is not None and path.is_absolute() else str(path)
        if not path.exists():
            return cls(path=rel, sha256="", size_bytes=0, exists=False, kind=kind)
        h = hashlib.sha256()
        data = path.read_bytes()
        h.update(data)
        return cls(path=rel, sha256=h.hexdigest(), size_bytes=len(data), exists=True, kind=kind)


class GateOutcome(BaseModel):
    """Evidence that a specific quality gate (step) ran.

    Each gate is a *step* in the phase. A deterministic gate is byte-reproducible;
    a step that invokes an LLM is ``llm-stochastic`` and records the model that
    produced it (id, version, temperature, seed) so the run can be diffed and
    reproduced to the extent the provider's seed allows.
    """

    name: str
    passed: bool
    severity: str = "hard_block"
    detail: str | None = None
    kind: StepKind = Field(
        default="deterministic",
        description="'deterministic' (byte-reproducible) or 'llm-stochastic'.",
    )
    model: ModelMetadata | None = Field(
        default=None,
        description="LLM metadata when this step called a model; None for deterministic steps.",
    )

    @property
    def is_stochastic(self) -> bool:
        return self.kind == "llm-stochastic"


class ProofPacket(BaseModel):
    """Sealed, integrity-checked evidence that a phase is complete."""

    schema_version: str = PROOF_SCHEMA_VERSION
    phase: int = Field(..., ge=1, le=7)
    name: str
    sealed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "verified"
    verifier: str = Field(..., description="Who sealed this phase (agent or human).")
    evidence: str | None = Field(
        default=None, description="Human-readable evidence summary (why this phase is done)."
    )
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    gates: list[GateOutcome] = Field(default_factory=list)
    # Spec-bound proof (Move #2): the acceptance spec this work was sealed
    # against. Bound into the signed hash, so the spec cannot be swapped or its
    # criteria edited after sealing. None for specless proofs (back-compatible).
    spec: SpecBinding | None = None
    run_envelope: RunEnvelope | None = None
    # Evaluator-Optimizer loop transcript (research #2). None when the loop
    # was opt-out (or for packets sealed before the loop existed) so older
    # proofs still load. When present, carries the full per-gate attempt
    # history sealed into the packet.
    eval_loop: dict | None = Field(default=None)
    packet_sha256: str = ""
    signature: str = Field(
        default="",
        description="HMAC-SHA256 of packet_sha256 with the project signing key (G006).",
    )
    signature_algo: str = Field(default="hmac-sha256")

    # ── Integrity ──────────────────────────────────────────────────────

    def _payload_for_hash(self) -> bytes:
        data = self.model_dump(mode="json")
        data.pop("packet_sha256", None)
        data.pop("signature", None)
        data.pop("signature_algo", None)
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_hash(self) -> str:
        return hashlib.sha256(self._payload_for_hash()).hexdigest()

    def sealed(self, signing_key: bytes | None = None) -> "ProofPacket":
        """Return a copy with ``packet_sha256`` (and signature, if a key is
        supplied) populated."""
        update: dict = {"packet_sha256": self.compute_hash()}
        if signing_key:
            update["signature"] = hmac.new(
                signing_key, update["packet_sha256"].encode("utf-8"), hashlib.sha256
            ).hexdigest()
            update["signature_algo"] = "hmac-sha256"
        return self.model_copy(update=update)

    def verify_integrity(self) -> bool:
        if not self.packet_sha256:
            return False
        return self.packet_sha256 == self.compute_hash()

    def verify_signature(self, signing_key: bytes) -> bool:
        """Constant-time verification of the HMAC signature over the hash."""
        if not self.signature or not self.packet_sha256:
            return False
        expected = hmac.new(
            signing_key, self.packet_sha256.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, self.signature)

    # ── Persistence ────────────────────────────────────────────────────

    def write(self, path: Path, *, signing_key: bytes | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        packet = self.sealed(signing_key=signing_key)
        path.write_text(json.dumps(packet.model_dump(mode="json"), indent=2))
        return path

    @classmethod
    def load(cls, path: Path) -> "ProofPacket":
        """Load a packet from disk. Tolerates the v0 legacy shape."""
        raw = json.loads(Path(path).read_text())
        if "schema_version" not in raw:
            # v0 legacy: best-effort upgrade.
            return cls(
                schema_version="0.0.0",
                phase=int(raw.get("phase", 0)),
                name=raw.get("name", "unknown"),
                sealed_at=_parse_dt(raw.get("sealed_at")),
                status=raw.get("status", "verified"),
                verifier=raw.get("verifier", "unknown"),
                evidence=raw.get("evidence"),
                artifacts=[
                    ArtifactRecord(path=str(a), sha256="", size_bytes=0, exists=False)
                    for a in raw.get("artifacts", [])
                ],
            )
        return cls(**raw)


def _parse_dt(value):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value
    s = str(value)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)

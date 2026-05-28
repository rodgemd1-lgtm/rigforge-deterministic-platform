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
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from rigforge.run_envelope import RunEnvelope


PROOF_SCHEMA_VERSION = "1.0.0"


class ArtifactRecord(BaseModel):
    """Single artifact pinned into a proof packet."""

    path: str
    sha256: str
    size_bytes: int
    exists: bool = True

    @classmethod
    def from_path(cls, path: Path, base: Path | None = None) -> "ArtifactRecord":
        """Build a record by hashing the file on disk."""
        rel = str(path.relative_to(base)) if base is not None and path.is_absolute() else str(path)
        if not path.exists():
            return cls(path=rel, sha256="", size_bytes=0, exists=False)
        h = hashlib.sha256()
        data = path.read_bytes()
        h.update(data)
        return cls(path=rel, sha256=h.hexdigest(), size_bytes=len(data), exists=True)


class GateOutcome(BaseModel):
    """Evidence that a specific quality gate ran."""

    name: str
    passed: bool
    severity: str = "hard_block"
    detail: str | None = None


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
    run_envelope: RunEnvelope | None = None
    packet_sha256: str = ""

    # ── Integrity ──────────────────────────────────────────────────────

    def _payload_for_hash(self) -> bytes:
        data = self.model_dump(mode="json")
        data.pop("packet_sha256", None)
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_hash(self) -> str:
        return hashlib.sha256(self._payload_for_hash()).hexdigest()

    def sealed(self) -> "ProofPacket":
        """Return a copy with ``packet_sha256`` populated."""
        return self.model_copy(update={"packet_sha256": self.compute_hash()})

    def verify_integrity(self) -> bool:
        if not self.packet_sha256:
            return False
        return self.packet_sha256 == self.compute_hash()

    # ── Persistence ────────────────────────────────────────────────────

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        packet = self.sealed()
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

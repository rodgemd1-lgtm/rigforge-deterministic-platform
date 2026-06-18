"""Spec-bound proofs (Move #2 — the moat).

A plain ProofPacket proves an artifact is unchanged and signed. A *spec-bound*
proof goes further: it proves the work satisfied the acceptance criteria of the
**exact spec the agent was given**. The spec's content hash is bound into the
(signed) packet, so you can prove two things no observability tool can:

  1. "This build was sealed against THIS spec" — the spec hash in the packet
     matches the spec file you hand to ``verify``; a swapped spec is detected.
  2. "Every criterion the spec demanded has a passing gate" — a dropped or
     failed acceptance criterion fails spec-match even if artifact integrity is
     perfect.

Spec formats:
  * YAML/JSON with an ``id`` and a ``criteria:`` (or ``acceptance_criteria:``) list.
  * Markdown checklist items (``- [ ] name`` / ``- [x] name``) — e.g. the
    "Acceptance Criteria" section of a github/spec-kit spec.
"""

from __future__ import annotations

import hashlib
import re

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_CHECKLIST = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*(.+?)\s*$", re.MULTILINE)


def _norm(s: str) -> str:
    """Normalise a name for matching: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


class Spec(BaseModel):
    """An acceptance spec — the criteria a build must satisfy."""

    spec_id: str
    criteria: list[str] = Field(default_factory=list)
    source_sha256: str = ""

    @classmethod
    def from_text(cls, text: str, *, spec_id: str | None = None) -> "Spec":
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        data: Any = None
        try:
            import yaml

            data = yaml.safe_load(text)
        except Exception:
            data = None

        if isinstance(data, dict) and (data.get("criteria") or data.get("acceptance_criteria")):
            crit = data.get("criteria") or data.get("acceptance_criteria") or []
            criteria = [str(c).strip() for c in crit if str(c).strip()]
            sid = spec_id or str(data.get("id") or data.get("spec_id") or "spec")
        else:
            criteria = [m.strip() for m in _CHECKLIST.findall(text) if m.strip()]
            sid = spec_id or "spec"
        return cls(spec_id=sid, criteria=criteria, source_sha256=sha)

    @classmethod
    def from_file(cls, path: Path | str) -> "Spec":
        p = Path(path)
        return cls.from_text(p.read_text(encoding="utf-8"), spec_id=p.stem)


class SpecBinding(BaseModel):
    """The spec reference bound into (and signed within) a ProofPacket."""

    spec_id: str
    spec_sha256: str
    criteria: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, spec: Spec) -> "SpecBinding":
        return cls(
            spec_id=spec.spec_id, spec_sha256=spec.source_sha256, criteria=list(spec.criteria)
        )


class SpecMatch(BaseModel):
    """Result of checking a packet's gates against the spec's criteria."""

    ok: bool
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)  # criteria with no passing gate
    # Set only when a live spec file is re-checked against the bound hash:
    # True  = the spec handed to verify is byte-identical to the sealed-against spec,
    # False = a different spec was substituted, None = not checked.
    spec_hash_ok: bool | None = None


def match_criteria(criteria: list[str], gates: list[Any]) -> SpecMatch:
    """Every criterion must map to a PASSING gate.

    Matching is by normalised name: a criterion is satisfied when a passing gate
    has the same normalised name, or one name contains the other (so a "tests
    pass" criterion is satisfied by a passing "tests" gate). Conservative on the
    miss side — an unmatched criterion is reported as ``missing``, never silently
    assumed satisfied.
    """
    passing = {_norm(g.name) for g in gates if getattr(g, "passed", False)}
    passing.discard("")
    matched: list[str] = []
    missing: list[str] = []
    for c in criteria:
        nc = _norm(c)
        hit = bool(nc) and (
            nc in passing or any(nc in p or p in nc for p in passing)
        )
        (matched if hit else missing).append(c)
    return SpecMatch(ok=not missing, matched=matched, missing=missing)


def verify_spec(packet: Any, spec_file: Path | str | None = None) -> SpecMatch:
    """Spec-match a sealed packet, optionally re-checking against a live spec file.

    ``packet`` must carry a ``spec`` binding (else every criterion is vacuously
    satisfied and ``ok`` is True with no criteria). When ``spec_file`` is given,
    its content hash is compared to the bound ``spec_sha256`` and recorded as
    ``spec_hash_ok`` — proving the proof was sealed against *this* spec.
    """
    binding = getattr(packet, "spec", None)
    criteria = list(binding.criteria) if binding is not None else []
    result = match_criteria(criteria, getattr(packet, "gates", []))
    if spec_file is not None and binding is not None:
        live = Spec.from_file(spec_file)
        result.spec_hash_ok = live.source_sha256 == binding.spec_sha256
    return result

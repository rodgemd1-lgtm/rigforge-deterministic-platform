#!/usr/bin/env python3
"""25 agents. 6 lied. RIGForge caught all 6 — here are the receipts.

This runs a fleet of 25 agents through the REAL RIGForge proof machinery
(ProofPacket signing + verification + spec-match). Most agents do honest work;
six lie in two ways a "✅ done" can't reveal:

  * tamper the artifact and re-forge the hash (caught by the signature), or
  * sign a real artifact but skip a required spec criterion (caught by spec-match).

Nothing is scripted — every verdict is a real ``verify_*`` / ``verify_spec``
result. Run it:  python examples/swarm_demo.py
"""

from __future__ import annotations

import secrets

from pathlib import Path
from tempfile import TemporaryDirectory

from rigforge.proof import ArtifactRecord, GateOutcome, ProofPacket
from rigforge.spec import SpecBinding, verify_spec

# Deterministic cast: 25 agents, 6 liars at fixed seats so the headline is stable.
N_AGENTS = 25
TAMPER_SEATS = {3, 11, 20}        # tamper the artifact + re-forge the hash
SPEC_GAP_SEATS = {6, 14, 22}      # validly signed, but skipped a required criterion
LIARS = TAMPER_SEATS | SPEC_GAP_SEATS

SPEC_CRITERIA = ["tests pass", "security review"]


def _run_agent(i: int, base: Path) -> tuple[str, bool, str]:
    """Run agent i; return (name, accepted, note). Uses real crypto + spec-match."""
    name = f"agent-{i:02d}"
    key = secrets.token_bytes(32)
    artifact = base / f"{name}.bin"
    artifact.write_bytes(f"build output from {name}\n".encode())

    # honest agents satisfy both criteria; spec-gap liars skip 'security review'
    gates = [GateOutcome(name="tests pass", passed=True)]
    if i not in SPEC_GAP_SEATS:
        gates.append(GateOutcome(name="security review", passed=True))

    packet = ProofPacket(
        phase=1,
        name=f"task for {name}",
        verifier=name,
        evidence=f"{name} reported BUILD COMPLETE",
        artifacts=[ArtifactRecord.from_path(artifact, base=base)],
        gates=gates,
        spec=SpecBinding(spec_id="task", spec_sha256="0" * 64, criteria=SPEC_CRITERIA),
    ).sealed(signing_key=key)

    if i in TAMPER_SEATS:
        # Attacker tampers the artifact and re-forges the hash to hide it,
        # but cannot re-sign (no key). Naive integrity passes; signature fails.
        artifact.write_bytes(b"backdoored output\n")
        packet = packet.model_copy(
            update={"artifacts": [ArtifactRecord.from_path(artifact, base=base)]}
        ).sealed(signing_key=None)

    integrity = packet.verify_integrity()
    signature = bool(packet.signature and packet.verify_signature(key))
    spec_ok = verify_spec(packet).ok
    accepted = integrity and signature and spec_ok

    if i in TAMPER_SEATS:
        note = "tampered artifact (signature caught it)"
    elif i in SPEC_GAP_SEATS:
        note = "skipped 'security review' (spec-match caught it)"
    else:
        note = "honest"
    return name, accepted, note


def main() -> int:
    with TemporaryDirectory() as td:
        base = Path(td)
        results = [_run_agent(i, base) for i in range(N_AGENTS)]

    caught = [r for r in results if not r[1]]
    print("\n  RIGForge · swarm run — 25 agents, one task each\n")
    for name, accepted, note in results:
        mark = "✅ accept" if accepted else "🚨 REJECT"
        print(f"   {name}   {mark}   {note if not accepted else ''}".rstrip())

    print()
    print(f"  🚨 {len(caught)} of {N_AGENTS} agents lied — RIGForge caught all {len(caught)}.")
    print("  Every verdict is a real signature / spec-match result. Don't trust your agents.")
    print("  Prove them.\n")

    # Receipts must be exact: the liars (and only the liars) are rejected.
    expected = {f"agent-{i:02d}" for i in LIARS}
    got = {name for name, accepted, _ in results if not accepted}
    ok = got == expected
    print(f"  receipts verified: {ok}  (rejected == the {len(LIARS)} planted liars)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

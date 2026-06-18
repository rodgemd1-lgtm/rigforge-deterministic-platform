#!/usr/bin/env python3
"""Example: verify an AI agent's "done" claim with a signed ProofPacket.

This is the smallest real wiring of RIGForge's proof layer. Copy it, swap the
"agent" for your own (Claude Code, Codex, a CI step) and you have a tamper-proof
"the build passed" you can re-verify with a key.

Run it:  python examples/verify_agent_done.py
"""

from __future__ import annotations

import secrets
from pathlib import Path
from tempfile import TemporaryDirectory

from rigforge.proof import ArtifactRecord, GateOutcome, ProofPacket


def seal_agent_claim(artifact: Path, base: Path, signing_key: bytes) -> ProofPacket:
    """An agent finished work and produced `artifact`; seal a signed proof of it."""
    packet = ProofPacket(
        phase=1,
        name="Agent build",
        verifier="my-agent@example",
        evidence="Agent reported BUILD COMPLETE.",
        artifacts=[ArtifactRecord.from_path(artifact, base=base)],
        gates=[GateOutcome(name="tests", passed=True, detail="agent: 100% green")],
    )
    return packet.sealed(signing_key=signing_key)


def accepted(packet: ProofPacket, signing_key: bytes) -> bool:
    """The require-signature policy: integrity valid AND signature verifies."""
    return packet.verify_integrity() and packet.verify_signature(signing_key)


def main() -> int:
    key = secrets.token_bytes(32)  # your platform's signing key — never give it to the agent

    with TemporaryDirectory() as td:
        base = Path(td)
        artifact = base / "build.bin"

        # 1. Honest agent: produces a real artifact, you seal a signed proof.
        artifact.write_bytes(b"the real build output\n")
        honest = seal_agent_claim(artifact, base, key)
        print("honest claim   accepted:", accepted(honest, key))   # -> True

        # 2. Lying agent: tampers the artifact AND re-forges the packet hash so
        #    naive integrity still passes — but it can't re-sign without the key.
        artifact.write_bytes(b"the real build output\n :: BACKDOOR\n")
        forged = honest.model_copy(
            update={"artifacts": [ArtifactRecord.from_path(artifact, base=base)]}
        ).sealed(signing_key=None)  # attacker re-hashes, has no key to re-sign

        print("forged integrity ok:", forged.verify_integrity())   # -> True  (fooled)
        print("forged claim   accepted:", accepted(forged, key))   # -> False (caught)

    ok = accepted(honest, key) is True and accepted(forged, key) is False
    print("\nRIGForge accepted the truth and rejected the lie:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

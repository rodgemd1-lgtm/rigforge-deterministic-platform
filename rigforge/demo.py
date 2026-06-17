"""
``rigforge demo`` — the live tamper-detection wedge.

This is the share-worthy proof: it runs the REAL RIGForge proof machinery
(``rigforge.proof.ProofPacket``) end-to-end and shows that a forged seal —
one where an attacker tampered with an artifact AND re-forged the integrity
hash to hide it — is still caught by the HMAC signature check, because the
attacker never had the signing key.

Nothing here is scripted or faked: every hash, signature, and verdict is
computed by the same code paths the platform uses to seal and verify real
phases. A reviewer can replace the demo's narration with their own asserts
and the cryptographic outcome will not change.

The flow:

1. An AI agent reports ``BUILD COMPLETE`` and emits an artifact.
2. RIGForge seals a signed ``ProofPacket`` over that artifact (real signing key).
3. The agent (or an attacker) tampers the artifact and re-forges the packet
   hash so that *naive integrity* (``verify_integrity``) still PASSES.
4. ``verify`` runs the signature check (``verify_signature``) → the forgery is
   caught, because the HMAC was bound to the *original* hash and the attacker
   cannot recompute it without the key.
5. Closing line.

``run_demo`` returns a :class:`DemoResult` so a test can assert the forgery is
genuinely caught (and, via plant-and-restore, that *without* the signature
check it would have slipped through).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from rigforge.proof import ArtifactRecord, GateOutcome, ProofPacket


HONEST_ARTIFACT = b"build/app.bin :: v1.0.0 :: status=GREEN :: 42 tests passed\n"
TAMPERED_ARTIFACT = b"build/app.bin :: v1.0.0 :: status=GREEN :: 0 tests passed (BACKDOOR)\n"


@dataclass(frozen=True)
class DemoResult:
    """Machine-readable outcome of the demo, for tests and JSON output."""

    # The signed seal the honest agent produced.
    sealed_packet_sha256: str
    sealed_signature: str
    # The forged packet the attacker produced after tampering.
    forged_packet_sha256: str
    # Did the attacker successfully fool the naive integrity check?
    naive_integrity_passes_on_forgery: bool
    # Did the signature check catch the forgery? (This is the whole point.)
    signature_catches_forgery: bool
    # Control: does the honest, untampered packet verify clean on both checks?
    honest_integrity_ok: bool
    honest_signature_ok: bool

    @property
    def forgery_caught(self) -> bool:
        """The demo's success condition: integrity was fooled, signature was not."""
        return self.naive_integrity_passes_on_forgery and self.signature_catches_forgery

    def to_dict(self) -> dict:
        return {
            "sealed_packet_sha256": self.sealed_packet_sha256,
            "sealed_signature": self.sealed_signature,
            "forged_packet_sha256": self.forged_packet_sha256,
            "naive_integrity_passes_on_forgery": self.naive_integrity_passes_on_forgery,
            "signature_catches_forgery": self.signature_catches_forgery,
            "honest_integrity_ok": self.honest_integrity_ok,
            "honest_signature_ok": self.honest_signature_ok,
            "forgery_caught": self.forgery_caught,
        }


def _build_packet_over(artifact: Path, base: Path) -> ProofPacket:
    """Build an (unsealed) ProofPacket pinning ``artifact`` — real hashing."""
    return ProofPacket(
        phase=1,
        name="Demo Build",
        verifier="ai-agent@rigforge.demo",
        evidence="Agent reported BUILD COMPLETE.",
        artifacts=[ArtifactRecord.from_path(artifact, base=base)],
        gates=[GateOutcome(name="build", passed=True, detail="agent says: success")],
    )


def run_demo(*, workdir: Path | None = None) -> DemoResult:
    """Run the real tamper-detection flow and return the structured outcome.

    Uses a temporary directory and an ephemeral signing key by default, so it
    is fully self-contained and leaves no state behind. ``workdir`` may be
    supplied (e.g. by a test) to keep the artifacts around for inspection.
    """
    if workdir is not None:
        return _run_in(Path(workdir))
    with TemporaryDirectory(prefix="rigforge-demo-") as td:
        return _run_in(Path(td))


def _run_in(base: Path) -> DemoResult:
    # The signing key the honest platform holds. The attacker NEVER sees it.
    signing_key = secrets.token_bytes(32)

    artifact = base / "app.bin"

    # ── 1+2. Agent emits artifact; RIGForge seals a SIGNED packet over it. ──
    artifact.write_bytes(HONEST_ARTIFACT)
    sealed = _build_packet_over(artifact, base).sealed(signing_key=signing_key)

    # Control sanity: the honest seal verifies clean on BOTH checks.
    honest_integrity_ok = sealed.verify_integrity()
    honest_signature_ok = sealed.verify_signature(signing_key)

    # ── 3. Attacker tampers the artifact and re-forges the integrity hash. ──
    # The attacker has write access to the file and the packet JSON, but NOT
    # the signing key. They:
    #   (a) overwrite the artifact with a backdoored build,
    #   (b) recompute the ArtifactRecord so the packet matches the new file,
    #   (c) recompute packet_sha256 so verify_integrity() PASSES,
    #   (d) leave the signature untouched (they can't forge it without the key).
    artifact.write_bytes(TAMPERED_ARTIFACT)
    tampered_record = ArtifactRecord.from_path(artifact, base=base)
    forged = sealed.model_copy(update={"artifacts": [tampered_record]})
    # Re-seal WITHOUT the key → recomputes packet_sha256, keeps old signature.
    forged = forged.sealed(signing_key=None)

    # ── 4. The two checks disagree — that disagreement IS the detection. ────
    # Naive integrity is fooled: the recomputed hash matches the tampered body.
    naive_integrity_passes = forged.verify_integrity()
    # The signature does NOT verify: it was bound to the original hash, and the
    # attacker could not recompute the HMAC without the signing key.
    signature_ok = forged.verify_signature(signing_key)
    signature_catches_forgery = not signature_ok

    return DemoResult(
        sealed_packet_sha256=sealed.packet_sha256,
        sealed_signature=sealed.signature,
        forged_packet_sha256=forged.packet_sha256,
        naive_integrity_passes_on_forgery=naive_integrity_passes,
        signature_catches_forgery=signature_catches_forgery,
        honest_integrity_ok=honest_integrity_ok,
        honest_signature_ok=honest_signature_ok,
    )


# ── Rich narration ──────────────────────────────────────────────────────────


def render_demo(result: DemoResult) -> None:
    """Narrate the demo to the terminal with gorgeous ``rich`` output."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = Console()

    console.print()
    console.print(
        Panel.fit(
            Text.assemble(
                ("RIGForge", "bold white"),
                ("  ·  ", "dim"),
                ("Live Tamper-Detection Demo", "bold cyan"),
            ),
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()

    # Step 1 — the agent's claim.
    console.print(
        Panel(
            Text.assemble(
                ("An AI agent reports:  ", "white"),
                ("BUILD COMPLETE ✅", "bold green"),
            ),
            title="[bold]1 · the claim[/]",
            border_style="green",
            padding=(0, 2),
        )
    )

    # Step 2 — RIGForge seals a signed ProofPacket.
    seal_table = Table.grid(padding=(0, 2))
    seal_table.add_column(style="dim")
    seal_table.add_column()
    seal_table.add_row("packet sha256", Text(result.sealed_packet_sha256, style="yellow"))
    seal_table.add_row("hmac signature", Text(result.sealed_signature[:48] + "…", style="magenta"))
    seal_table.add_row(
        "integrity",
        Text("✔ valid", style="green") if result.honest_integrity_ok else Text("✘", style="red"),
    )
    seal_table.add_row(
        "signature",
        Text("✔ valid", style="green") if result.honest_signature_ok else Text("✘", style="red"),
    )
    console.print(
        Panel(
            seal_table,
            title="[bold]2 · RIGForge seals a signed ProofPacket[/]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    # Step 3 — the tamper.
    console.print(
        Panel(
            Text.assemble(
                ("But did the agent tell the truth?\n\n", "italic white"),
                ("The artifact is ", "white"),
                ("TAMPERED", "bold red"),
                (" and the packet hash is re-forged to hide it.\n", "white"),
                ("Naive integrity now ", "white"),
                ("PASSES", "bold yellow"),
                (" — the lie looks clean.", "white"),
            ),
            title="[bold]3 · the tamper[/]",
            border_style="red",
            padding=(1, 2),
        )
    )

    # Step 4 — the catch.
    verdict_table = Table.grid(padding=(0, 2))
    verdict_table.add_column(style="dim")
    verdict_table.add_column()
    verdict_table.add_row(
        "naive integrity check",
        Text("PASS  — fooled by the re-forged hash", style="yellow")
        if result.naive_integrity_passes_on_forgery
        else Text("(unexpected)", style="dim"),
    )
    verdict_table.add_row(
        "hmac signature check",
        Text("FAIL  — signature does not verify", style="bold red")
        if result.signature_catches_forgery
        else Text("(unexpected)", style="dim"),
    )
    console.print(
        Panel(
            verdict_table,
            title="[bold]4 · RIGForge verify[/]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    if result.forgery_caught:
        console.print(
            Panel.fit(
                Text("🚨 FORGED. Signature invalid. The agent lied.", style="bold red"),
                border_style="red",
                padding=(0, 3),
            )
        )
    else:  # pragma: no cover — only reachable if the crypto invariant breaks
        console.print(
            Panel.fit(
                Text("⚠️  Demo invariant broken — forgery NOT caught.", style="bold yellow"),
                border_style="yellow",
            )
        )

    console.print()
    console.print(
        Text("Don't trust your agents. Prove them.  — RIGForge", style="bold white"),
        justify="center",
    )
    console.print()

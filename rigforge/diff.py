"""
``rigforge diff`` — MODEL-DIFF between two ProofPackets.

The share-worthy question this answers: *two runs claimed "done" — what actually
changed between them?* Re-run an agent with a different model, a different seed,
or after a code change, and `diff` shows you exactly what diverged: which gates
flipped, which artifact hashes changed, and — for ``llm-stochastic`` steps — which
model produced each (id, version, temperature, seed).

This is pure structural comparison over two :class:`~rigforge.proof.ProofPacket`
objects. It reads, never writes, and is fully deterministic: the same two packets
always produce the same diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rigforge.proof import ArtifactRecord, GateOutcome, ModelMetadata, ProofPacket


# ── Model-metadata rendering ──────────────────────────────────────────────


def _model_label(model: ModelMetadata | None) -> str:
    """Compact one-line label for a step's model, e.g. ``sonnet@2025-09 t=0.7 seed=42``."""
    if model is None:
        return "—"
    bits = model.model_id
    if model.version:
        bits += f"@{model.version}"
    extra = []
    if model.temperature is not None:
        extra.append(f"t={model.temperature}")
    if model.seed is not None:
        extra.append(f"seed={model.seed}")
    return f"{bits} {' '.join(extra)}".strip() if extra else bits


def _short_hash(sha: str) -> str:
    return sha[:8] if sha else "∅"


# ── Field-level changes ───────────────────────────────────────────────────


@dataclass(frozen=True)
class FieldChange:
    """A single ``before → after`` divergence on one step/artifact/header field."""

    scope: str          # "header" | "gate:<name>" | "artifact:<path>"
    field: str          # e.g. "passed", "output hash", "model"
    before: str
    after: str

    def as_line(self) -> str:
        return f"{self.scope}: {self.field} {self.before} → {self.after}"


@dataclass
class PacketDiff:
    """Structured result of diffing two ProofPackets."""

    changes: list[FieldChange] = field(default_factory=list)
    only_in_a: list[str] = field(default_factory=list)   # steps/artifacts present only in A
    only_in_b: list[str] = field(default_factory=list)   # ...only in B

    @property
    def has_differences(self) -> bool:
        return bool(self.changes or self.only_in_a or self.only_in_b)

    def to_dict(self) -> dict:
        return {
            "has_differences": self.has_differences,
            "changes": [
                {"scope": c.scope, "field": c.field, "before": c.before, "after": c.after}
                for c in self.changes
            ],
            "only_in_a": list(self.only_in_a),
            "only_in_b": list(self.only_in_b),
        }


# ── Diffing logic ─────────────────────────────────────────────────────────


def _diff_header(a: ProofPacket, b: ProofPacket, out: PacketDiff) -> None:
    for fname, av, bv in (
        ("phase", a.phase, b.phase),
        ("name", a.name, b.name),
        ("status", a.status, b.status),
        ("verifier", a.verifier, b.verifier),
        ("packet hash", _short_hash(a.packet_sha256), _short_hash(b.packet_sha256)),
    ):
        if av != bv:
            out.changes.append(FieldChange("header", fname, str(av), str(bv)))


def _diff_gate(name: str, ga: GateOutcome, gb: GateOutcome, out: PacketDiff) -> None:
    scope = f"step {name}"
    if ga.passed != gb.passed:
        out.changes.append(
            FieldChange(scope, "passed", str(ga.passed).lower(), str(gb.passed).lower())
        )
    if ga.kind != gb.kind:
        out.changes.append(FieldChange(scope, "kind", ga.kind, gb.kind))
    la, lb = _model_label(ga.model), _model_label(gb.model)
    if la != lb:
        out.changes.append(FieldChange(scope, "model", la, lb))


def _diff_artifact(path: str, aa: ArtifactRecord, ab: ArtifactRecord, out: PacketDiff) -> None:
    scope = f"artifact {path}"
    if aa.sha256 != ab.sha256:
        out.changes.append(
            FieldChange(scope, "output hash", _short_hash(aa.sha256), _short_hash(ab.sha256))
        )
    if aa.kind != ab.kind:
        out.changes.append(FieldChange(scope, "kind", aa.kind, ab.kind))
    if aa.exists != ab.exists:
        out.changes.append(
            FieldChange(scope, "exists", str(aa.exists).lower(), str(ab.exists).lower())
        )


def diff_packets(a: ProofPacket, b: ProofPacket) -> PacketDiff:
    """Compute the structural diff of two ProofPackets (A = before, B = after)."""
    out = PacketDiff()
    _diff_header(a, b, out)

    # Gates (steps), keyed by name.
    a_gates = {g.name: g for g in a.gates}
    b_gates = {g.name: g for g in b.gates}
    for name in sorted(set(a_gates) & set(b_gates)):
        _diff_gate(name, a_gates[name], b_gates[name], out)
    out.only_in_a += [f"step {n}" for n in sorted(set(a_gates) - set(b_gates))]
    out.only_in_b += [f"step {n}" for n in sorted(set(b_gates) - set(a_gates))]

    # Artifacts, keyed by path.
    a_arts = {r.path: r for r in a.artifacts}
    b_arts = {r.path: r for r in b.artifacts}
    for path in sorted(set(a_arts) & set(b_arts)):
        _diff_artifact(path, a_arts[path], b_arts[path], out)
    out.only_in_a += [f"artifact {p}" for p in sorted(set(a_arts) - set(b_arts))]
    out.only_in_b += [f"artifact {p}" for p in sorted(set(b_arts) - set(a_arts))]

    return out


# ── Rich rendering ────────────────────────────────────────────────────────


def render_diff(diff: PacketDiff, *, label_a: str, label_b: str) -> None:
    """Render the diff to the terminal with a rich table."""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console()
    console.print()
    console.print(
        Text.assemble(
            ("RIGForge", "bold white"),
            ("  ·  ", "dim"),
            ("ProofPacket diff", "bold cyan"),
        )
    )
    console.print(Text.assemble(("A  ", "dim"), (label_a, "yellow")))
    console.print(Text.assemble(("B  ", "dim"), (label_b, "magenta")))
    console.print()

    if not diff.has_differences:
        console.print(Text("✅  no differences — the two packets are identical.", style="bold green"))
        console.print()
        return

    if diff.changes:
        table = Table(title="changed", title_style="bold", header_style="bold dim")
        table.add_column("where", style="cyan", no_wrap=True)
        table.add_column("field", style="white")
        table.add_column("A", style="yellow")
        table.add_column("→", style="dim", justify="center")
        table.add_column("B", style="magenta")
        for c in diff.changes:
            table.add_row(c.scope, c.field, c.before, "→", c.after)
        console.print(table)

    for items, title, style in (
        (diff.only_in_a, "only in A", "yellow"),
        (diff.only_in_b, "only in B", "magenta"),
    ):
        if items:
            console.print()
            console.print(Text(title, style=f"bold {style}"))
            for it in items:
                console.print(Text.assemble(("  • ", "dim"), (it, style)))

    console.print()
    n = len(diff.changes) + len(diff.only_in_a) + len(diff.only_in_b)
    console.print(Text(f"{n} difference(s).", style="bold white"))
    console.print()

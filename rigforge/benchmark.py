"""
``rigforge benchmark`` — the honesty gate.

A fixed, seeded, fully-OFFLINE suite of deterministic build-task scenarios.
For each scenario an agent "claims done"; some claims are honest and some are
forged/tampered. RIGForge runs its REAL proof-verification machinery
(:meth:`rigforge.proof.ProofPacket.verify_integrity` /
:meth:`rigforge.proof.ProofPacket.verify_signature`) over each claim and we
count what actually happened.

Nothing here is faked, scripted, or hand-waved:

* every artifact hash is computed by :class:`rigforge.proof.ArtifactRecord`;
* every packet seal/signature is computed by :meth:`ProofPacket.sealed`;
* every verdict is the return value of the real ``verify_*`` methods the
  platform uses on production phases.

A scenario's ground truth (``is_honest``) is fixed at construction time and
the metrics are derived by counting real verdicts against that ground truth.
Re-running the suite with the same seed reproduces the same artifacts, the
same forgeries, and the same counts — byte-for-byte deterministic.

What we measure (and what we deliberately OMIT):

* **false-done-caught rate** — of the forged claims, the fraction RIGForge
  rejected. (Recall on forgeries.)
* **false-pass rate (wrongly blocked)** — of the honest claims, the fraction
  RIGForge *rejected*. (False-positive rate on truth.) Lower is better; this
  is the "did we block a true claim?" number.
* **tamper-detection precision** — of the claims RIGForge rejected, the
  fraction that were genuinely forged. (Precision on rejections.)
* **overall accuracy** — correct verdicts / total scenarios.
* **per-scenario elapsed_ms** — real wall-clock of the seal+verify pass.

We do NOT report throughput (tasks/sec) as a headline number because it is
dominated by Python/pydantic overhead and timezone datetime serialization,
not the cryptographic work — reporting it would be misleading. The
per-scenario ``elapsed_ms`` is reported raw so a reviewer can see for
themselves; no aggregate "tasks/sec" is computed.

The whole suite is offline: no network, no LLM, no filesystem state outside a
single temp dir, no wall-clock dependence in the verdicts (only in the
``elapsed_ms`` telemetry, which is clearly labelled as non-deterministic and
excluded from the equality contract).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Literal

from rigforge.proof import ArtifactRecord, GateOutcome, ProofPacket


# ── Seeded RNG ──────────────────────────────────────────────────────────────


class _RNG:
    """Deterministic PRNG.

    We use HMAC-SHA256 in counter mode over a fixed seed so the byte stream is
    byte-identical across processes, Python builds, and machines (unlike
    ``random.Random`` whose string/int generation is implementation-defined).
    Everything that must reproduce — artifact contents, scenario labels — is
    drawn from here.
    """

    def __init__(self, seed: int) -> None:
        self._key = seed.to_bytes(16, "big", signed=False)
        self._counter = 0
        self._buf = b""
        self._pos = 0

    def _refill(self, n: int) -> None:
        have = len(self._buf)
        chunks: list[bytes] = []
        while have < n:
            chunk = hmac.new(self._key, self._counter.to_bytes(8, "big"), hashlib.sha256).digest()
            chunks.append(chunk)
            have += len(chunk)
            self._counter += 1
        if chunks:
            self._buf += b"".join(chunks)

    def bytes(self, n: int) -> bytes:
        self._refill(n)
        out = self._buf[:n]
        self._buf = self._buf[n:]
        return out

    def uint(self, bound: int) -> int:
        # Rejection-sample a uniform int in [0, bound).
        if bound <= 0:
            return 0
        span = 1
        while span < bound:
            span <<= 1
        while True:
            v = int.from_bytes(self.bytes((span.bit_length() + 7) // 8), "big") % span
            if v < bound:
                return v


# Deterministic artifact corpus. Each scenario's "real" artifact is a labeled
# blob drawn from the seeded RNG, so re-runs reproduce byte-for-byte.
def _artifact_bytes(rng: _RNG, scenario_id: str, size: int) -> bytes:
    """Deterministic, scenario-distinct artifact body."""
    label = f"build/{scenario_id}.bin :: status=GREEN :: tests=pass\n".encode()
    body = rng.bytes(size)
    return label + body


# ── Scenario model ──────────────────────────────────────────────────────────


ScenarioKind = Literal[
    "honest",                # truth: signed cleanly → must PASS
    "tampered_artifact",     # lie: file changed + hash re-forged → signature FAIL
    "forged_signature",      # lie: signature replaced with garbage → signature FAIL
    "swapped_artifact",      # lie: different file, hash re-forged → signature FAIL
    "unsigned_tamper",       # lie: tampered, re-hashed, NO signature → require-signature blocks
    "dropped_gate",          # lie: a failed gate flipped to passed, hash re-forged → signature FAIL
]


@dataclass(frozen=True)
class Scenario:
    """One deterministic build-task scenario with known ground truth."""

    sid: str
    phase: int
    kind: ScenarioKind
    is_honest: bool
    artifact_size: int
    # A human-readable description of what the (possibly malicious) agent did.
    agent_action: str
    # The packet the agent/platform produced (already sealed or forged).
    packet: ProofPacket
    # The signing key the honest platform holds (None for unsigned_tamper).
    signing_key: bytes | None

    @property
    def label(self) -> str:
        flag = "honest" if self.is_honest else "FORGED"
        return f"{self.sid:>4}  p{self.phase}  {self.kind:<18s} [{flag}]"


@dataclass(frozen=True)
class ScenarioResult:
    """The outcome of running RIGForge verify over one scenario."""

    sid: str
    kind: ScenarioKind
    is_honest: bool
    agent_action: str
    # Real verdicts from the production verify methods.
    integrity_ok: bool
    signature_ok: bool | None  # None when no signature exists to check
    # RIGForge's accept/reject decision under the require-signature policy.
    accepted: bool
    # Was this verdict correct given the ground truth?
    correct: bool
    # Telemetry — NON-deterministic, excluded from the equality contract.
    elapsed_ms: float


@dataclass(frozen=True)
class BenchmarkResult:
    """Aggregate outcome of the full benchmark run."""

    seed: int
    scenario_count: int
    honest_count: int
    forged_count: int
    # Counts derived by tallying real verdicts.
    true_negatives: int   # forged claims correctly rejected
    false_negatives: int  # forged claims wrongly accepted (catastrophic)
    true_positives: int   # honest claims correctly accepted
    false_positives: int  # honest claims wrongly rejected (false-pass blocks)
    # Derived rates (floats; rounded for display, exact for equality).
    false_done_caught_rate: float  # recall on forged = TN / (TN + FN)
    false_pass_rate: float         # wrongly-blocked honest = FP / (TP + FP)
    tamper_detection_precision: float  # TN / (TN + FP)
    accuracy: float                # (TP + TN) / total
    # Per-scenario detail.
    scenarios: list[ScenarioResult] = field(default_factory=list)
    # Aggregate wall-clock of the whole run. NON-deterministic; telemetry only.
    total_elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "scenario_count": self.scenario_count,
            "honest_count": self.honest_count,
            "forged_count": self.forged_count,
            "counts": {
                "true_negatives": self.true_negatives,
                "false_negatives": self.false_negatives,
                "true_positives": self.true_positives,
                "false_positives": self.false_positives,
            },
            "rates": {
                "false_done_caught_rate": self.false_done_caught_rate,
                "false_pass_rate": self.false_pass_rate,
                "tamper_detection_precision": self.tamper_detection_precision,
                "accuracy": self.accuracy,
            },
            "scenarios": [
                {
                    "sid": s.sid,
                    "kind": s.kind,
                    "is_honest": s.is_honest,
                    "agent_action": s.agent_action,
                    "integrity_ok": s.integrity_ok,
                    "signature_ok": s.signature_ok,
                    "accepted": s.accepted,
                    "correct": s.correct,
                    "elapsed_ms": s.elapsed_ms,
                }
                for s in self.scenarios
            ],
            "total_elapsed_ms": self.total_elapsed_ms,
            # Explicit honesty note: what we do NOT claim.
            "omitted_metrics": [
                "throughput (tasks/sec) — dominated by pydantic/datetime overhead, not crypto; would mislead",
                "latency percentiles — same reason; only raw per-scenario elapsed_ms is reported",
            ],
        }


# ── Scenario builders ───────────────────────────────────────────────────────
#
# Each builder writes the artifact(s) to disk, builds the packet via the REAL
# ArtifactRecord.from_path hashing, seals/forges it via the REAL ProofPacket
# machinery, and returns the Scenario. Nothing about the verdict is decided
# here — only the ground-truth ``is_honest`` flag.


def _base_packet(phase: int, sid: str, artifact: Path, base: Path) -> ProofPacket:
    return ProofPacket(
        phase=phase,
        name=f"Benchmark Build {sid}",
        verifier="ai-agent@benchmark",
        evidence=f"Agent reported BUILD COMPLETE for {sid}.",
        artifacts=[ArtifactRecord.from_path(artifact, base=base)],
        gates=[
            GateOutcome(name="build", passed=True, detail="agent says: success"),
            GateOutcome(name="tests", passed=True, detail="agent says: 100% green"),
        ],
    )


def _build_honest(rng: _RNG, sid: str, phase: int, base: Path) -> Scenario:
    size = 256 + rng.uint(2048)
    artifact = base / f"{sid}.bin"
    artifact.write_bytes(_artifact_bytes(rng, sid, size))
    key = secrets.token_bytes(32)  # key value is irrelevant to determinism; verdict is
    sealed = _base_packet(phase, sid, artifact, base).sealed(signing_key=key)
    return Scenario(
        sid=sid,
        phase=phase,
        kind="honest",
        is_honest=True,
        artifact_size=size,
        agent_action="emitted a real artifact and let RIGForge seal a signed packet",
        packet=sealed,
        signing_key=key,
    )


def _build_tampered_artifact(rng: _RNG, sid: str, phase: int, base: Path) -> Scenario:
    size = 256 + rng.uint(2048)
    artifact = base / f"{sid}.bin"
    artifact.write_bytes(_artifact_bytes(rng, sid, size))
    key = secrets.token_bytes(32)
    sealed = _base_packet(phase, sid, artifact, base).sealed(signing_key=key)
    # Attacker: overwrite artifact with a backdoor, re-pin the record, re-forge
    # the packet hash (so naive integrity passes), keep the old signature.
    artifact.write_bytes(_artifact_bytes(rng, sid, size) + b" :: BACKDOOR\n")
    tampered_record = ArtifactRecord.from_path(artifact, base=base)
    forged = sealed.model_copy(update={"artifacts": [tampered_record]}).sealed(signing_key=None)
    return Scenario(
        sid=sid,
        phase=phase,
        kind="tampered_artifact",
        is_honest=False,
        artifact_size=size,
        agent_action="tampered the artifact and re-forged the packet hash to hide it",
        packet=forged,
        signing_key=key,
    )


def _build_forged_signature(rng: _RNG, sid: str, phase: int, base: Path) -> Scenario:
    size = 256 + rng.uint(2048)
    artifact = base / f"{sid}.bin"
    artifact.write_bytes(_artifact_bytes(rng, sid, size))
    key = secrets.token_bytes(32)
    sealed = _base_packet(phase, sid, artifact, base).sealed(signing_key=key)
    # Attacker: keep the honest body but slap on a signature they cannot have
    # computed (random bytes). Integrity still passes; signature must FAIL.
    fake_sig = secrets.token_hex(32)
    forged = sealed.model_copy(update={"signature": fake_sig})
    return Scenario(
        sid=sid,
        phase=phase,
        kind="forged_signature",
        is_honest=False,
        artifact_size=size,
        agent_action="attached a fabricated HMAC signature they could not have computed",
        packet=forged,
        signing_key=key,
    )


def _build_swapped_artifact(rng: _RNG, sid: str, phase: int, base: Path) -> Scenario:
    size_a = 256 + rng.uint(1024)
    size_b = 2048 + rng.uint(4096)
    artifact_a = base / f"{sid}.bin"
    artifact_a.write_bytes(_artifact_bytes(rng, sid, size_a))
    key = secrets.token_bytes(32)
    sealed = _base_packet(phase, sid, artifact_a, base).sealed(signing_key=key)
    # Attacker: swap in a completely different artifact, re-pin, re-forge hash.
    artifact_a.write_bytes(_artifact_bytes(rng, sid + "-swap", size_b))
    swapped_record = ArtifactRecord.from_path(artifact_a, base=base)
    forged = sealed.model_copy(update={"artifacts": [swapped_record]}).sealed(signing_key=None)
    return Scenario(
        sid=sid,
        phase=phase,
        kind="swapped_artifact",
        is_honest=False,
        artifact_size=size_b,
        agent_action="swapped in a different artifact and re-forged the packet hash",
        packet=forged,
        signing_key=key,
    )


def _build_unsigned_tamper(rng: _RNG, sid: str, phase: int, base: Path) -> Scenario:
    size = 256 + rng.uint(2048)
    artifact = base / f"{sid}.bin"
    artifact.write_bytes(_artifact_bytes(rng, sid, size))
    # Attacker never let the platform sign. They tamper, re-hash, ship unsigned.
    artifact.write_bytes(_artifact_bytes(rng, sid, size) + b" :: BACKDOOR\n")
    tampered = _base_packet(phase, sid, artifact, base).sealed(signing_key=None)
    return Scenario(
        sid=sid,
        phase=phase,
        kind="unsigned_tamper",
        is_honest=False,
        artifact_size=size,
        agent_action="shipped a tampered, unsigned packet (no signature to check)",
        packet=tampered,
        signing_key=None,
    )


def _build_dropped_gate(rng: _RNG, sid: str, phase: int, base: Path) -> Scenario:
    size = 256 + rng.uint(2048)
    artifact = base / f"{sid}.bin"
    artifact.write_bytes(_artifact_bytes(rng, sid, size))
    key = secrets.token_bytes(32)
    # Honest packet records a FAILED test gate.
    base_packet = ProofPacket(
        phase=phase,
        name=f"Benchmark Build {sid}",
        verifier="ai-agent@benchmark",
        evidence=f"Agent reported BUILD COMPLETE for {sid}.",
        artifacts=[ArtifactRecord.from_path(artifact, base=base)],
        gates=[
            GateOutcome(name="build", passed=True, detail="agent says: success"),
            GateOutcome(name="tests", passed=False, detail="2 tests failed"),
        ],
    )
    sealed = base_packet.sealed(signing_key=key)
    # Attacker: flip the failed gate to passed, re-pin gates, re-forge hash.
    cleaned_gates = [
        sealed.gates[0],
        sealed.gates[1].model_copy(update={"passed": True, "detail": "agent says: 100% green"}),
    ]
    forged = sealed.model_copy(update={"gates": cleaned_gates}).sealed(signing_key=None)
    return Scenario(
        sid=sid,
        phase=phase,
        kind="dropped_gate",
        is_honest=False,
        artifact_size=size,
        agent_action="flipped a failed test gate to passed and re-forged the packet hash",
        packet=forged,
        signing_key=key,
    )


# Ordered, fixed scenario plan. The mix is deliberately balanced so every
# attack class is represented and the honest/forged split is non-trivial.
_BUILDER_FOR: dict[ScenarioKind, Callable[[_RNG, str, int, Path], Scenario]] = {
    "honest": _build_honest,
    "tampered_artifact": _build_tampered_artifact,
    "forged_signature": _build_forged_signature,
    "swapped_artifact": _build_swapped_artifact,
    "unsigned_tamper": _build_unsigned_tamper,
    "dropped_gate": _build_dropped_gate,
}

# 16 scenarios: 8 honest, 8 forged across 5 attack classes.
_PLAN: list[tuple[ScenarioKind, int]] = [
    ("honest", 1),
    ("tampered_artifact", 1),
    ("honest", 2),
    ("forged_signature", 2),
    ("honest", 3),
    ("swapped_artifact", 3),
    ("honest", 4),
    ("dropped_gate", 4),
    ("honest", 5),
    ("tampered_artifact", 5),
    ("honest", 6),
    ("forged_signature", 6),
    ("honest", 7),
    ("unsigned_tamper", 7),
    ("honest", 1),
    ("tampered_artifact", 1),
]

DEFAULT_SEED = 0xC0FFEE  # a fixed, named seed so the default run is reproducible


def build_scenarios(seed: int, base: Path) -> list[Scenario]:
    """Construct the full deterministic scenario suite under ``base``."""
    rng = _RNG(seed)
    scenarios: list[Scenario] = []
    for idx, (kind, phase) in enumerate(_PLAN):
        sid = f"s{idx:02d}"
        scenarios.append(_BUILDER_FOR[kind](rng, sid, phase, base))
    return scenarios


# ── The verify step (the thing under test) ──────────────────────────────────


def _rigforge_verdict(packet: ProofPacket, signing_key: bytes | None) -> tuple[bool, bool | None, bool]:
    """Run RIGForge's REAL verify over ``packet``.

    Returns ``(integrity_ok, signature_ok, accepted)`` where ``accepted`` is the
    platform's accept/reject decision under the strict ``--require-signature``
    policy: a packet is accepted only if (a) its integrity hash is valid AND
    (b) it carries a signature that verifies against the platform key.

    This is exactly the policy ``rigforge verify --strict --require-signature``
    enforces in :mod:`rigforge.cli`.
    """
    integrity_ok = packet.verify_integrity()
    signature_ok: bool | None = None
    if signing_key is not None and packet.signature:
        signature_ok = packet.verify_signature(signing_key)
    # require-signature policy: must be signed AND the signature must verify.
    accepted = bool(integrity_ok) and (signature_ok is True)
    return integrity_ok, signature_ok, accepted


def _run_one(scenario: Scenario) -> ScenarioResult:
    t0 = time.perf_counter()
    integrity_ok, signature_ok, accepted = _rigforge_verdict(scenario.packet, scenario.signing_key)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    # Ground truth: honest claims must be ACCEPTED; forged claims must be REJECTED.
    correct = (accepted == scenario.is_honest)
    return ScenarioResult(
        sid=scenario.sid,
        kind=scenario.kind,
        is_honest=scenario.is_honest,
        agent_action=scenario.agent_action,
        integrity_ok=integrity_ok,
        signature_ok=signature_ok,
        accepted=accepted,
        correct=correct,
        elapsed_ms=elapsed_ms,
    )


def run_benchmark(seed: int = DEFAULT_SEED, *, workdir: Path | None = None) -> BenchmarkResult:
    """Run the full benchmark suite and return the aggregate result.

    Fully offline and self-contained: uses a temp dir unless ``workdir`` is
    given (for tests that want to inspect the artifacts). No network, no LLM.
    """
    if workdir is not None:
        return _run_in(Path(workdir), seed)
    with TemporaryDirectory(prefix="rigforge-bench-") as td:
        return _run_in(Path(td), seed)


def _run_in(base: Path, seed: int) -> BenchmarkResult:
    scenarios = build_scenarios(seed, base)
    t_total = time.perf_counter()
    results = [_run_one(s) for s in scenarios]
    total_elapsed_ms = (time.perf_counter() - t_total) * 1000.0
    return _aggregate(seed, results, total_elapsed_ms)


def _aggregate(seed: int, results: list[ScenarioResult], total_elapsed_ms: float) -> BenchmarkResult:
    honest = [r for r in results if r.is_honest]
    forged = [r for r in results if not r.is_honest]

    # Confusion matrix against the "accepted" decision (require-signature policy).
    true_positives = sum(1 for r in honest if r.accepted)     # honest → accepted ✔
    false_positives = sum(1 for r in honest if not r.accepted)  # honest → rejected (wrongly blocked)
    true_negatives = sum(1 for r in forged if not r.accepted)   # forged → rejected ✔
    false_negatives = sum(1 for r in forged if r.accepted)      # forged → accepted (catastrophic)

    total = len(results)

    def safe(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return BenchmarkResult(
        seed=seed,
        scenario_count=total,
        honest_count=len(honest),
        forged_count=len(forged),
        true_negatives=true_negatives,
        false_negatives=false_negatives,
        true_positives=true_positives,
        false_positives=false_positives,
        false_done_caught_rate=safe(true_negatives, true_negatives + false_negatives),
        false_pass_rate=safe(false_positives, true_positives + false_positives),
        tamper_detection_precision=safe(true_negatives, true_negatives + false_positives),
        accuracy=safe(true_positives + true_negatives, total),
        scenarios=results,
        total_elapsed_ms=total_elapsed_ms,
    )


# ── Rendering ───────────────────────────────────────────────────────────────


def render_benchmark(result: BenchmarkResult) -> None:
    """Render the benchmark result as a clean table to the terminal."""
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
                ("Benchmark — the honesty gate", "bold cyan"),
            ),
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print(
        Text.assemble(
            ("seed=", "dim"),
            (f"{result.seed:#x}", "yellow"),
            ("  scenarios=", "dim"),
            (str(result.scenario_count), "white"),
            ("  honest=", "dim"),
            (str(result.honest_count), "green"),
            ("  forged=", "dim"),
            (str(result.forged_count), "red"),
        )
    )
    console.print()

    tbl = Table(title="Per-scenario verdicts (real ProofPacket.verify_*)", border_style="cyan")
    tbl.add_column("sid", style="dim")
    tbl.add_column("kind")
    tbl.add_column("truth")
    tbl.add_column("integrity", justify="center")
    tbl.add_column("signature", justify="center")
    tbl.add_column("accepted", justify="center")
    tbl.add_column("correct", justify="center")
    for r in result.scenarios:
        truth_txt = Text("honest", style="green") if r.is_honest else Text("FORGED", style="red")
        integ = Text("pass", style="green") if r.integrity_ok else Text("FAIL", style="red")
        if r.signature_ok is None:
            sig = Text("—", style="dim")
        else:
            sig = Text("pass", style="green") if r.signature_ok else Text("FAIL", style="red")
        acc = Text("accept", style="green") if r.accepted else Text("REJECT", style="red")
        cor = Text("✔", style="green") if r.correct else Text("✘", style="red")
        tbl.add_row(r.sid, r.kind, truth_txt, integ, sig, acc, cor)
    console.print(tbl)
    console.print()

    def rate_row(label: str, value: float, good_when: str) -> Text:
        return Text.assemble(
            (f"  {label:<32s} ", "white"),
            (f"{value:.4f}", "bold yellow"),
            (f"   ({good_when})", "dim"),
        )

    rates = Table(title="Aggregate rates (computed by counting real verdicts)", border_style="cyan")
    rates.add_column("metric")
    rates.add_column("value", justify="right")
    rates.add_column("confusion-matrix basis", style="dim")
    rates.add_row(
        "false-done-caught rate (recall)",
        f"{result.false_done_caught_rate:.4f}",
        f"TN={result.true_negatives} / (TN+FN={result.true_negatives + result.false_negatives})",
    )
    rates.add_row(
        "false-pass rate (wrongly blocked)",
        f"{result.false_pass_rate:.4f}",
        f"FP={result.false_positives} / (TP+FP={result.true_positives + result.false_positives})",
    )
    rates.add_row(
        "tamper-detection precision",
        f"{result.tamper_detection_precision:.4f}",
        f"TN={result.true_negatives} / (TN+FP={result.true_negatives + result.false_positives})",
    )
    rates.add_row(
        "accuracy",
        f"{result.accuracy:.4f}",
        f"(TP+TN={result.true_positives + result.true_negatives}) / N={result.scenario_count}",
    )
    console.print(rates)
    console.print()
    console.print(
        Text.assemble(
            ("  total wall-clock: ", "dim"),
            (f"{result.total_elapsed_ms:.2f} ms", "white"),
            ("  (telemetry only — excluded from the determinism equality contract)", "dim"),
        )
    )
    console.print(
        Text.assemble(
            ("  omitted: ", "dim"),
            (", ".join(result.to_dict()["omitted_metrics"]), "dim italic"),
        )
    )
    console.print()

#!/usr/bin/env python3
"""
RIGForge CLI — ``rigforge`` command-line interface.

Top-level commands::

    rigforge init                       # scaffold a new RIGForge project
    rigforge doctor                     # diagnose env, repo layout, contracts
    rigforge status [--json]            # phase seal status
    rigforge run PHASE [--dry-run] [--json]
    rigforge seal PHASE [--artifact PATH ...] [--evidence TEXT]
    rigforge verify [--strict] [--json]
    rigforge contract list|validate|create|inspect
    rigforge archon  plan|run|status
    rigforge review                     # show questions, gaps, status snapshot
    rigforge questions [--json]
    rigforge gaps [--json]
    rigforge mcp-serve                  # boot the MCP server

Every command honours the global ``--cwd`` option to override project-root
discovery and ``--json`` (where supported) to emit machine-readable output.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

import rigforge
from rigforge.context import ProjectContext
from rigforge.gaps import GAPS, RESOLVED_GAPS, as_dicts as gaps_as_dicts, resolved_as_dicts
from rigforge.gates import (
    GateResult,
    HARD_BLOCK,
    gate_ci_workflow,
    gate_config_valid,
    gate_contract_schema,
    gate_contracts_present,
    gate_python_version,
    gate_repo_layout,
    gate_ruff,
)
from rigforge.harness import ArchonHarness, PHASES
from rigforge.ledger import ExecutionLedger
from rigforge.proof import ProofPacket
from rigforge.questions import QUESTIONS, as_dicts as questions_as_dicts


CI_WORKFLOW_TEMPLATE = "ci-workflow.yml"


# ── Helpers ─────────────────────────────────────────────────────────────


def _ctx(click_ctx: click.Context) -> ProjectContext:
    return click_ctx.obj["project"]


def _is_json(click_ctx: click.Context) -> bool:
    return bool(click_ctx.obj.get("json"))


def _emit(click_ctx: click.Context, text_renderer, json_payload):
    """Emit either the text rendering or the JSON payload."""
    if _is_json(click_ctx):
        click.echo(json.dumps(json_payload, indent=2, default=str))
    else:
        text_renderer()


def _verifier_identity() -> str:
    return (
        os.environ.get("RIGFORGE_VERIFIER")
        or os.environ.get("GITHUB_ACTOR")
        or os.environ.get("USER")
        or "unknown"
    )


def _ensure_gitignore_entry(root: Path, entry: str) -> None:
    """Append ``entry`` to the project's ``.gitignore`` if not already present.

    Idempotent and best-effort — used to keep the auto-generated signing key
    out of version control.
    """
    gitignore = root / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    lines = {ln.strip() for ln in existing.splitlines()}
    if entry.strip() in lines:
        return
    block = "" if existing.endswith("\n") or not existing else "\n"
    block += f"\n# RIGForge signing key (auto-generated, never commit)\n{entry}\n"
    with gitignore.open("a", encoding="utf-8") as fh:
        fh.write(block)


# ── Root group ──────────────────────────────────────────────────────────


@click.group()
@click.option("--cwd", "cwd", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Override project-root discovery; treat this dir as the starting point.")
@click.option("--json", "json_mode", is_flag=True, default=False,
              help="Emit machine-readable JSON where supported.")
@click.version_option(rigforge.__version__, prog_name="rigforge")
@click.pass_context
def main(click_ctx: click.Context, cwd: Path | None, json_mode: bool):
    """RIGForge — Deterministic 7-Phase Agentic Engineering Platform."""
    click_ctx.ensure_object(dict)
    click_ctx.obj["project"] = ProjectContext.discover(cwd)
    click_ctx.obj["json"] = json_mode


# ── init ────────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def init(click_ctx: click.Context):
    """Scaffold the standard RIGForge directory layout in the current project."""
    ctx = _ctx(click_ctx)
    created: list[str] = []
    for d in (ctx.proofs_dir, ctx.contracts_dir / "v1", ctx.ledger_dir, ctx.docs_dir):
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d.relative_to(ctx.root)))

    cfg = ctx.config_file
    if not cfg.exists():
        from rigforge.config import default_config_yaml
        cfg.write_text(default_config_yaml(ctx.root.name))
        created.append(str(cfg.relative_to(ctx.root)))

    # Auto-generate a per-project signing key so ProofPackets are tamper-evident
    # by default (G006). Gitignored so the secret never gets committed.
    from rigforge.config import ensure_signing_key

    key_path, key_created = ensure_signing_key(ctx.root)
    if key_created:
        created.append(str(key_path.relative_to(ctx.root)))
    _ensure_gitignore_entry(ctx.root, ".rigforge/")

    payload = {"root": str(ctx.root), "created": created, "ok": True}
    _emit(
        click_ctx,
        lambda: (
            click.echo(f"📁 RIGForge project at: {ctx.root}"),
            click.echo("  created:" if created else "  nothing to do (already initialized)"),
            *(click.echo(f"    + {p}") for p in created),
        ),
        payload,
    )


# ── doctor ──────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def doctor(click_ctx: click.Context):
    """Validate Python version, repo layout, contracts, CI, and lint readiness."""
    ctx = _ctx(click_ctx)
    checks: list[GateResult] = [
        gate_python_version(),
        gate_repo_layout(ctx),
        gate_ci_workflow(ctx),
        gate_contracts_present(ctx),
        gate_contract_schema(ctx),
        gate_config_valid(ctx),
        gate_ruff(ctx),
    ]
    blocking_failed = [c for c in checks if not c.passed and c.severity == HARD_BLOCK]
    payload = {
        "root": str(ctx.root),
        "ok": not blocking_failed,
        "checks": [c.to_dict() for c in checks],
    }

    def render():
        click.echo(f"🩺 RIGForge doctor — {ctx.root}")
        for c in checks:
            icon = "✅" if c.passed else ("❌" if c.severity == HARD_BLOCK else "⚠️")
            click.echo(f"  {icon} {c.name:24s} [{c.severity:11s}] {c.detail}")
        click.echo()
        click.echo("✅ Doctor OK." if not blocking_failed else "❌ Blocking failures present.")

    _emit(click_ctx, render, payload)
    if blocking_failed:
        sys.exit(1)


# ── status ──────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def status(click_ctx: click.Context):
    """Show current phase, sealed status, and blockers."""
    ctx = _ctx(click_ctx)
    harness = ArchonHarness(ctx)
    phase_status = harness.status()

    blockers_file = ctx.docs_dir / "BLOCKERS.md"
    blockers_text = ""
    if blockers_file.exists():
        content = blockers_file.read_text().strip()
        if content and content.lower() not in ("none currently.", "none", ""):
            blockers_text = content

    payload = {
        "root": str(ctx.root),
        "phases": {str(k): v for k, v in phase_status.items()},
        "blockers": blockers_text or None,
    }

    def render():
        click.echo("RIGForge Phase Status")
        click.echo("=" * 50)
        for p, info in phase_status.items():
            icon = "✅ sealed" if info["sealed"] else "⬜ unsealed"
            extra = ""
            if info.get("sealed") and "integrity_ok" in info:
                extra = "" if info["integrity_ok"] else "  ⚠️  integrity FAIL"
            click.echo(f"  Phase {p}: {info['name']:40s} {icon}{extra}")
        click.echo()
        if blockers_text:
            click.echo("⚠️  Blockers:")
            click.echo(blockers_text)
        else:
            click.echo("✅ No blockers")

    _emit(click_ctx, render, payload)


# ── run ─────────────────────────────────────────────────────────────────


@main.command()
@click.argument("phase", type=click.IntRange(1, 7))
@click.option("--dry-run", is_flag=True, default=False,
              help="Plan only; do not execute gates or mutate state.")
@click.pass_context
def run(click_ctx: click.Context, phase: int, dry_run: bool):
    """Run phase N (1-7): executes that phase's deterministic gate bundle."""
    ctx = _ctx(click_ctx)
    harness = ArchonHarness(ctx)
    verifier = _verifier_identity()
    result = harness.run(phase, dry_run=dry_run, verifier=verifier)
    payload = result.to_dict()

    def render():
        mode = " (dry-run)" if dry_run else ""
        click.echo(f"🔧 Running Phase {phase}: {PHASES[phase]}{mode}")
        if dry_run:
            click.echo("  Plan:")
            for step in harness.plan(phase):
                click.echo(f"    • {step.name}: {step.description}")
            click.echo("✅ Dry-run complete (no state changed).")
            return
        for g in result.gates:
            icon = "✅" if g.passed else ("❌" if g.severity == HARD_BLOCK else "⚠️")
            click.echo(f"  {icon} {g.name:24s} [{g.severity:11s}] {g.detail}")
        if result.ok:
            click.echo(f"✅ Phase {phase} ready. Run `rigforge seal {phase} --evidence '...'`.")
        else:
            click.echo(f"❌ Phase {phase} has {len(result.blockers)} blocking failure(s).")

    _emit(click_ctx, render, payload)
    if not dry_run and not result.ok:
        sys.exit(1)


# ── seal ────────────────────────────────────────────────────────────────


@main.command()
@click.argument("phase", type=click.IntRange(1, 7))
@click.option("--artifact", "artifacts", multiple=True, type=click.Path(path_type=Path),
              help="Path to an artifact to pin into the proof packet (repeatable).")
@click.option("--evidence", default=None, help="Human-readable evidence summary.")
@click.option("--verifier", default=None, help="Override verifier identity for the packet.")
@click.option("--force", is_flag=True, default=False,
              help="Seal even if blocking gates fail (records the failures in the packet).")
@click.pass_context
def seal(click_ctx: click.Context, phase: int, artifacts: tuple[Path, ...],
         evidence: str | None, verifier: str | None, force: bool):
    """Seal phase N: writes a ProofPacket with checksums + run envelope."""
    ctx = _ctx(click_ctx)
    harness = ArchonHarness(ctx)
    actor = verifier or _verifier_identity()

    run_result = harness.run(phase, dry_run=False, verifier=actor)
    if run_result.blockers and not force:
        payload = {
            "ok": False,
            "phase": phase,
            "blockers": [b.to_dict() for b in run_result.blockers],
            "message": "blocking gates failed; pass --force to seal anyway.",
        }
        _emit(
            click_ctx,
            lambda: click.echo(
                f"❌ Refusing to seal Phase {phase}: "
                f"{len(run_result.blockers)} blocking gate(s) failed. Use --force to override."
            ),
            payload,
        )
        sys.exit(1)

    packet = harness.seal(
        phase,
        verifier=actor,
        evidence=evidence,
        artifacts=list(artifacts),
        gates=run_result.gates,
        envelope=run_result.envelope,
    )
    proof_path = ctx.proof_file(phase)
    payload = {
        "ok": True,
        "phase": phase,
        "proof": str(proof_path.relative_to(ctx.root)),
        "packet_sha256": packet.packet_sha256,
        "artifact_count": len(packet.artifacts),
    }
    _emit(
        click_ctx,
        lambda: click.echo(
            f"🔒 Sealed Phase {phase}: {PHASES[phase]}\n"
            f"   {proof_path.relative_to(ctx.root)}  sha256={packet.packet_sha256[:12]}…"
        ),
        payload,
    )


# ── verify ──────────────────────────────────────────────────────────────


@main.command()
@click.option("--strict", is_flag=True, default=False,
              help="Require integrity hash + phase-order continuity (no gaps).")
@click.option("--require-signature", is_flag=True, default=False,
              help="Also verify the HMAC signature on each proof packet (G006).")
@click.pass_context
def verify(click_ctx: click.Context, strict: bool, require_signature: bool):
    """Verify all sealed phases: schema, integrity hash, phase order, signature."""
    ctx = _ctx(click_ctx)
    from rigforge.config import load_config

    try:
        cfg = load_config(ctx)
    except ValueError:
        cfg = None
    signing_key = cfg.resolve_signing_key(ctx.root) if cfg is not None else None
    if require_signature and signing_key is None:
        click.echo("❌ --require-signature was set but no signing key is configured "
                   "(set RIGFORGE_SIGNING_KEY or configure signing.key_file in rigforge.yaml).")
        sys.exit(2)

    phase_reports: list[dict] = []
    sealed_phases: list[int] = []
    errors: list[str] = []

    for p, name in PHASES.items():
        path = ctx.proof_file(p)
        if not path.exists():
            phase_reports.append({"phase": p, "name": name, "sealed": False})
            continue
        try:
            packet = ProofPacket.load(path)
        except Exception as exc:  # noqa: BLE001
            phase_reports.append({"phase": p, "name": name, "sealed": True, "error": str(exc)})
            errors.append(f"Phase {p}: schema invalid — {exc}")
            continue
        sealed_phases.append(p)
        integrity = packet.verify_integrity()
        signature_ok: bool | None = None
        if signing_key is not None and packet.signature:
            signature_ok = packet.verify_signature(signing_key)
        report = {
            "phase": p,
            "name": name,
            "sealed": True,
            "verifier": packet.verifier,
            "artifact_count": len(packet.artifacts),
            "integrity_ok": integrity,
            "signed": bool(packet.signature),
            "signature_ok": signature_ok,
        }
        if strict and not integrity and packet.schema_version != "0.0.0":
            errors.append(f"Phase {p}: integrity hash mismatch.")
        if require_signature:
            if not packet.signature:
                errors.append(f"Phase {p}: proof packet is not signed.")
            elif signature_ok is False:
                errors.append(f"Phase {p}: signature does not verify.")
        phase_reports.append(report)

    if strict and sealed_phases:
        expected = list(range(1, max(sealed_phases) + 1))
        gaps = sorted(set(expected) - set(sealed_phases))
        if gaps:
            errors.append(f"phase-order gaps (strict): missing {gaps}")

    ok = not errors
    payload = {"ok": ok, "strict": strict, "phases": phase_reports, "errors": errors}

    def render():
        click.echo("🔍 Verifying all phases...")
        for r in phase_reports:
            if not r["sealed"]:
                click.echo(f"  Phase {r['phase']}: ❌ {r['name']} — NOT SEALED")
            elif "error" in r:
                click.echo(f"  Phase {r['phase']}: ⚠️  {r['name']} — schema error: {r['error']}")
            else:
                tag = "✅" if r["integrity_ok"] else "⚠️"
                click.echo(
                    f"  Phase {r['phase']}: {tag} {r['name']} "
                    f"(verifier={r['verifier']}, artifacts={r['artifact_count']})"
                )
        click.echo()
        if ok:
            click.echo("✅ Verify complete.")
        else:
            click.echo("❌ Verify failed:")
            for e in errors:
                click.echo(f"   - {e}")

    _emit(click_ctx, render, payload)
    if not ok:
        sys.exit(1)


# ── demo (live tamper-detection wedge) ───────────────────────────────────


@main.command("demo")
@click.pass_context
def demo(click_ctx: click.Context):
    """Run the live tamper-detection demo: seal, tamper, and catch the forgery.

    Runs the REAL proof machinery end-to-end in a temp dir with an ephemeral
    signing key: an agent claims success, RIGForge seals a signed ProofPacket,
    the artifact is tampered and the integrity hash re-forged to hide it, then
    ``verify`` catches the forgery via the HMAC signature check. Self-contained,
    no external deps, leaves no state behind.
    """
    from rigforge.demo import render_demo, run_demo

    result = run_demo()
    if _is_json(click_ctx):
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        render_demo(result)
    # The whole point of the demo is that the forgery is caught. If the
    # cryptographic invariant ever breaks, fail loudly rather than lie.
    if not result.forgery_caught:
        sys.exit(1)


# ── contract group ──────────────────────────────────────────────────────


@main.group("contract")
def contract_group():
    """DoneContract operations: list, validate, create, inspect."""


@contract_group.command("list")
@click.pass_context
def contract_list(click_ctx: click.Context):
    """List contract YAML files under contracts/."""
    ctx = _ctx(click_ctx)
    files = sorted(str(f.relative_to(ctx.root)) for f in ctx.contracts_dir.rglob("*.yaml")) \
        if ctx.contracts_dir.exists() else []
    payload = {"count": len(files), "contracts": files}
    _emit(
        click_ctx,
        lambda: (
            click.echo(f"📋 Found {len(files)} contract file(s):"),
            *(click.echo(f"  - {f}") for f in files),
        ),
        payload,
    )


@contract_group.command("validate")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def contract_validate(click_ctx: click.Context, file: Path):
    """Validate a YAML file against the DoneContract schema."""
    import yaml as _yaml

    from contracts.v1 import DoneContract

    data = _yaml.safe_load(file.read_text()) or {}
    try:
        dc = DoneContract(**data)
        payload = {"ok": True, "file": str(file), "sealed": dc.is_sealed()}
        _emit(
            click_ctx,
            lambda: click.echo(f"✅ {file} — valid (sealed={dc.is_sealed()})"),
            payload,
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        payload = {"ok": False, "file": str(file), "error": message}
        _emit(click_ctx, lambda: click.echo(f"❌ {file} — invalid: {message}"), payload)
        sys.exit(1)


@contract_group.command("create")
@click.option("--studio", required=True)
@click.option("--lane", required=True)
@click.option("--objective", default=None)
@click.option("--generator", default="PyCode")
@click.option("--verifier", default="Codex CLI")
@click.option("--evaluator", default="Human")
@click.option("--out", "out_path", type=click.Path(path_type=Path), default=None,
              help="Path to write the contract YAML.")
@click.pass_context
def contract_create(click_ctx: click.Context, studio: str, lane: str, objective: str | None,
                    generator: str, verifier: str, evaluator: str, out_path: Path | None):
    """Create a new DoneContract YAML with the GEV triad pre-filled."""
    from datetime import datetime, timezone

    import yaml as _yaml

    from contracts.v1 import DoneContract, VerifierPackage
    from contracts.v1.models.verifier_package import AgentRole

    agent_map = {r.value: r for r in AgentRole}
    dc = DoneContract(
        studio=studio,
        lane=lane,
        objective=objective,
        created_at=datetime.now(timezone.utc),
        verifier_package=VerifierPackage(
            generator=agent_map[generator],
            verifier=agent_map[verifier],
            evaluator=agent_map[evaluator],
        ),
    )
    yaml_dict = dc.to_yaml_dict()
    text = _yaml.safe_dump(yaml_dict, sort_keys=False)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text)
    payload = {"ok": True, "contract": yaml_dict, "written_to": str(out_path) if out_path else None}
    _emit(
        click_ctx,
        lambda: (
            click.echo(f"📝 Created DoneContract for studio={studio} lane={lane}"),
            click.echo(text),
            *([click.echo(f"  → wrote {out_path}")] if out_path else []),
        ),
        payload,
    )


@contract_group.command("inspect")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def contract_inspect(click_ctx: click.Context, file: Path):
    """Inspect a contract: artifacts, criteria, GEV triad, gates."""
    import yaml as _yaml

    from contracts.v1 import DoneContract

    data = _yaml.safe_load(file.read_text()) or {}
    try:
        dc = DoneContract(**data)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"❌ {file} — invalid: {exc}")
        sys.exit(1)
    payload = {
        "file": str(file),
        "studio": dc.studio,
        "lane": dc.lane,
        "sealed": dc.is_sealed(),
        "required_artifacts": [a.name for a in dc.required_artifacts],
        "acceptance_criteria": len(dc.acceptance_criteria),
        "forbidden_actions": len(dc.forbidden_actions),
        "blocking_artifacts": dc.blocking_artifact_count(),
        "blocking_criteria": dc.blocking_criteria_count(),
    }

    def render():
        click.echo(f"🔎 {file}")
        click.echo(f"   studio={dc.studio}  lane={dc.lane}  sealed={dc.is_sealed()}")
        click.echo(f"   required_artifacts={len(dc.required_artifacts)}  "
                   f"blocking={dc.blocking_artifact_count()}")
        click.echo(f"   acceptance_criteria={len(dc.acceptance_criteria)}  "
                   f"blocking={dc.blocking_criteria_count()}")
        click.echo(f"   forbidden_actions={len(dc.forbidden_actions)}")
        if dc.verifier_package:
            vp = dc.verifier_package
            click.echo(f"   GEV: gen={vp.generator.value}  "
                       f"verify={vp.verifier.value}  eval={vp.evaluator.value}")

    _emit(click_ctx, render, payload)


# ── archon group ────────────────────────────────────────────────────────


@main.group("archon")
def archon_group():
    """Archon harness orchestration: plan, run, status."""


@archon_group.command("plan")
@click.argument("phase", type=click.IntRange(1, 7))
@click.pass_context
def archon_plan(click_ctx: click.Context, phase: int):
    """Show the plan (gate sequence) the harness would execute for PHASE."""
    ctx = _ctx(click_ctx)
    harness = ArchonHarness(ctx)
    steps = harness.plan(phase)
    payload = {"phase": phase, "steps": [{"name": s.name, "description": s.description} for s in steps]}
    _emit(
        click_ctx,
        lambda: (
            click.echo(f"📐 Archon plan for Phase {phase}: {PHASES[phase]}"),
            *(click.echo(f"  {i+1}. {s.name} — {s.description}") for i, s in enumerate(steps)),
        ),
        payload,
    )


@archon_group.command("run")
@click.argument("phase", type=click.IntRange(1, 7))
@click.option("--dry-run", is_flag=True, default=False)
@click.pass_context
def archon_run(click_ctx: click.Context, phase: int, dry_run: bool):
    """Drive the harness through PHASE (alias of `rigforge run PHASE`)."""
    click_ctx.invoke(run, phase=phase, dry_run=dry_run)


@archon_group.command("status")
@click.pass_context
def archon_status(click_ctx: click.Context):
    """Show the ledger tail and per-phase seal status."""
    ctx = _ctx(click_ctx)
    harness = ArchonHarness(ctx)
    ledger_tail = ExecutionLedger(ctx.ledger_file).read(limit=10)
    payload = {"phases": harness.status(), "ledger_tail": ledger_tail}

    def render():
        click.echo("🛰️  Archon status")
        for p, info in harness.status().items():
            tag = "sealed" if info["sealed"] else "open"
            click.echo(f"  Phase {p}: {info['name']:40s} {tag}")
        click.echo()
        click.echo(f"  Ledger ({len(ledger_tail)} recent event(s)):")
        for e in ledger_tail:
            click.echo(f"    {e.get('ts', '?')}  {e.get('kind', '?'):20s}  {e.get('actor', '?')}")

    _emit(click_ctx, render, payload)


# ── review / questions / gaps ───────────────────────────────────────────


@main.command()
@click.pass_context
def review(click_ctx: click.Context):
    """Self-review: surface engineering questions, gaps, and platform status."""
    ctx = _ctx(click_ctx)
    harness = ArchonHarness(ctx)
    payload = {
        "questions": questions_as_dicts(),
        "gaps": gaps_as_dicts(),
        "status": harness.status(),
    }

    def render():
        click.echo("🪞  RIGForge self-review")
        click.echo(f"\n  {len(QUESTIONS)} engineering questions encoded "
                   f"(`rigforge questions` to list)")
        click.echo(f"  {len(GAPS)} tracked gaps (`rigforge gaps` to list)")
        click.echo("\n  Phase status:")
        for p, info in harness.status().items():
            tag = "✅" if info["sealed"] else "⬜"
            click.echo(f"    {tag} Phase {p}: {info['name']}")

    _emit(click_ctx, render, payload)


@main.command()
@click.pass_context
def questions(click_ctx: click.Context):
    """List the 20 senior-agentic-engineering questions."""
    payload = {"questions": questions_as_dicts()}
    _emit(
        click_ctx,
        lambda: (
            click.echo("📚 Senior-agentic-engineering questions:"),
            *(click.echo(f"  {q.number:2d}. [{q.category:14s}] {q.question}") for q in QUESTIONS),
        ),
        payload,
    )


@main.command()
@click.option("--all", "show_all", is_flag=True, default=False,
              help="Also list previously resolved gaps for the audit trail.")
@click.pass_context
def gaps(click_ctx: click.Context, show_all: bool):
    """List known platform gaps."""
    payload: dict = {"gaps": gaps_as_dicts()}
    if show_all:
        payload["resolved"] = resolved_as_dicts()

    def render():
        click.echo("🕳️  Known platform gaps:")
        if not GAPS:
            click.echo("  (none — all tracked gaps resolved)")
        for g in GAPS:
            click.echo(f"  {g.id} [{g.severity:8s}] {g.area:14s} {g.summary}")
        if show_all and RESOLVED_GAPS:
            click.echo("\n✅ Resolved:")
            for g in RESOLVED_GAPS:
                click.echo(f"  {g.id} [{g.severity:8s}] {g.area:14s} {g.summary}")

    _emit(click_ctx, render, payload)


# ── resume (G007) ───────────────────────────────────────────────────────


@main.command()
@click.pass_context
def resume(click_ctx: click.Context):
    """Re-run the most recent failed or unfinished phase (G007)."""
    ctx = _ctx(click_ctx)
    harness = ArchonHarness(ctx)
    target = harness.find_resumable()
    if target is None or not target.get("phase"):
        payload = {"ok": True, "resumed": False, "message": "nothing to resume"}
        _emit(click_ctx, lambda: click.echo("✅ Nothing to resume."), payload)
        return
    verifier = _verifier_identity()
    result = harness.resume(verifier=verifier)
    payload = {
        "ok": bool(result and result.ok),
        "resumed": True,
        "target": target,
        "result": result.to_dict() if result else None,
    }

    def render():
        click.echo(f"♻️  Resuming Phase {target['phase']} (reason: {target.get('reason')})")
        if result is None:
            click.echo("  nothing to do.")
            return
        for g in result.gates:
            icon = "✅" if g.passed else ("❌" if g.severity == HARD_BLOCK else "⚠️")
            click.echo(f"  {icon} {g.name:24s} [{g.severity:11s}] {g.detail}")
        click.echo("✅ Resume complete." if result.ok else "❌ Resume still has blockers.")

    _emit(click_ctx, render, payload)
    if result is not None and not result.ok:
        sys.exit(1)


# ── cockpit (G008) ──────────────────────────────────────────────────────


@main.command()
@click.option("--host", default=None, help="Override cockpit bind host.")
@click.option("--port", default=None, type=int, help="Override cockpit bind port.")
@click.option("--print", "print_only", is_flag=True, default=False,
              help="Render the cockpit HTML to stdout instead of starting the server.")
@click.pass_context
def cockpit(click_ctx: click.Context, host: str | None, port: int | None, print_only: bool):
    """Phase 7 cockpit: mission-control HTML view over phases + ledger (G008)."""
    ctx = _ctx(click_ctx)
    from rigforge.cockpit import build_cockpit_app, render_cockpit_html

    if print_only:
        click.echo(render_cockpit_html(ctx))
        return

    from rigforge.config import load_config
    cfg = load_config(ctx)
    bind_host = host or cfg.cockpit.host
    bind_port = port or cfg.cockpit.port
    try:
        app = build_cockpit_app(ctx)
        import uvicorn
    except ImportError as exc:
        click.echo(f"⚠️  Cockpit dependencies not installed: {exc}")
        click.echo("   Install with: pip install rigforge[mcp]")
        sys.exit(1)
    click.echo(f"🛸  RIGForge cockpit at http://{bind_host}:{bind_port}/")
    uvicorn.run(app, host=bind_host, port=bind_port)


# ── capability registry (G009) ──────────────────────────────────────────


@main.command("capabilities")
@click.pass_context
def capabilities(click_ctx: click.Context):
    """List registered capabilities (plugin gates) in the active registry.

    Capabilities are discovered from ``RIGFORGE_CAPABILITY_MODULES`` (a
    comma-separated list of importable modules whose ``@capability``
    decorators register into the process-wide registry).
    """
    from rigforge.registry import REGISTRY, discover_from_env

    imported = discover_from_env()
    payload = {"imported_modules": imported, "capabilities": REGISTRY.as_dicts()}

    def render():
        click.echo("🧩 Registered capabilities")
        if imported:
            click.echo(f"   (discovered from: {', '.join(imported)})")
        if not REGISTRY.all():
            click.echo("  (none registered — set RIGFORGE_CAPABILITY_MODULES to load plugins)")
        for cap in REGISTRY.all():
            scope = "all" if cap.phases == "*" else ",".join(str(p) for p in cap.phases)
            click.echo(f"  • {cap.name:24s} [{cap.severity:11s}] phases={scope}  {cap.description}")

    _emit(click_ctx, render, payload)


# ── goal harness (G010) ─────────────────────────────────────────────────


@main.command("goal")
@click.option("--phases", "phases_csv", default=None,
              help="Comma-separated phases to drive (e.g. '1,2'). Default: 1..--up-to.")
@click.option("--up-to", "up_to", type=click.IntRange(1, 7), default=None,
              help="Drive phases 1..N (ignored if --phases is given).")
@click.option("--done-artifact", "done_artifact", type=click.Path(path_type=Path), default=None,
              help="Done-condition: this artifact must exist for the goal to be proven.")
@click.option("--max-iterations", default=5, type=click.IntRange(1, 100),
              help="Hard cap on convergence passes (default 5).")
@click.option("--cost-per-iteration", default=0.0, type=float,
              help="Cost (USD) charged to the budget per pass (G005 enforcement).")
@click.option("--tokens-per-iteration", default=0, type=int,
              help="Tokens charged to the budget per pass (G005 enforcement).")
@click.pass_context
def goal(click_ctx: click.Context, phases_csv: str | None, up_to: int | None,
         done_artifact: Path | None, max_iterations: int,
         cost_per_iteration: float, tokens_per_iteration: int):
    """Converge-until-proven: drive phases until the done-condition is proven (G010).

    The done-condition is: every targeted phase runs without blocking failures
    AND (if --done-artifact is given) that artifact exists. On success the
    final phase is sealed with a ProofPacket. The loop is bounded by
    --max-iterations and the cost/token budget, so it can never spin forever.
    """
    ctx = _ctx(click_ctx)
    from rigforge.goal import GoalHarness

    if phases_csv:
        phase_list = [int(p.strip()) for p in phases_csv.split(",") if p.strip()]
    elif up_to is not None:
        phase_list = list(range(1, up_to + 1))
    else:
        phase_list = [1]
    for p in phase_list:
        if not 1 <= p <= 7:
            click.echo(f"❌ phase {p} out of range (1-7)")
            sys.exit(2)

    if done_artifact is not None:
        target = done_artifact

        def done_check(c: ProjectContext) -> bool:
            return target.exists()
    else:
        def done_check(c: ProjectContext) -> bool:
            return True  # done == all phases ran clean

    verifier = _verifier_identity()
    gh = GoalHarness(ctx)
    result = gh.converge(
        phases=phase_list,
        done_check=done_check,
        verifier=verifier,
        max_iterations=max_iterations,
        cost_per_iteration=cost_per_iteration,
        tokens_per_iteration=tokens_per_iteration,
    )
    payload = result.to_dict()

    def render():
        click.echo(f"🎯 Goal harness over phases {phase_list}")
        for it in result.iterations:
            tag = "✅ done" if it.done else (
                f"❌ blocked@{it.blocked_phase}" if it.blocked_phase else "↻ pass"
            )
            click.echo(f"  iteration {it.index}: {tag} "
                       f"(cost=${it.cost_usd:.4f} tokens={it.tokens})")
        click.echo()
        if result.proven:
            sha = result.proof.packet_sha256[:12] if result.proof else "—"
            click.echo(f"✅ Goal proven in {result.iteration_count} iteration(s). "
                       f"Sealed phase {phase_list[-1]} sha256={sha}…")
        else:
            click.echo(f"❌ Goal not proven (stop_reason={result.stop_reason}, "
                       f"{result.iteration_count} iteration(s)).")

    _emit(click_ctx, render, payload)
    if not result.proven:
        sys.exit(1)


# ── full-stack cockpit (G011) ────────────────────────────────────────────


@main.command("serve")
@click.option("--host", default=None, help="Override cockpit bind host (default 127.0.0.1).")
@click.option("--port", default=None, type=int, help="Override cockpit bind port.")
@click.option("--auth-token", default=None,
              help="Bearer token required for /api/* (reuses the MCP token logic). "
                   "Falls back to RIGFORGE_MCP_TOKEN / rigforge.yaml.")
@click.option("--require-auth", is_flag=True, default=False,
              help="Refuse to start without a token (off by default for the local dashboard).")
@click.pass_context
def serve(click_ctx: click.Context, host: str | None, port: int | None,
          auth_token: str | None, require_auth: bool):
    """Boot the full-stack cockpit: REST API + single-page app (G011)."""
    ctx = _ctx(click_ctx)
    from rigforge.config import load_config

    cfg = load_config(ctx)
    bind_host = host or cfg.cockpit.host
    bind_port = port or cfg.cockpit.port
    token = auth_token if auth_token is not None else cfg.resolve_mcp_token(ctx.root)

    try:
        from rigforge.webapp import build_app
        from rigforge.mcp_server import MCPInsecureBindError

        try:
            app = build_app(ctx, auth_token=token, allow_insecure=not require_auth)
        except MCPInsecureBindError as exc:
            click.echo(f"❌ {exc}")
            sys.exit(2)
        import uvicorn
    except ImportError as exc:
        click.echo(f"⚠️  Cockpit dependencies not installed: {exc}")
        click.echo("   Install with: pip install rigforge[mcp]")
        sys.exit(1)

    click.echo(f"🛸  RIGForge full-stack cockpit at http://{bind_host}:{bind_port}/")
    click.echo(f"    API: /api/status /api/phases /api/verify /api/contracts "
               f"/api/proof/{{n}} /api/ledger/stream")
    click.echo(f"    Auth: {'bearer-token required' if token else 'open (local dashboard)'}")
    uvicorn.run(app, host=bind_host, port=bind_port)


# ── MCP server ──────────────────────────────────────────────────────────


@main.command("mcp-serve")
@click.option("--host", default=None, help="Host to bind MCP server (HTTP transport)")
@click.option("--port", default=None, type=int, help="Port for MCP server (HTTP transport)")
@click.option("--services", default=None,
              help="Comma-separated MCP services to start")
@click.option("--transport", type=click.Choice(["http", "stdio"], case_sensitive=False),
              default=None, help="Transport protocol (default from rigforge.yaml).")
@click.option("--auth-token", default=None,
              help="Shared bearer token required for HTTP requests (G003). "
                   "Falls back to RIGFORGE_MCP_TOKEN / rigforge.yaml.")
@click.option("--allow-insecure", is_flag=True, default=False,
              help="Permit the HTTP transport to start with NO auth token (G003). "
                   "Off by default; exposes the contract tools to any caller.")
@click.pass_context
def mcp_serve(click_ctx: click.Context, host: str | None, port: int | None,
              services: str | None, transport: str | None, auth_token: str | None,
              allow_insecure: bool):
    """Boot MCP servers (Recall, Stitch, Archon, DeerFlow) over HTTP or stdio."""
    ctx = _ctx(click_ctx)
    from rigforge.config import load_config

    cfg = load_config(ctx)
    transport_kind = (transport or cfg.mcp.transport).lower()
    service_list = (
        [s.strip() for s in services.split(",")] if services else list(cfg.mcp.services)
    )

    if transport_kind == "stdio":
        from rigforge.mcp_server import serve_stdio
        click.echo(
            f"🛰️  RIGForge MCP stdio transport ready (services={','.join(service_list)})",
            err=True,
        )
        serve_stdio()
        return

    bind_host = host or cfg.mcp.host
    bind_port = port or cfg.mcp.port
    token = auth_token if auth_token is not None else cfg.resolve_mcp_token(ctx.root)
    click.echo(f"🚀 Starting RIGForge MCP server on {bind_host}:{bind_port}")
    click.echo(f"   Services: {', '.join(service_list)}")
    if token:
        click.echo("   Auth: bearer-token required (G003)")
    try:
        from rigforge.mcp_server import create_mcp_server, MCPInsecureBindError

        try:
            server = create_mcp_server(
                host=bind_host, port=bind_port, services=service_list,
                auth_token=token, allow_insecure=allow_insecure,
            )
        except MCPInsecureBindError as exc:
            click.echo(f"❌ {exc}")
            sys.exit(2)
        click.echo(f"   MCP server ready at http://{bind_host}:{bind_port}")
        click.echo("   Tools: gev.contract_create/validate/list, gev.phase_status, gev.proof_seal")
        click.echo("   Press Ctrl+C to stop.")
        import uvicorn

        uvicorn.run(server, host=bind_host, port=bind_port)
    except ImportError as exc:
        click.echo(f"⚠️  MCP dependencies not installed: {exc}")
        click.echo("   Install with: pip install rigforge[mcp]")
        sys.exit(1)


if __name__ == "__main__":
    main()

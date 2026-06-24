"""forge --build command: execute intent -> lattice -> troika."""
from __future__ import annotations

import click


@click.command(name="forge")
@click.argument("intent", nargs=-1, type=str, required=False)
@click.option("--dry-run", is_flag=True, help="Parse and route without executing.")
@click.option("--model", default="blackwell-minimax", help="Model routing hint.")
@click.pass_context
def forge_cmd(ctx: click.Context, intent: tuple[str, ...], dry_run: bool, model: str):
    """Execute a natural-language intent through the RIG lattice + troika.

    INTENT is the task description (may be multiple words).
    """
    from rigforge.forge import forge as _forge

    text = " ".join(intent) if intent else "forge status"
    result = _forge(text, dry_run=dry_run, context={"model": model})

    if ctx.obj.get("json"):
        import json
        click.echo(json.dumps(result.as_dict(), indent=2, default=str))
    else:
        click.echo(f"    cell: {result.cell_id}")
        click.echo(f"    bms:  {result.bms_score}")
        click.echo(f"    arch: {result.archetype}")
        click.echo(f" tools: {result.tools}")
        click.echo(f" troika: {'ok' if result.troika_ok else 'FAIL'}")
        if result.artifact:
            click.echo(f" artifact: {result.artifact}")
        click.echo(f"status: {'PASS' if result.ok else 'FAIL'}")

    ctx.exit(0 if result.ok else 1)

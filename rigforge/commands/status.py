"""Lattice status command."""
from __future__ import annotations

import click


@click.command(name="status")
@click.option("--cell", "cell_id", default=None, help="Show a specific cell.")
@click.pass_context
def status_cmd(ctx: click.Context, cell_id: str | None):
    """Show RIG Forge status — phase, lattice, contracts."""
    from rigforge.triple_diamond import LATTICE, Cell
    from rigforge.lattice.router import LatticeRouter

    if cell_id:
        c = LATTICE.get(cell_id)
        if c:
            click.echo(f"cell:    {c.cell_id}")
            click.echo(f"level:   {c.level.value}")
            click.echo(f"diamond: {c.diamond.value}")
            click.echo(f"mode:    {c.mode.value}")
            click.echo(f"step:    {c.step.value}")
        else:
            click.echo(f"cell not found: {cell_id}", err=True)
            ctx.exit(1)
    else:
        router = LatticeRouter()
        click.echo(f"lattice cells: {len(LATTICE)}")
        click.echo(f"chief block:   {len(router.chief_block())} cells")
        click.echo(f"status: READY")

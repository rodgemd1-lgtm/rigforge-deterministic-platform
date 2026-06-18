"""
RIGForge project configuration loader.

``rigforge init`` scaffolds a ``rigforge.yaml`` file but, until now, no
module actually consumed it. ``RigForgeConfig`` is the typed, validated
view of that file: budgets, signing key reference, MCP options, scheduler
options, and cockpit options.

The loader is intentionally tolerant — a missing file resolves to the
defaults so existing projects keep working without re-running ``init``.

Environment-variable overrides:

* ``RIGFORGE_SIGNING_KEY`` / ``RIGFORGE_SIGNING_KEY_FILE``
* ``RIGFORGE_MCP_TOKEN`` / ``RIGFORGE_MCP_TOKEN_FILE``
* ``RIGFORGE_MAX_PARALLEL_GATES``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from rigforge.context import ProjectContext


CONFIG_SCHEMA_VERSION = "1.1.0"

# Conventional location for the auto-generated per-project signing key (G006).
# Gitignored so the key never leaves the machine.
DEFAULT_SIGNING_KEY_PATH = Path(".rigforge") / "signing.key"


class BudgetConfig(BaseModel):
    """Hard ceilings the harness enforces at runtime (see G005)."""

    max_cost_usd: float = Field(default=2.0, ge=0.0)
    max_tokens: int = Field(default=200_000, ge=0)
    max_runtime_minutes: int = Field(default=20, ge=1)


class MCPConfig(BaseModel):
    """Configuration for ``rigforge mcp-serve``."""

    # Bind to loopback by default (G003): exposing the contract-tool RPC surface
    # on a routable interface must be a deliberate, explicit choice.
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    transport: str = Field(default="http", description="http | stdio")
    services: list[str] = Field(
        default_factory=lambda: ["recall", "stitch", "archon", "deerflow"]
    )
    # Shared bearer token for HTTP transport (G003). Empty string disables auth.
    token: str = ""
    token_file: str | None = None

    @field_validator("transport")
    @classmethod
    def _transport_kind(cls, v: str) -> str:
        v = v.lower()
        if v not in {"http", "stdio"}:
            raise ValueError("transport must be 'http' or 'stdio'")
        return v


class SchedulerConfig(BaseModel):
    """Multi-agent scheduling (G002)."""

    max_parallel_gates: int = Field(default=1, ge=1, le=32)
    agents: list[str] = Field(default_factory=lambda: ["local"])


class SigningConfig(BaseModel):
    """Cryptographic signing of ProofPackets (G006).

    Signing is **on by default**: ``rigforge init`` auto-generates a per-project
    key at ``.rigforge/signing.key`` (gitignored). When no key can be resolved
    the harness falls back gracefully to unsigned seals so existing projects
    keep working, but a configured key makes tampering detectable.
    """

    enabled: bool = True
    key_file: str | None = None
    # If both ``key`` and ``key_file`` are unset and ``enabled`` is True,
    # the env var ``RIGFORGE_SIGNING_KEY`` then the conventional
    # ``.rigforge/signing.key`` are consulted at sign time.
    require_on_verify: bool = False


class CockpitConfig(BaseModel):
    """Phase 7 cockpit UI (G008)."""

    host: str = "127.0.0.1"
    port: int = Field(default=8770, ge=1, le=65535)


class EvalLoopConfig(BaseModel):
    """Evaluator-Optimizer loop config (research priority #2).

    Opt-in: ``enabled`` defaults to False so existing runs are unchanged.
    When enabled via config or the ``--eval-loop`` CLI flag, each gate runs
    through a score → retry → escalate cycle before its result is sealed.
    ``max_retries`` bounds the loop (no infinite spin).
    """

    enabled: bool = False
    max_retries: int = Field(default=3, ge=1, le=10)


class RigForgeConfig(BaseModel):
    """Typed view of ``rigforge.yaml``."""

    schema_version: str = CONFIG_SCHEMA_VERSION
    project: str = "rigforge-project"
    phases: int = Field(default=7, ge=1, le=7)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    signing: SigningConfig = Field(default_factory=SigningConfig)
    cockpit: CockpitConfig = Field(default_factory=CockpitConfig)
    eval_loop: EvalLoopConfig = Field(default_factory=EvalLoopConfig)

    # ── Resolved helpers (apply env-var overrides) ─────────────────────

    def resolve_signing_key(self, root: Path | None = None) -> bytes | None:
        """Return the signing key as bytes, or None if unavailable.

        Resolution order: env var ``RIGFORGE_SIGNING_KEY`` →
        ``RIGFORGE_SIGNING_KEY_FILE`` → ``signing.key_file`` (relative to
        ``root`` if given).
        """
        env_key = os.environ.get("RIGFORGE_SIGNING_KEY")
        if env_key:
            return env_key.encode("utf-8")
        env_file = os.environ.get("RIGFORGE_SIGNING_KEY_FILE")
        if env_file and Path(env_file).exists():
            return Path(env_file).read_bytes().strip()
        if self.signing.key_file:
            p = Path(self.signing.key_file)
            if root is not None and not p.is_absolute():
                p = root / p
            if p.exists():
                return p.read_bytes().strip()
        # Conventional auto-generated key (written by ``rigforge init``).
        if root is not None:
            default_key = root / DEFAULT_SIGNING_KEY_PATH
            if default_key.exists():
                return default_key.read_bytes().strip()
        return None

    def resolve_mcp_token(self, root: Path | None = None) -> str | None:
        env_tok = os.environ.get("RIGFORGE_MCP_TOKEN")
        if env_tok:
            return env_tok
        env_file = os.environ.get("RIGFORGE_MCP_TOKEN_FILE")
        if env_file and Path(env_file).exists():
            return Path(env_file).read_text().strip()
        if self.mcp.token:
            return self.mcp.token
        if self.mcp.token_file:
            p = Path(self.mcp.token_file)
            if root is not None and not p.is_absolute():
                p = root / p
            if p.exists():
                return p.read_text().strip()
        return None

    def resolve_parallelism(self) -> int:
        env_val = os.environ.get("RIGFORGE_MAX_PARALLEL_GATES")
        if env_val and env_val.isdigit():
            return max(1, int(env_val))
        return self.scheduler.max_parallel_gates


# ── Loader ─────────────────────────────────────────────────────────────


def load_config(ctx: ProjectContext) -> RigForgeConfig:
    """Load and validate ``rigforge.yaml`` from a project context.

    Returns defaults if the file is missing. Raises ``ValueError`` for an
    invalid file so callers (CLI, doctor gate) can surface a precise error.
    """
    path = ctx.config_file
    if not path.exists():
        return RigForgeConfig()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML parse error: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level value must be a mapping")
    try:
        return RigForgeConfig(**raw)
    except Exception as exc:  # noqa: BLE001 — surface as ValueError
        raise ValueError(f"{path}: invalid rigforge.yaml: {exc}") from exc


def ensure_signing_key(root: Path) -> tuple[Path, bool]:
    """Ensure a per-project signing key exists at ``.rigforge/signing.key``.

    Returns ``(path, created)`` where ``created`` is True only when a new key
    was generated this call. The key is 32 random bytes, hex-encoded, written
    with ``0o600`` permissions. Idempotent: an existing key is left untouched.
    """
    import os as _os
    import secrets

    key_path = root / DEFAULT_SIGNING_KEY_PATH
    if key_path.exists():
        return key_path, False
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(secrets.token_hex(32))
    try:
        _os.chmod(key_path, 0o600)
    except OSError:  # pragma: no cover — best-effort on exotic filesystems
        pass
    return key_path, True


def default_config_yaml(project_name: str) -> str:
    """Render the default ``rigforge.yaml`` body used by ``rigforge init``."""
    cfg = RigForgeConfig(project=project_name)
    data: dict[str, Any] = {
        "schema_version": cfg.schema_version,
        "project": cfg.project,
        "phases": cfg.phases,
        "budgets": cfg.budgets.model_dump(),
        "mcp": cfg.mcp.model_dump(),
        "scheduler": cfg.scheduler.model_dump(),
        "signing": cfg.signing.model_dump(),
        "cockpit": cfg.cockpit.model_dump(),
    }
    header = "# RIGForge project config — see rigforge.config.RigForgeConfig\n"
    return header + yaml.safe_dump(data, sort_keys=False)

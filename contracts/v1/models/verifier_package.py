# contracts/v1/models/verifier_package.py
"""
VerifierPackage — the GEV triad: who generates, who verifies, who evaluates.

Every DoneContract must name a VerifierPackage before build starts.
The triad enforces separation of concerns:
  - Generator  : produces artifacts
  - Verifier   : checks artifacts exist + pass gates
  - Evaluator  : scores quality + approves/denies ship
"""

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class AgentRole(str, Enum):
    """Named agents that can fill GEV roles."""
    CODEX = "Codex CLI"
    PYCODE = "PyCode"
    CLAUDE_CODE = "Claude Code"
    OPENCODE = "OpenCode"
    HERMES = "Hermes"
    JAKE = "Jake"
    HUMAN = "Human"


class VerifierPackage(BaseModel):
    """
    Triad assignment for a DoneContract verification.

    Fields
    ------
    generator  : AgentRole — produces artifacts (code, docs, configs)
    verifier  : AgentRole — checks existence, structure, and gate compliance
    evaluator : AgentRole — scores quality, approves or denies ship

    Rules
    -----
    - generator != verifier  (no self-verification)
    - evaluator must be >= generator authority
    - at least one human in the chain for approval_required=True contracts
    """
    generator: AgentRole = Field(
        ...,
        description="Agent that produces artifacts for this contract",
    )
    verifier: AgentRole = Field(
        ...,
        description="Agent that verifies artifacts exist and pass gates",
    )
    evaluator: AgentRole = Field(
        ...,
        description="Agent that scores quality and approves/denies ship",
    )

    @field_validator("verifier")
    @classmethod
    def verifier_must_differ_from_generator(cls, v: AgentRole, info) -> AgentRole:
        """No self-verification: verifier cannot be the same agent as generator."""
        gen = info.data.get("generator")
        if gen is not None and v == gen:
            raise ValueError(
                f"verifier ({v.value}) must differ from generator ({gen.value}) — "
                "no self-verification allowed"
            )
        return v

    @field_validator("evaluator")
    @classmethod
    def evaluator_must_have_authority(cls, v: AgentRole, info) -> AgentRole:
        """Evaluator must not be lower authority than generator."""
        # Simple authority ranking: HUMAN > CLAUDE_CODE > CODEX > PYCODE > others
        authority = {
            AgentRole.HUMAN: 5,
            AgentRole.CLAUDE_CODE: 4,
            AgentRole.HERMES: 3,
            AgentRole.JAKE: 3,
            AgentRole.CODEX: 3,
            AgentRole.OPENCODE: 2,
            AgentRole.PYCODE: 1,
        }
        gen = info.data.get("generator")
        if gen is not None and authority.get(v, 0) < authority.get(gen, 0):
            raise ValueError(
                f"evaluator ({v.value}, authority={authority.get(v, 0)}) must have "
                f">= authority of generator ({gen.value}, authority={authority.get(gen, 0)})"
            )
        return v

    def has_human_in_chain(self) -> bool:
        """True if at least one role is filled by a human."""
        return AgentRole.HUMAN in (self.generator, self.verifier, self.evaluator)

    def to_yaml_dict(self) -> dict:
        return {
            "verifier": self.verifier.value,
            "generator": self.generator.value,
            "evaluator": self.evaluator.value,
        }
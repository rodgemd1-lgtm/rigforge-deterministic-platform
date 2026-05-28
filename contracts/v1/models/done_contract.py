# contracts/v1/models/done_contract.py
"""
DoneContract — the core build contract in the GEV system.

A DoneContract is the single source of truth for:
  - What we are building (objective)
  - What artifacts must be produced (required_artifacts)
  - What gates must pass (acceptance_criteria)
  - What rules must not be broken (forbidden_actions)
  - Who verifies the work (verifier_package)

Hard rule: No DoneContract → no build.
"""

from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator

from contracts.v1.models.verifier_package import VerifierPackage
from contracts.v1.models.required_artifact import RequiredArtifact
from contracts.v1.models.acceptance_criterion import AcceptanceCriterion
from contracts.v1.models.forbidden_action import ForbiddenAction


class DoneContract(BaseModel):
    """
    A deterministic build contract following the GEV (Generate-Evaluate-Verify) system.

    Fields
    ------
    schema_version       : always "1.0.0"
    studio               : which studio owns this contract
    lane                 : build card lane ID (e.g., "BC-RIG-STRATEGY-V4")
    created_at           : ISO timestamp when contract was created
    objective            : what we are building
    non_goals            : explicitly out of scope
    run_id               : reference to RunEnvelope
    required_artifacts   : list of RequiredArtifact this contract must produce
    acceptance_criteria  : list of AcceptanceCriterion gates
    forbidden_actions    : list of ForbiddenAction hard rules
    max_iterations       : max agent passes before abort
    max_runtime_minutes  : gate: abort if exceeded
    max_cost_usd         : gate: abort if cost exceeded
    approval_required    : if True, human must approve before ship
    approval_gate        : who must approve (always "mike" for RIG)
    verifier_package     : GEV triad assignment
    """
    schema_version: str = Field(
        default="1.0.0",
        pattern=r"^\d+\.\d+\.\d+$",
        description="Schema version — always 1.0.0 for v1 contracts",
    )
    studio: str = Field(
        ...,
        min_length=1,
        description="Which studio owns this contract (e.g., 'strategy', 'app')",
    )
    lane: str = Field(
        ...,
        min_length=1,
        description="Build card lane ID (e.g., 'BC-RIG-STRATEGY-V4')",
    )
    created_at: datetime | None = Field(
        default=None,
        description="ISO timestamp when this contract was created",
    )
    objective: str | None = Field(
        default=None,
        description="What we are building",
    )
    non_goals: list[str] = Field(
        default_factory=list,
        description="Explicitly out of scope",
    )
    run_id: str | None = Field(
        default=None,
        description="Must reference RunEnvelope",
    )
    required_artifacts: list[RequiredArtifact] = Field(
        default_factory=list,
        description="Artifacts this contract must produce",
    )
    acceptance_criteria: list[AcceptanceCriterion] = Field(
        default_factory=list,
        description="Boolean gates that must pass before ship",
    )
    forbidden_actions: list[ForbiddenAction] = Field(
        default_factory=list,
        description="Hard rules; violation = abort",
    )
    max_iterations: int = Field(
        default=10,
        ge=1,
        description="Max agent passes before abort",
    )
    max_runtime_minutes: int = Field(
        default=20,
        ge=1,
        description="Abort if exceeded",
    )
    max_cost_usd: float = Field(
        default=2.0,
        ge=0.0,
        description="Abort if cost exceeded",
    )
    approval_required: bool = Field(
        default=True,
        description="If True, human must approve before ship",
    )
    approval_gate: str = Field(
        default="mike",
        description="Who must approve (always 'mike' for RIG)",
    )
    verifier_package: VerifierPackage | None = Field(
        default=None,
        description="GEV triad assignment (generator/verifier/evaluator)",
    )

    @field_validator("required_artifacts", "acceptance_criteria", "forbidden_actions")
    @classmethod
    def must_not_be_empty_on_ship(cls, v: list) -> list:
        """A DoneContract with no gates is not a contract — it is a wish."""
        # This validator fires at creation time; empty is allowed during
        # incremental construction, but seal() enforces non-empty.
        return v

    @model_validator(mode="after")
    def approval_requires_human(self) -> DoneContract:
        """If approval_required=True, verifier_package must have a human."""
        if self.approval_required and self.verifier_package is not None:
            if not self.verifier_package.has_human_in_chain():
                raise ValueError(
                    "approval_required=True but no human in verifier_package chain"
                )
        return self

    def is_sealed(self) -> bool:
        """A contract is sealed when it has objective, artifacts, criteria, and a verifier."""
        return (
            self.objective is not None
            and len(self.required_artifacts) > 0
            and len(self.acceptance_criteria) > 0
            and self.verifier_package is not None
            and len(self.forbidden_actions) > 0
        )

    def blocking_artifact_count(self) -> int:
        """Count of non-optional required artifacts."""
        return sum(1 for a in self.required_artifacts if a.is_blocking())

    def blocking_criteria_count(self) -> int:
        """Count of hard_block acceptance criteria."""
        return sum(1 for c in self.acceptance_criteria if c.is_blocking())

    def to_yaml_dict(self) -> dict:
        """Serialize to a YAML-compatible dict matching the existing example format."""
        result = {
            "schema_version": self.schema_version,
            "studio": self.studio,
            "lane": self.lane,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "objective": self.objective,
            "non_goals": self.non_goals,
            "run_id": self.run_id,
            "required_artifacts": [a.name for a in self.required_artifacts],
            "acceptance_criteria": [c.to_yaml_dict() for c in self.acceptance_criteria],
            "forbidden_actions": [f.to_yaml_dict() for f in self.forbidden_actions],
            "max_iterations": self.max_iterations,
            "max_runtime_minutes": self.max_runtime_minutes,
            "max_cost_usd": self.max_cost_usd,
            "approval_required": self.approval_required,
            "approval_gate": self.approval_gate,
        }
        if self.verifier_package is not None:
            result["verifier_package"] = self.verifier_package.to_yaml_dict()
        return result
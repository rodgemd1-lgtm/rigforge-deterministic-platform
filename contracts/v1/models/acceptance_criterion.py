# contracts/v1/models/acceptance_criterion.py
"""
AcceptanceCriterion — a boolean gate that must pass before ship.

Each criterion is a single, testable assertion:
  - expression : human-readable assertion (e.g., "file_count >= 12")
  - category   : structural | functional | security | quality | compliance
  - severity   : hard_block | soft_block | advisory
  - test_id    : optional link to a specific test case
"""

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field


class CriterionCategory(str, Enum):
    STRUCTURAL = "structural"      # File count, naming, layout
    FUNCTIONAL = "functional"      # Behavior, logic correctness
    SECURITY = "security"          # Security gates, no secrets
    QUALITY = "quality"           # Code quality, lint, types
    COMPLIANCE = "compliance"     # Policy, legal, regulation


class CriterionSeverity(str, Enum):
    HARD_BLOCK = "hard_block"     # Fails → no ship, no override
    SOFT_BLOCK = "soft_block"     # Fails → warns, can override with approval
    ADVISORY = "advisory"         # Fails → informational only


class AcceptanceCriterion(BaseModel):
    """
    A single boolean gate in a DoneContract.

    Fields
    ------
    expression : human-readable assertion (e.g., "file_count >= 12")
    category   : what domain this gate covers
    severity   : hard_block | soft_block | advisory
    test_id    : optional link to a concrete test case ID
    """
    expression: str = Field(
        ...,
        min_length=1,
        description="Human-readable assertion that must evaluate to True",
    )
    category: CriterionCategory = Field(
        default=CriterionCategory.FUNCTIONAL,
        description="Domain this gate covers",
    )
    severity: CriterionSeverity = Field(
        default=CriterionSeverity.HARD_BLOCK,
        description="Consequence if this criterion fails",
    )
    test_id: str | None = Field(
        default=None,
        description="Optional link to a concrete test case ID",
    )

    def is_blocking(self) -> bool:
        """Hard blocks prevent ship; soft blocks require approval override."""
        return self.severity == CriterionSeverity.HARD_BLOCK

    def to_yaml_dict(self) -> dict:
        result = {
            "expression": self.expression,
            "category": self.category.value,
            "severity": self.severity.value,
        }
        if self.test_id is not None:
            result["test_id"] = self.test_id
        return result
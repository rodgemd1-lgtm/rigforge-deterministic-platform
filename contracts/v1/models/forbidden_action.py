# contracts/v1/models/forbidden_action.py
"""
ForbiddenAction — a hard rule that, if violated, aborts the build.

These are non-negotiable constraints derived from RIG BC-HARDEN-001
and the broader RIG doctrine. Violation = immediate abort + escalation.
"""

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field


class ActionDomain(str, Enum):
    SECURITY = "security"         # No secrets, no exposures
    SCOPE = "scope"               # No writes outside target_dir
    DEPLOY = "deploy"             # No unauthorized deployments
    NETWORK = "network"           # No external API calls
    DATA = "data"                 # No data exfiltration
    PROCESS = "process"           # No subprocess abuse
    MEMORY = "memory"            # No memory update without approval
    APPROVAL = "approval"         # No ship without required approval


class ForbiddenAction(BaseModel):
    """
    A single hard rule in a DoneContract.

    Fields
    ------
    rule        : natural-language description of the forbidden action
    domain      : what domain this rule belongs to
    consequence : what happens if violated (always abort for forbidden actions)
    rationale   : why this rule exists (links to doctrine/policy)
    """
    rule: str = Field(
        ...,
        min_length=1,
        description="Natural-language description of the forbidden action",
    )
    domain: ActionDomain = Field(
        ...,
        description="Domain this rule belongs to",
    )
    consequence: str = Field(
        default="abort",
        description="What happens if violated (always abort for forbidden actions)",
    )
    rationale: str = Field(
        default="",
        description="Why this rule exists (links to doctrine or policy)",
    )

    def matches(self, action_description: str) -> bool:
        """Heuristic check: does an action description match this rule?
        Uses keyword overlap — not a substitute for human review."""
        rule_words = set(self.rule.lower().split())
        action_words = set(action_description.lower().split())
        overlap = rule_words & action_words
        # Require at least 2 significant words overlap
        return len(overlap) >= 2

    def to_yaml_dict(self) -> dict:
        result = {"rule": self.rule, "domain": self.domain.value}
        if self.consequence != "abort":
            result["consequence"] = self.consequence
        if self.rationale:
            result["rationale"] = self.rationale
        return result
# contracts/v1/models/required_artifact.py
"""
RequiredArtifact — an artifact that a DoneContract must produce.

Every artifact has:
  - name       : machine-readable slug (e.g., "proofpacket", "decision_contract")
  - artifact_type  : code | doc | config | data | test | proof
  - gate       : when this artifact must exist and pass checks
  - optional   : if True, missing artifact warns but does not block
"""

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field


class ArtifactType(str, Enum):
    CODE = "code"
    DOC = "doc"
    CONFIG = "config"
    DATA = "data"
    TEST = "test"
    PROOF = "proof"


class Gate(str, Enum):
    """When the artifact must be verified."""
    PRE_BUILD = "pre_build"      # Must exist before build starts
    MID_BUILD = "mid_build"      # Must exist at a mid-point checkpoint
    POST_BUILD = "post_build"    # Must exist after build completes
    PRE_SHIP = "pre_ship"        # Must exist before ship/delivery
    ALWAYS = "always"            # Must always be present


class RequiredArtifact(BaseModel):
    """
    A single artifact requirement in a DoneContract.

    Fields
    ------
    name          : machine-readable slug (snake_case)
    artifact_type : code | doc | config | data | test | proof
    gate          : when this artifact must be verified
    optional      : if True, missing triggers warning not block
    description   : human-readable explanation
    """
    name: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Machine-readable slug (snake_case)",
    )
    artifact_type: ArtifactType = Field(
        ...,
        description="Category of artifact",
    )
    gate: Gate = Field(
        default=Gate.POST_BUILD,
        description="When this artifact must be verified",
    )
    optional: bool = Field(
        default=False,
        description="If True, missing artifact warns but does not block",
    )
    description: str = Field(
        default="",
        description="Human-readable explanation of this artifact",
    )

    def is_blocking(self) -> bool:
        """Non-optional artifacts block ship if missing."""
        return not self.optional

    def to_yaml_dict(self) -> dict:
        result = {
            "name": self.name,
            "artifact_type": self.artifact_type.value,
            "gate": self.gate.value,
        }
        if self.optional:
            result["optional"] = "true"
        if self.description:
            result["description"] = self.description
        return result
# contracts/v1 — GEV (Generate-Evaluate-Verify) models
# RIG DoneContract system: deterministic phase-gated build contracts
"""
Five core models:
  1. DoneContract        — top-level build contract
  2. VerifierPackage     — generator/verifier/evaluator triad
  3. RequiredArtifact    — artifact specification with type and gate
  4. AcceptanceCriterion — boolean gate that must pass before ship
  5. ForbiddenAction     — hard rule; violation = abort

Usage:
  from contracts.v1 import DoneContract, VerifierPackage, RequiredArtifact, AcceptanceCriterion, ForbiddenAction
"""

from contracts.v1.models.done_contract import DoneContract
from contracts.v1.models.verifier_package import VerifierPackage
from contracts.v1.models.required_artifact import RequiredArtifact
from contracts.v1.models.acceptance_criterion import AcceptanceCriterion
from contracts.v1.models.forbidden_action import ForbiddenAction

__all__ = [
    "DoneContract",
    "VerifierPackage",
    "RequiredArtifact",
    "AcceptanceCriterion",
    "ForbiddenAction",
]

__version__ = "1.0.0"
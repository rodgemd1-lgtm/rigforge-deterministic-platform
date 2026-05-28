"""
Tests for contracts/v1 GEV models.
Validates all five models: DoneContract, VerifierPackage, RequiredArtifact,
AcceptanceCriterion, ForbiddenAction.
"""

import pytest
from datetime import datetime

from contracts.v1.models.verifier_package import VerifierPackage, AgentRole
from contracts.v1.models.required_artifact import RequiredArtifact, ArtifactType, Gate
from contracts.v1.models.acceptance_criterion import AcceptanceCriterion, CriterionCategory, CriterionSeverity
from contracts.v1.models.forbidden_action import ForbiddenAction, ActionDomain
from contracts.v1.models.done_contract import DoneContract


# ─── VerifierPackage ───────────────────────────────────────────────────

class TestVerifierPackage:
    def test_valid_triad(self):
        vp = VerifierPackage(generator=AgentRole.PYCODE, verifier=AgentRole.CODEX, evaluator=AgentRole.CLAUDE_CODE)
        assert vp.generator == AgentRole.PYCODE
        assert vp.verifier == AgentRole.CODEX
        assert vp.evaluator == AgentRole.CLAUDE_CODE

    def test_self_verification_blocked(self):
        with pytest.raises(ValueError, match="must differ from generator"):
            VerifierPackage(generator=AgentRole.CODEX, verifier=AgentRole.CODEX, evaluator=AgentRole.CLAUDE_CODE)

    def test_evaluator_authority(self):
        with pytest.raises(ValueError, match="authority"):
            VerifierPackage(generator=AgentRole.CLAUDE_CODE, verifier=AgentRole.CODEX, evaluator=AgentRole.PYCODE)

    def test_human_in_chain(self):
        vp = VerifierPackage(generator=AgentRole.PYCODE, verifier=AgentRole.CODEX, evaluator=AgentRole.HUMAN)
        assert vp.has_human_in_chain() is True

    def test_no_human_in_chain(self):
        vp = VerifierPackage(generator=AgentRole.PYCODE, verifier=AgentRole.CODEX, evaluator=AgentRole.CLAUDE_CODE)
        assert vp.has_human_in_chain() is False

    def test_to_yaml_dict(self):
        vp = VerifierPackage(generator=AgentRole.PYCODE, verifier=AgentRole.CODEX, evaluator=AgentRole.HUMAN)
        d = vp.to_yaml_dict()
        assert d == {"generator": "PyCode", "verifier": "Codex CLI", "evaluator": "Human"}


# ─── RequiredArtifact ──────────────────────────────────────────────────

class TestRequiredArtifact:
    def test_basic_artifact(self):
        a = RequiredArtifact(name="proofpacket", artifact_type=ArtifactType.PROOF)
        assert a.name == "proofpacket"
        assert a.gate == Gate.POST_BUILD
        assert a.optional is False
        assert a.is_blocking() is True

    def test_optional_artifact_not_blocking(self):
        a = RequiredArtifact(name="deploy_docs", artifact_type=ArtifactType.DOC, optional=True)
        assert a.is_blocking() is False

    def test_invalid_name(self):
        with pytest.raises(Exception):
            RequiredArtifact(name="Bad Name!", artifact_type=ArtifactType.CODE)

    def test_gate_values(self):
        for gate in Gate:
            a = RequiredArtifact(name=f"artifact_{gate.value}", artifact_type=ArtifactType.CODE, gate=gate)
            assert a.gate == gate

    def test_to_yaml_dict(self):
        a = RequiredArtifact(name="spec_parsed", artifact_type=ArtifactType.CODE, gate=Gate.PRE_BUILD, description="Parsed spec")
        d = a.to_yaml_dict()
        assert d["name"] == "spec_parsed"
        assert d["artifact_type"] == "code"
        assert d["gate"] == "pre_build"
        assert d["description"] == "Parsed spec"


# ─── AcceptanceCriterion ───────────────────────────────────────────────

class TestAcceptanceCriterion:
    def test_basic_criterion(self):
        c = AcceptanceCriterion(expression="file_count >= 12")
        assert c.category == CriterionCategory.FUNCTIONAL
        assert c.severity == CriterionSeverity.HARD_BLOCK
        assert c.is_blocking() is True

    def test_advisory_not_blocking(self):
        c = AcceptanceCriterion(expression="has changelog", severity=CriterionSeverity.ADVISORY)
        assert c.is_blocking() is False

    def test_soft_block(self):
        c = AcceptanceCriterion(expression="test coverage > 80%", severity=CriterionSeverity.SOFT_BLOCK)
        assert c.is_blocking() is False

    def test_with_test_id(self):
        c = AcceptanceCriterion(expression="smoke.py passes", test_id="TC-001")
        assert c.test_id == "TC-001"

    def test_to_yaml_dict(self):
        c = AcceptanceCriterion(
            expression="spec.theme == requested_theme",
            category=CriterionCategory.STRUCTURAL,
            severity=CriterionSeverity.HARD_BLOCK,
        )
        d = c.to_yaml_dict()
        assert d["expression"] == "spec.theme == requested_theme"
        assert d["category"] == "structural"
        assert d["severity"] == "hard_block"


# ─── ForbiddenAction ───────────────────────────────────────────────────

class TestForbiddenAction:
    def test_basic_action(self):
        f = ForbiddenAction(rule="No file writes outside target_dir", domain=ActionDomain.SCOPE)
        assert f.domain == ActionDomain.SCOPE
        assert f.consequence == "abort"

    def test_matches_heuristic(self):
        f = ForbiddenAction(rule="No file writes outside target_dir", domain=ActionDomain.SCOPE)
        assert f.matches("file writes outside directory") is True
        assert f.matches("totally unrelated thing") is False

    def test_security_domain(self):
        f = ForbiddenAction(rule="No hardcoded secrets or credentials", domain=ActionDomain.SECURITY, rationale="BC-HARDEN-001")
        assert f.domain == ActionDomain.SECURITY
        assert f.rationale == "BC-HARDEN-001"

    def test_to_yaml_dict(self):
        f = ForbiddenAction(rule="No publish or deploy actions", domain=ActionDomain.DEPLOY)
        d = f.to_yaml_dict()
        assert d["rule"] == "No publish or deploy actions"
        assert d["domain"] == "deploy"


# ─── DoneContract ──────────────────────────────────────────────────────

class TestDoneContract:
    def _make_contract(self) -> DoneContract:
        return DoneContract(
            studio="strategy",
            lane="BC-RIG-STRATEGY-V4",
            objective="Build RIG strategy deliverable",
            created_at=datetime(2026, 1, 1, 12, 0, 0),
            required_artifacts=[
                RequiredArtifact(name="decision_contract", artifact_type=ArtifactType.DOC),
                RequiredArtifact(name="evidence_pack", artifact_type=ArtifactType.PROOF),
                RequiredArtifact(name="proofpacket", artifact_type=ArtifactType.PROOF),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(expression="no_source_no_number", category=CriterionCategory.COMPLIANCE),
                AcceptanceCriterion(expression="rig_l_score_computed", category=CriterionCategory.FUNCTIONAL),
            ],
            forbidden_actions=[
                ForbiddenAction(rule="No strategy without RIG-L score", domain=ActionDomain.APPROVAL),
                ForbiddenAction(rule="No model hallucination of sources", domain=ActionDomain.SECURITY),
            ],
            verifier_package=VerifierPackage(
                generator=AgentRole.PYCODE,
                verifier=AgentRole.CODEX,
                evaluator=AgentRole.HUMAN,
            ),
        )

    def test_contract_creation(self):
        dc = self._make_contract()
        assert dc.studio == "strategy"
        assert dc.schema_version == "1.0.0"
        assert dc.is_sealed() is True

    def test_unsealed_contract(self):
        dc = DoneContract(studio="app", lane="D3-RIG-APP-V1")
        assert dc.is_sealed() is False

    def test_approval_requires_human(self):
        with pytest.raises(ValueError, match="no human in verifier_package chain"):
            DoneContract(
                studio="app",
                lane="D3-RIG-APP-V1",
                approval_required=True,
                verifier_package=VerifierPackage(
                    generator=AgentRole.PYCODE,
                    verifier=AgentRole.CODEX,
                    evaluator=AgentRole.CLAUDE_CODE,
                ),
            )

    def test_blocking_artifact_count(self):
        dc = self._make_contract()
        assert dc.blocking_artifact_count() == 3

    def test_blocking_criteria_count(self):
        dc = self._make_contract()
        assert dc.blocking_criteria_count() == 2

    def test_to_yaml_dict(self):
        dc = self._make_contract()
        d = dc.to_yaml_dict()
        assert d["studio"] == "strategy"
        assert d["lane"] == "BC-RIG-STRATEGY-V4"
        assert "required_artifacts" in d
        assert "acceptance_criteria" in d
        assert "forbidden_actions" in d
        assert "verifier_package" in d
        assert d["verifier_package"]["generator"] == "PyCode"

    def test_max_iterations_validation(self):
        with pytest.raises(Exception):
            DoneContract(studio="app", lane="X", max_iterations=0)

    def test_max_cost_validation(self):
        with pytest.raises(Exception):
            DoneContract(studio="app", lane="X", max_cost_usd=-1.0)


# ─── Package-level import ──────────────────────────────────────────────

class TestPackageImport:
    def test_import_all(self):
        from contracts.v1 import DoneContract, VerifierPackage, RequiredArtifact, AcceptanceCriterion, ForbiddenAction
        assert DoneContract is not None
        assert VerifierPackage is not None
        assert RequiredArtifact is not None
        assert AcceptanceCriterion is not None
        assert ForbiddenAction is not None

    def test_version(self):
        import contracts.v1
        assert contracts.v1.__version__ == "1.0.0"
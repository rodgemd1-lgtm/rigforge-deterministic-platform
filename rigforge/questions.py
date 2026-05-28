"""
20 senior-agentic-engineering questions, encoded as data.

These power ``rigforge questions`` and serve as review prompts that ship
with every contract / proof packet. They are also surfaced in
``rigforge review`` so that operators are reminded of the determinism
checks the platform is trying to enforce.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class EngineeringQuestion:
    number: int
    category: str
    question: str


QUESTIONS: tuple[EngineeringQuestion, ...] = (
    EngineeringQuestion(1, "determinism", "What is the deterministic source of truth for each run?"),
    EngineeringQuestion(2, "traceability", "Can every generated artifact be traced back to a contract, phase, command, and verifier?"),
    EngineeringQuestion(3, "verification", "What prevents an agent from self-verifying its own work?"),
    EngineeringQuestion(4, "evidence", "What evidence proves a phase was actually completed?"),
    EngineeringQuestion(5, "immutability", "Are proof packets immutable enough to support audit/replay?"),
    EngineeringQuestion(6, "budget", "What happens if an agent exceeds time, token, cost, or iteration budgets?"),
    EngineeringQuestion(7, "recovery", "How are failed runs captured and resumed?"),
    EngineeringQuestion(8, "escalation", "What is the escalation path from automated verifier to human approval?"),
    EngineeringQuestion(9, "gating", "Which artifacts are blocking versus advisory?"),
    EngineeringQuestion(10, "quality", "How do we prove that generated code passed objective quality gates?"),
    EngineeringQuestion(11, "ordering", "How is phase order enforced?"),
    EngineeringQuestion(12, "hermeticity", "How do we prevent hidden dependencies on local machine state?"),
    EngineeringQuestion(13, "provenance", "How are tools, prompts, models, and versions recorded?"),
    EngineeringQuestion(14, "safety", "What is the policy for forbidden actions and hard aborts?"),
    EngineeringQuestion(15, "separation", "How does the harness distinguish planning, generation, verification, and evaluation?"),
    EngineeringQuestion(16, "evolution", "How are contracts versioned and migrated?"),
    EngineeringQuestion(17, "authz", "How do MCP-exposed tools authenticate or restrict dangerous operations?"),
    EngineeringQuestion(18, "environment", "How does the system behave in CI versus local interactive mode?"),
    EngineeringQuestion(19, "minimum-set", "What is the minimum artifact set required before sealing?"),
    EngineeringQuestion(20, "operator-ux", "How do we make the CLI useful to both humans and agentic automation?"),
)


def as_dicts() -> list[dict]:
    return [asdict(q) for q in QUESTIONS]

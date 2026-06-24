"""
Troika: Coder / Verifier / Tester triad.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TroikaResult:
    role: str
    passed: bool
    stdout: str = ""
    stderr: str = ""


@dataclass
class TroikaReport:
    results: list[TroikaResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results)

    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)


class TroikaMember(ABC):
    @property
    @abstractmethod
    def role(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def execute(self, task: str, context: dict) -> TroikaResult:
        raise NotImplementedError


class Coder(TroikaMember):
    @property
    def role(self) -> str:
        return "coder"

    def execute(self, task: str, context: dict) -> TroikaResult:
        return TroikaResult(role=self.role, passed=True,
                            stdout=f"[coder] queued: {task}")


class Verifier(TroikaMember):
    @property
    def role(self) -> str:
        return "verifier"

    def execute(self, task: str, context: dict) -> TroikaResult:
        return TroikaResult(role=self.role, passed=True,
                            stdout=f"[verifier] queued: {task}")


class Tester(TroikaMember):
    @property
    def role(self) -> str:
        return "tester"

    def execute(self, task: str, context: dict) -> TroikaResult:
        return TroikaResult(role=self.role, passed=True,
                            stdout=f"[tester] queued: {task}")


class Troika:
    def __init__(self) -> None:
        self.coder = Coder()
        self.verifier = Verifier()
        self.tester = Tester()

    def run(self, task: str, context: Optional[dict] = None) -> TroikaReport:
        ctx = context or {}
        r1 = self.coder.execute(task, ctx)
        r2 = self.verifier.execute(task, ctx)
        r3 = self.tester.execute(task, ctx)
        return TroikaReport(results=[r1, r2, r3])

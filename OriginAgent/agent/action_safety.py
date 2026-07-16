"""Composable safety gates for action authorization decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

VALID_TRIGGERS = {"user_initiated", "scheduled", "system", "subagent", "automation"}
VALID_RISKS = {"low", "medium", "high"}


@dataclass
class ActionRequest:
    action: str
    scope: str
    trigger: str
    risk: str
    requested_by: str | None = None
    requires_presence_empty: bool = False
    uses_facts: list[str] = field(default_factory=list)


@dataclass
class ActionDecision:
    decision: str
    reason: str
    supporting_facts: list[str] = field(default_factory=list)
    pending_facts: list[str] = field(default_factory=list)
    presence_status: str = "unknown"


@runtime_checkable
class SafetyGate(Protocol):
    def evaluate(self, request: ActionRequest) -> ActionDecision:
        ...


class CompositeSafetyGate:
    def __init__(self, gates: list[SafetyGate] | None = None):
        self.gates = list(gates or [])

    def evaluate(self, request: ActionRequest) -> ActionDecision:
        final = ActionDecision(decision="allow", reason="safety checks passed")
        for gate in self.gates:
            decision = gate.evaluate(request)
            if decision.decision != "allow":
                return decision
            final = decision
        return final


class DefaultSafetyGate:
    def evaluate(self, request: ActionRequest) -> ActionDecision:
        if request.trigger not in VALID_TRIGGERS:
            return ActionDecision(decision="deny", reason="invalid trigger")
        if request.risk not in VALID_RISKS:
            return ActionDecision(decision="deny", reason="invalid risk")
        return ActionDecision(decision="allow", reason="safety checks passed")

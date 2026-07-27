"""Execution Planner models — frozen, immutable execution plan.

Nothing here places an order, generates a broker payload, chooses a
strike or expiry, or connects to any broker. `ExecutionPlan` describes
a broker-independent, conceptual workflow — steps a future Execution
Engine can implement however it likes, for whatever broker it targets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ExecutionStep:
    step_type: str
    description: str


@dataclass(frozen=True)
class ExecutionPlan:
    plan_id: str
    status: str
    execution_intent: str
    strategy_id: Optional[str]
    capital_intent: str
    required_controls: Tuple[str, ...]
    execution_constraints: Tuple[str, ...]
    execution_steps: Tuple[ExecutionStep, ...]
    confidence: str
    planning_trace: str
    capital_decision_id: Optional[str]
    strategy_decision_id: Optional[str]
    timestamp: str
    version: str

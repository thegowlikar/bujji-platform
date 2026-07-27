"""Evaluation Engine — Configuration — BUJJI Options OS, Integration
Series 3, Sprint 1.

A finite, statically-typed configuration object -- no dynamic loading,
no environment-variable magic. Carries no credential, no broker
endpoint, no order-routing detail of any kind. There is no separate
enable flag here: evaluation runs strictly inside the same
`intelligence_adapter.enabled` block the Adapter (Sprint 1) and
Observation Monitor (Sprint 2) already use.
"""
from __future__ import annotations

from dataclasses import dataclass

EVALUATION_CONFIG_VERSION = "1.0.0"


@dataclass(frozen=True)
class EvaluationConfig:
    ruleset_version: str = "1.0.0"


DEFAULT_EVALUATION_CONFIG = EvaluationConfig()

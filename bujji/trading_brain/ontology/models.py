"""Trading Ontology models — frozen, immutable vocabulary instances.

No method on any dataclass here mutates anything, computes a score, or
makes a decision. These are pure data containers: one value from each
finite vocabulary in taxonomy.py, plus provenance for auditability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class OntologyProvenance:
    """Records where a TradingOntologySnapshot's values came from."""

    source: str
    ruleset_version: str
    reasoning_summary: str = ""


@dataclass(frozen=True)
class TradingOntologySnapshot:
    """One instance of the shared Trading Brain vocabulary.

    This is the composite object every future Trading Brain module
    (Evidence Interpreter, Market State Builder, Strategy Selector,
    Risk Brain, Position Manager, ...) constructs, reads, and passes
    along. It carries exactly one value from each of the six named
    vocabularies plus one confidence value — never more, never fewer.

    Construction is the only place values are checked against their
    own finite taxonomy (see ontology/runner.py::build_snapshot). This
    dataclass itself performs no validation; it is deliberately a pure
    data container so the ontology package has no logic branch a
    future module would need to reason about beyond "call the
    validated constructor."
    """

    snapshot_id: str
    timestamp: str
    market_state: str
    opportunity_state: str
    risk_state: str
    execution_intent: str
    strategy_intent: str
    capital_intent: str
    confidence: str
    provenance: OntologyProvenance
    ontology_version: str
    context_id: Optional[str] = None

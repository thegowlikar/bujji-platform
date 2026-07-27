"""Trading Ontology runner — BUJJI Options OS v3, Engineering Series 31.

There is deliberately no engine.py in this package. This sprint builds
vocabulary, not decision logic, so the only function this package
needs is a pure, strict CONSTRUCTOR/VALIDATOR: given one candidate
value per vocabulary, either produce a well-formed
TradingOntologySnapshot or reject the input outright.

Rejection is intentional and loud (ValueError), not silent coercion to
UNKNOWN. UNKNOWN is a legitimate vocabulary member for "the evidence
was insufficient to classify" -- it is not a dumping ground for typos
or ad-hoc strings that were never part of the ontology. Enforcing that
distinction here, at the one place every future module must pass
through to obtain a snapshot, is what keeps the vocabulary finite: no
module may introduce a new state string by simply passing one in.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Optional

from .models import OntologyProvenance, TradingOntologySnapshot
from .taxonomy import ONTOLOGY_PACKAGE_VERSION, VOCABULARIES

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _validate(field_name: str, value: str) -> None:
    allowed, _version = VOCABULARIES[field_name]
    if value not in allowed:
        raise ValueError(
            f"'{value}' is not a member of the {field_name} vocabulary. "
            f"Allowed values: {allowed}. The ontology is closed -- add a "
            f"new value to taxonomy.py explicitly rather than passing an "
            f"ad-hoc string."
        )


def build_snapshot(
    market_state: str,
    opportunity_state: str,
    risk_state: str,
    execution_intent: str,
    strategy_intent: str,
    capital_intent: str,
    confidence: str,
    source: str = "paper",
    reasoning_summary: str = "",
    context_id: Optional[str] = None,
    clock: Clock = _real_clock,
) -> TradingOntologySnapshot:
    """Validate seven vocabulary values and package them into a snapshot.

    `clock` defaults to the real wall clock and is injectable for
    deterministic testing -- this snapshot's `timestamp` (and hence
    its `snapshot_id`) is genuinely wall-clock-derived by design, the
    same pattern used throughout MIC v2's lifecycle/observability
    engines.
    """
    _validate("market_state", market_state)
    _validate("opportunity_state", opportunity_state)
    _validate("risk_state", risk_state)
    _validate("execution_intent", execution_intent)
    _validate("strategy_intent", strategy_intent)
    _validate("capital_intent", capital_intent)
    _validate("confidence", confidence)

    timestamp = clock().isoformat()

    seed = "|".join(
        [
            timestamp,
            market_state,
            opportunity_state,
            risk_state,
            execution_intent,
            strategy_intent,
            capital_intent,
            confidence,
            source,
        ]
    )
    snapshot_id = "ONTO-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    provenance = OntologyProvenance(
        source=source,
        ruleset_version=ONTOLOGY_PACKAGE_VERSION,
        reasoning_summary=reasoning_summary,
    )

    return TradingOntologySnapshot(
        snapshot_id=snapshot_id,
        timestamp=timestamp,
        market_state=market_state,
        opportunity_state=opportunity_state,
        risk_state=risk_state,
        execution_intent=execution_intent,
        strategy_intent=strategy_intent,
        capital_intent=capital_intent,
        confidence=confidence,
        provenance=provenance,
        ontology_version=ONTOLOGY_PACKAGE_VERSION,
        context_id=context_id,
    )

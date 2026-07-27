"""Evidence Interpreter engine — BUJJI Options OS v3, Engineering Series
32, Sprint 1.

This is the ONLY function in the Trading Brain permitted to translate
MIC v2 intelligence into Trading Ontology vocabulary. It performs
deterministic table lookups only -- never reasoning, never scoring,
never voting, never fusion of multiple sources into one ontology
field. Exactly one MIC v2 layer drives exactly one ontology field
(see taxonomy.py::MAPPINGS).

Inputs are plain classification strings -- e.g. "BULLISH",
"TRENDING_UP", "APPROVED_WITH_WARNINGS" -- exactly the kind of value
BUJJI's existing mic_adapter readers already expose (see
bujji/intelligence/mic_adapter/opinion_reader.py's
translate_classification pattern). This module never imports MIC v2's
own package, and never imports Publication or Consumer models: it has
no way to reach into MIC v2 even if it wanted to, by construction.

If a source layer's value is absent (None), the corresponding ontology
field becomes UNKNOWN, with provenance explaining the absence. If a
source layer's value is present but not a recognized member of its own
domain, this raises ValueError immediately -- never silently coerced,
mirroring the ontology package's own closed-vocabulary discipline.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Optional

from ..ontology import runner as ontology_runner
from ..ontology.models import TradingOntologySnapshot
from . import taxonomy
from .models import EvidenceInterpretation, TranslationProvenance

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _translate_field(ontology_field: str, raw_value: Optional[str]) -> TranslationProvenance:
    source_layer, mapping, mapping_version = taxonomy.MAPPINGS[ontology_field]

    if raw_value is None:
        return TranslationProvenance(
            ontology_field=ontology_field,
            ontology_value="UNKNOWN",
            source_layer=source_layer,
            source_value=None,
            mapping_version=mapping_version,
            reason=f"No {source_layer} supplied.",
        )

    if raw_value not in mapping:
        raise ValueError(
            f"'{raw_value}' is not a recognized {source_layer} classification. "
            f"Allowed values: {tuple(mapping.keys())}. The Evidence Interpreter "
            f"never guesses a mapping for an unrecognized source value."
        )

    ontology_value = mapping[raw_value]
    return TranslationProvenance(
        ontology_field=ontology_field,
        ontology_value=ontology_value,
        source_layer=source_layer,
        source_value=raw_value,
        mapping_version=mapping_version,
        reason=f"Derived from {source_layer}={raw_value}.",
    )


def interpret(
    market_context: Optional[str] = None,
    market_opinion: Optional[str] = None,
    context_stability: Optional[str] = None,
    calibration: Optional[str] = None,
    governance: Optional[str] = None,
    lifecycle: Optional[str] = None,
    contract: Optional[str] = None,
    source: str = "paper",
    reasoning_summary: str = "",
    context_id: Optional[str] = None,
    clock: Clock = _real_clock,
) -> EvidenceInterpretation:
    """Translate MIC v2 classification strings into an EvidenceInterpretation.

    Every argument here is a raw MIC v2 classification string (or None
    if that layer's publication was not supplied this cycle) -- never a
    MIC v2 object, never a Publication or Consumer record.
    """
    raw_inputs = {
        "market_state": market_context,
        "strategy_intent": market_opinion,
        "confidence": context_stability,
        "opportunity_state": calibration,
        "risk_state": governance,
        "execution_intent": lifecycle,
        "capital_intent": contract,
    }

    provenance = tuple(
        _translate_field(field, raw_inputs[field]) for field in taxonomy.MAPPINGS
    )
    by_field = {p.ontology_field: p for p in provenance}

    timestamp = clock().isoformat()
    fixed_clock: Clock = lambda: datetime.fromisoformat(timestamp)

    snapshot: TradingOntologySnapshot = ontology_runner.build_snapshot(
        market_state=by_field["market_state"].ontology_value,
        opportunity_state=by_field["opportunity_state"].ontology_value,
        risk_state=by_field["risk_state"].ontology_value,
        execution_intent=by_field["execution_intent"].ontology_value,
        strategy_intent=by_field["strategy_intent"].ontology_value,
        capital_intent=by_field["capital_intent"].ontology_value,
        confidence=by_field["confidence"].ontology_value,
        source=source,
        reasoning_summary=reasoning_summary,
        context_id=context_id,
        clock=fixed_clock,
    )

    source_versions = {
        p.source_layer: (p.mapping_version if p.source_value is not None else None)
        for p in provenance
    }

    seed = "|".join(
        [snapshot.snapshot_id, timestamp]
        + [f"{p.source_layer}={p.source_value}" for p in provenance]
    )
    interpretation_id = "EI-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return EvidenceInterpretation(
        interpretation_id=interpretation_id,
        ontology_snapshot=snapshot,
        translation_provenance=provenance,
        source_versions=source_versions,
        timestamp=timestamp,
        interpreter_version=taxonomy.INTERPRETER_VERSION,
    )

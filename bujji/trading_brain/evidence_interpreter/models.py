"""Evidence Interpreter models — frozen, immutable translation records.

Nothing here decides anything. `EvidenceInterpretation` is the
permanent, explainable interface between MIC v2 intelligence and the
Trading Brain: one TradingOntologySnapshot plus, for every one of its
seven fields, a record of exactly which MIC v2 layer and value it was
derived from (or why it is UNKNOWN).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from ..ontology.models import TradingOntologySnapshot


@dataclass(frozen=True)
class TranslationProvenance:
    """Explains one ontology field's derivation.

    `source_value` is None when the source layer was not supplied at
    all -- in which case `ontology_value` must be "UNKNOWN" and
    `reason` explains the absence. It is never None when a value truly
    mapped to UNKNOWN through the table itself (e.g. Market Opinion's
    own MIXED -> UNKNOWN); that distinction -- "no evidence" versus
    "evidence says inconclusive" -- is preserved rather than collapsed.
    """

    ontology_field: str
    ontology_value: str
    source_layer: str
    source_value: Optional[str]
    mapping_version: str
    reason: str


@dataclass(frozen=True)
class EvidenceInterpretation:
    """The permanent, explainable gateway output.

    Downstream Trading Brain modules (Strategy Selector, Risk Brain,
    Position Manager, Learning Engine, Portfolio Brain, Strategy
    Evolution) must consume only this object -- never MIC v2's
    Publication or Consumer models directly, and never raw evidence.
    """

    interpretation_id: str
    ontology_snapshot: TradingOntologySnapshot
    translation_provenance: Tuple[TranslationProvenance, ...]
    source_versions: Dict[str, Optional[str]]
    timestamp: str
    interpreter_version: str

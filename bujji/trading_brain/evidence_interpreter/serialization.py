"""JSON round-trip for EvidenceInterpretation / TranslationProvenance."""
from __future__ import annotations

from typing import Any, Dict, List

from ..ontology.serialization import snapshot_from_dict, snapshot_to_dict
from .models import EvidenceInterpretation, TranslationProvenance


def provenance_to_dict(p: TranslationProvenance) -> Dict[str, Any]:
    return {
        "ontology_field": p.ontology_field,
        "ontology_value": p.ontology_value,
        "source_layer": p.source_layer,
        "source_value": p.source_value,
        "mapping_version": p.mapping_version,
        "reason": p.reason,
    }


def provenance_from_dict(d: Dict[str, Any]) -> TranslationProvenance:
    return TranslationProvenance(
        ontology_field=d["ontology_field"],
        ontology_value=d["ontology_value"],
        source_layer=d["source_layer"],
        source_value=d.get("source_value"),
        mapping_version=d["mapping_version"],
        reason=d["reason"],
    )


def interpretation_to_dict(interp: EvidenceInterpretation) -> Dict[str, Any]:
    return {
        "interpretation_id": interp.interpretation_id,
        "ontology_snapshot": snapshot_to_dict(interp.ontology_snapshot),
        "translation_provenance": [provenance_to_dict(p) for p in interp.translation_provenance],
        "source_versions": dict(interp.source_versions),
        "timestamp": interp.timestamp,
        "interpreter_version": interp.interpreter_version,
    }


def interpretation_from_dict(d: Dict[str, Any]) -> EvidenceInterpretation:
    provenance: List[TranslationProvenance] = [
        provenance_from_dict(p) for p in d["translation_provenance"]
    ]
    return EvidenceInterpretation(
        interpretation_id=d["interpretation_id"],
        ontology_snapshot=snapshot_from_dict(d["ontology_snapshot"]),
        translation_provenance=tuple(provenance),
        source_versions=dict(d["source_versions"]),
        timestamp=d["timestamp"],
        interpreter_version=d["interpreter_version"],
    )

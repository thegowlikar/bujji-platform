"""PipelineInput Compatibility Validator — BUJJI Options OS v3,
Engineering Series 62.

Checks whether a replayed `PublishedState` (`publication_replay.py`)
provides every field Series 32's `PipelineInput` requires, and whether
each present value is a member of that field's own recognized
taxonomy. This module never translates, fabricates, or defaults a
missing value -- it imports Series 32's own already-frozen taxonomy
mappings directly (`bujji.trading_brain.evidence_interpreter.taxonomy`)
and only checks membership against them. It creates no new translation
table of its own.

A field's value being `None` (MIC v2 published nothing for that layer)
is an incompatibility. A field's value being the literal string
`"UNKNOWN"` is NOT an incompatibility -- `UNKNOWN` is itself a
recognized member of every one of these taxonomies (Series 32's own
design: an absent source classification maps to `UNKNOWN`, never to a
missing field). Conflating "UNKNOWN" with "missing" would misreport
genuinely honest MIC v2 output as broken.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from ..trading_brain.evidence_interpreter import taxonomy as ei_taxonomy
from .publication_replay import PublishedState

REQUIRED_FIELDS: Tuple[str, ...] = (
    "market_context",
    "market_opinion",
    "context_stability",
    "calibration",
    "governance",
    "lifecycle",
    "contract",
)

# field name -> Series 32's own MAPPING dict (imported verbatim, never
# redefined) whose KEYS are exactly the recognized raw source values
# for that field.
_FIELD_TO_MAPPING = {
    "market_context": ei_taxonomy.MARKET_STATE_MAPPING,
    "market_opinion": ei_taxonomy.STRATEGY_INTENT_MAPPING,
    "context_stability": ei_taxonomy.CONFIDENCE_MAPPING,
    "calibration": ei_taxonomy.OPPORTUNITY_STATE_MAPPING,
    "governance": ei_taxonomy.RISK_STATE_MAPPING,
    "lifecycle": ei_taxonomy.EXECUTION_INTENT_MAPPING,
    "contract": ei_taxonomy.CAPITAL_INTENT_MAPPING,
}


@dataclass(frozen=True)
class CompatibilityResult:
    compatible: bool
    missing_fields: Tuple[str, ...]
    unrecognized_fields: Dict[str, str]
    present_fields: Tuple[str, ...]


def validate_compatibility(state: PublishedState) -> CompatibilityResult:
    """Check `state` against Series 32's own taxonomy. Never mutates
    `state`. `missing_fields` lists fields whose value is `None`;
    `unrecognized_fields` lists fields whose value is a non-`None`
    string that Series 32's own taxonomy does not recognize (would
    itself indicate a genuine MIC v2/Series 32 taxonomy drift -- a
    defect to disclose, not silently accept).
    """
    missing = []
    unrecognized: Dict[str, str] = {}
    present = []

    for field in REQUIRED_FIELDS:
        value = getattr(state, field)
        if value is None:
            missing.append(field)
            continue
        present.append(field)
        recognized_values = _FIELD_TO_MAPPING[field]
        if value not in recognized_values:
            unrecognized[field] = value

    compatible = not missing and not unrecognized
    return CompatibilityResult(
        compatible=compatible,
        missing_fields=tuple(missing),
        unrecognized_fields=unrecognized,
        present_fields=tuple(present),
    )


def to_pipeline_input_kwargs(state: PublishedState) -> Dict[str, Optional[str]]:
    """Build the exact kwargs dict `bujji.production_runtime.runtime.PipelineInput`
    accepts -- one key per `REQUIRED_FIELDS` entry, value taken
    verbatim from `state`. Never invents a value; a field that is
    `None` in `state` is passed through as `None`, exactly Series 32's
    own "absent source -> UNKNOWN ontology value" contract already
    handles.
    """
    return {field: getattr(state, field) for field in REQUIRED_FIELDS}

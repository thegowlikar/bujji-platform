"""Market Intelligence Observatory — Classification Explanation.

BUJJI Options OS v3, Engineering Series 66.

Read-only. Given already-recorded evidence (Series 61's real
`Evidence` replay output) and an already-recorded classification
(Series 62's `PublishedState`, or a plain session dict from a saved
qualification report), explain what produced it -- never recompute,
never guess, never modify. Structural facts below (which MIC v2 module
owns which classification, and that module's own documented upstream
composition chain) are taken verbatim from Series 62's own
documentation of `mic_v2.contract.runner.run_replay_with_contract`'s
composition (`context` -> `context_stability` -> `calibration` ->
`governance` -> `lifecycle` -> `contract`, with `opinion` a sibling
branch) -- never invented here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

# Series 32's seven PipelineInput fields -> the MIC v2 module that
# owns deriving them, and that module's own real upstream dependency
# chain, as documented by Series 62 (never guessed; matches the
# discovered composition in `mic_v2.contract.runner`/`opinion.runner`).
FIELD_PROVENANCE: Dict[str, Dict[str, Any]] = {
    "market_context": {
        "module": "mic_v2.context.engine.derive_context",
        "upstream_chain": ("evidence", "qualification", "trace", "memory", "quality"),
    },
    "market_opinion": {
        "module": "mic_v2.opinion.engine.derive_opinion",
        "upstream_chain": ("evidence", "qualification", "trace", "memory"),
    },
    "context_stability": {
        "module": "mic_v2.context_stability.engine.derive_stability",
        "upstream_chain": ("market_context (window of recent MarketContext objects)", "memory", "quality"),
    },
    "calibration": {
        "module": "mic_v2.calibration.engine.derive_calibration",
        "upstream_chain": ("market_opinion", "market_context", "context_stability", "quality"),
    },
    "governance": {
        "module": "mic_v2.governance.engine.derive_governance",
        "upstream_chain": ("calibration", "context_stability", "quality", "consistency", "certification"),
    },
    "lifecycle": {
        "module": "mic_v2.lifecycle.engine.derive_lifecycle",
        "upstream_chain": ("publication", "governance", "calibration", "context_stability"),
    },
    "contract": {
        "module": "mic_v2.contract.engine.derive_contract",
        "upstream_chain": ("publication", "governance", "compatibility", "lifecycle", "calibration"),
    },
}

# Evidence module names (Series 61's 8 analyzer modules) most directly
# relevant to each field -- used only to filter, never to reweight or
# reinterpret, the already-recorded Evidence list.
_RELEVANT_EVIDENCE_MODULES: Dict[str, Tuple[str, ...]] = {
    "market_context": ("trend", "volatility", "liquidity", "market_regime"),
    "market_opinion": ("trend",),
    "context_stability": (),  # derived from a window of prior MarketContext, not fresh Evidence directly
    "calibration": (),  # derived from opinion/context/stability distributions, not fresh Evidence directly
    "governance": (),  # derived from calibration/stability/quality/consistency/certification
    "lifecycle": (),
    "contract": (),
}


@dataclass(frozen=True)
class ClassificationExplanation:
    """One field's explanation. `evidence_used` is the exact, unmodified
    subset of a real Series 61 `Evidence` replay result relevant to
    this field (may be empty when the field's true upstream basis is
    itself a downstream MIC artifact, not raw Evidence -- reported
    honestly, never backfilled with an irrelevant evidence item).
    `reasoning_summary` is `None` unless the caller supplied MIC v2's
    own recorded `reasoning_summary` for this classification -- never
    fabricated when absent.
    """

    field_name: str
    value: Optional[str]
    originating_module: str
    upstream_chain: Tuple[str, ...]
    evidence_used: Tuple[Dict[str, Any], ...]
    confidence: Optional[float]
    reasoning_summary: Optional[str]
    publication_id: Optional[str]


def explain_field(
    field_name: str,
    value: Optional[str],
    publication_id: Optional[str] = None,
    evidence: Sequence[Dict[str, Any]] = (),
    reasoning_summary: Optional[str] = None,
) -> ClassificationExplanation:
    """Explain one classification field. `evidence` is the full,
    already-recorded Series 61 Evidence list for the session (or
    corpus prefix) that produced `value` -- this function only filters
    it to the modules relevant to `field_name`; it never recomputes or
    reweights anything.
    """
    provenance = FIELD_PROVENANCE.get(field_name)
    if provenance is None:
        raise ValueError(f"Unrecognized PipelineInput field: {field_name!r}")

    relevant_modules = _RELEVANT_EVIDENCE_MODULES.get(field_name, ())
    relevant_evidence = tuple(e for e in evidence if e.get("module") in relevant_modules)

    confidence = None
    if relevant_evidence:
        confidences = [e.get("confidence") for e in relevant_evidence if e.get("confidence") is not None]
        if confidences:
            confidence = sum(confidences) / len(confidences)

    return ClassificationExplanation(
        field_name=field_name,
        value=value,
        originating_module=provenance["module"],
        upstream_chain=tuple(provenance["upstream_chain"]),
        evidence_used=relevant_evidence,
        confidence=confidence,
        reasoning_summary=reasoning_summary,
        publication_id=publication_id,
    )


def explain_session(
    session: Dict[str, Any],
    evidence: Sequence[Dict[str, Any]] = (),
    publication_ids: Optional[Dict[str, Optional[str]]] = None,
    reasoning_summaries: Optional[Dict[str, Optional[str]]] = None,
) -> Tuple[ClassificationExplanation, ...]:
    """Explain every recognized PipelineInput field present in `session`
    (a plain dict, e.g. one entry from a saved qualification report's
    `sessions` list, or a `PublishedState`-derived dict). Fields absent
    from `session` are skipped -- never fabricated as UNKNOWN with a
    fake explanation.
    """
    publication_ids = publication_ids or {}
    reasoning_summaries = reasoning_summaries or {}
    explanations = []
    for field_name in FIELD_PROVENANCE:
        if field_name not in session:
            continue
        explanations.append(
            explain_field(
                field_name,
                session.get(field_name),
                publication_id=publication_ids.get(f"{field_name}_id"),
                evidence=evidence,
                reasoning_summary=reasoning_summaries.get(field_name),
            )
        )
    return tuple(explanations)

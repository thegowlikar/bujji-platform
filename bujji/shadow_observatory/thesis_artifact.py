"""D-5: make the reasoning behind a trade (or a NO_TRADE) auditable.

WHY. Until now a session persisted its CONCLUSION and not its reasoning.
`decisions.jsonl` recorded `trend_regime: UNKNOWN -> selected_strategy: null`
and nothing else, so "why did Bujji not trade on 2026-08-19?" had no answer
in the artifacts -- only in a three-field log line that scrolls away. Two
real things were computed and thrown on the floor every cycle:

  1. `IntelligenceCycleRecorder.record_cycle()` RETURNS a complete, honest
     record of that cycle's understanding-layer conclusions. The runner
     called it for its side effect and dropped the return value.

  2. `MarketThesisAssessment` carries `preferred_strategy_families`,
     `rejected_strategy_families` and `insufficient_evidence_families` --
     the full 13-family verdict. The governor's own selection is a
     deterministic two-input (trend, volatility) lookup that never sees it.

This module turns both into one persisted record per derivation. It is a
PURE function over already-built objects: it computes no market opinion,
re-derives nothing, and never invents a value. A field the upstream object
does not carry is recorded as null and named in `absent_fields` -- absent
beats invented, and a reader can tell "we did not know" from "it was zero".

Persisting the family verdict alongside the lookup's choice is also what
makes the selector-authority question measurable: the divergence between
the two selectors accumulates in the artifacts, run after run, so the
operator gate on swapping them can be decided from observation instead of
argument.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Thesis fields worth persisting. Every one is a pass-through of a real
# upstream reading (see bujji/market_thesis/models.py) -- no derived values.
_THESIS_SCALARS = (
    "assessment_id", "timestamp", "market_regime", "directional_bias",
    "volatility_environment", "premium_environment", "expected_move_environment",
    "positioning_environment", "liquidity_environment", "confidence",
)
_THESIS_SEQUENCES = (
    "preferred_strategy_families", "rejected_strategy_families",
    "insufficient_evidence_families", "reasons", "supporting_assessment_ids",
)


def _scalar(obj: Any, field: str, absent: List[str]) -> Optional[Any]:
    value = getattr(obj, field, None)
    if value is None:
        absent.append(field)
    return value


def _sequence(obj: Any, field: str, absent: List[str]) -> Optional[List[Any]]:
    value = getattr(obj, field, None)
    if value is None:
        absent.append(field)
        return None
    return list(value)


def build_thesis_artifact(
    *,
    thesis: Any,
    cycle_record: Optional[Dict[str, Any]],
    trend_regime: Optional[str] = None,
    volatility_regime: Optional[str] = None,
    stability: Optional[Dict[str, Any]] = None,
    cycle: Optional[int] = None,
    recorded_at: Optional[str] = None,
    level_context: Optional[Dict[str, Any]] = None,
    depth_observation: Optional[Dict[str, Any]] = None,
    evidence_integrity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One auditable record of a single regime derivation.

    `thesis` is a MarketThesisAssessment. `cycle_record` is the dict
    `IntelligenceCycleRecorder.record_cycle()` returned for the SAME cycle
    (None when a caller has none -- recorded as null, never fabricated).
    `trend_regime`/`volatility_regime` are what the provider actually handed
    the governor, so the evidence -> thesis -> regime -> selection chain is
    linkable end to end.
    """
    absent: List[str] = []
    artifact: Dict[str, Any] = {
        "record_type": "MARKET_THESIS_DERIVATION",
        "recorded_at": recorded_at,
        "cycle": cycle,
        # What the governor was actually told. The lookup selector sees only
        # these two strings, which is exactly the point of recording them
        # next to the far richer thesis they were reduced from.
        "regime_handed_to_selector": {
            "trend_regime": trend_regime,
            "volatility_regime": volatility_regime,
        },
        "thesis": {f: _scalar(thesis, f, absent) for f in _THESIS_SCALARS},
        "stability": stability,
        # L-5: where price sat relative to reachable structure at this
        # derivation. OBSERVATION ONLY -- nothing in the decision chain reads
        # it. Recorded now so that when the operator gate is considered, the
        # question "would levels have helped?" is answered from a real trail
        # rather than from argument. None means no map was available, which
        # is itself worth knowing on the day of a trade.
        "level_context": level_context,
        # ORDER-BOOK PRESSURE, RECORDED BUT NOT CONSUMED (2026-08-20).
        #
        # Depth is fetched every cycle and is deliberately NOT a direction
        # lens yet. reconcile_lenses ignores confidence when detecting
        # conflict, and takes the MINIMUM confidence across opinionated
        # lenses -- so an honest LOW confidence on a single order-book
        # snapshot would cap the whole direction read at LOW whenever it
        # spoke, and manufacture MIXED whenever the book leaned against real
        # structure. Promoting it is a judgement about the reconciliation
        # rule, which is the operator's.
        #
        # So the raw imbalance is recorded beside what direction ACTUALLY
        # concluded on the same cycle. After enough sessions the question
        # "does the book agree with structure, or fight it?" is answerable
        # from this trail instead of from argument -- and NO THRESHOLD is
        # baked in here, because choosing one before measuring is the thing
        # this record exists to avoid.
        "depth_observation": depth_observation,
        # EVIDENCE INTEGRITY, MEASURED (2026-08-21).
        #
        # On 2026-08-20 this session's decisions cited 346 distinct
        # supporting observation ids and not one of them resolved anywhere
        # on disk -- while the campaign report printed "Explanation
        # completeness: 100%", because that metric counts POPULATED FIELDS,
        # not whether what they point at exists.
        #
        # This block is the honest counterpart: how many ids this cycle
        # cited, how many actually resolve in the session's own evidence
        # store, and the ids that do not. `resolution_rate` is None -- never
        # 1.0 -- when nothing was cited, because a cycle that cited nothing
        # has not demonstrated a whole trail, and scoring it perfect is the
        # exact failure this replaces.
        "evidence_integrity": evidence_integrity,
        # The understanding layer's own honest record for this cycle,
        # persisted verbatim -- this module reshapes nothing.
        "cycle_record": cycle_record,
    }
    artifact["thesis"].update({f: _sequence(thesis, f, absent) for f in _THESIS_SEQUENCES})

    # The 13-family verdict, lifted out of the thesis so a reader does not
    # have to know where it lives. INSUFFICIENT_EVIDENCE stays its own
    # bucket: "we don't know" and "we know it doesn't fit" are different
    # facts and folding them together would lose the distinction upstream
    # deliberately preserved.
    preferred = artifact["thesis"]["preferred_strategy_families"]
    rejected = artifact["thesis"]["rejected_strategy_families"]
    unknown = artifact["thesis"]["insufficient_evidence_families"]
    artifact["family_verdict"] = {
        "preferred": preferred,
        "rejected": rejected,
        "insufficient_evidence": unknown,
        "families_assessed": (
            None if preferred is None and rejected is None and unknown is None
            else len(preferred or []) + len(rejected or []) + len(unknown or [])
        ),
    }
    artifact["absent_fields"] = absent
    artifact["cycle_record_present"] = cycle_record is not None
    return artifact

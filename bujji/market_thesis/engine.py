"""Market Thesis engine — BUJJI Options OS v3, Trading Brain
Intelligence Upgrade, Phase 3.

A THIN COMPOSITION LAYER, not a new Market Thesis Generator. The
architecture audit that preceded this package found a real one
already exists and is reused here UNMODIFIED, in full:

  1. `bujji.msi_trade_thesis.engine.derive_trade_thesis(psi, mssi, mdi,
     mppi, vsb, consensus, timestamp=...)` -- composes six real
     domains (Price Structure, Market Structure, Market Direction,
     Participant Positioning, Volatility Structure, Consensus) into a
     deterministic market thesis (`thesis_type`: TREND_CONTINUATION /
     TREND_REVERSAL / RANGE_PERSISTENCE / VOLATILITY_EXPANSION /
     VOLATILITY_COMPRESSION / BREAKOUT / FAILED_BREAKOUT /
     MEAN_REVERSION / EVENT_RISK / NO_TRADE), with real conviction,
     invalidation conditions, and supporting/conflicting domains.
     Called here exactly as its own real signature requires -- never
     forked, never re-implemented.

  2. `bujji.msi_strategy_selection_foundation.engine.assess_all_families
     (mdi, mssi, consensus, vsb, liquidity, timestamp=...)` -- a real,
     declarative (never scoring) per-family SUITABLE/UNSUITABLE/
     INSUFFICIENT_EVIDENCE gate. Called here exactly as its own real
     signature requires; this module only RESHAPES its output into
     `preferred_strategy_families`/`rejected_strategy_families`/
     `insufficient_evidence_families` -- it adds no new suitability
     logic of its own.

WHAT THIS MODULE ADDS (the genuine gap the audit found, and nothing
more): `premium_environment` (neither upstream engine exposes IV
richness as its own field), `positioning_environment` (msi_trade_thesis
consumes MPPI but folds it into supporting/conflicting only, never
exposes it), and `liquidity_environment` (msi_trade_thesis never
consumes liquidity at all; SSF has it only per-family, not as a
thesis-level read). Every other field is a direct pass-through --
never recomputed.

FAILS CLOSED, MATCHING derive_trade_thesis'S OWN REAL CONSTRAINT:
`derive_trade_thesis` requires psi/mssi/mdi/mppi as real, non-None
objects (only vsb/consensus are internally None-safe) -- confirmed by
reading its body, not assumed. Rather than crash or silently skip
(the existing `market_state/trade_thesis_bridge.py` returns None in
this case), this module returns a real, disclosed UNKNOWN-state
MarketThesisAssessment naming exactly which required evidence was
missing -- "Unknown is a valid answer" applies to the whole thesis,
not just individual fields.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional, Tuple

from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy
from bujji.msi_strategy_selection_foundation.engine import assess_all_families
from bujji.msi_trade_thesis.engine import derive_trade_thesis

from . import taxonomy
from .models import MarketThesisAssessment

PROVENANCE = "bujji.market_thesis.engine.assess"

_VSB_IV_TO_PREMIUM = {"IV_RICH": taxonomy.PREMIUM_RICH, "IV_CHEAP": taxonomy.PREMIUM_CHEAP, "IV_FAIR": taxonomy.PREMIUM_FAIR}
_VI_IV_TO_PREMIUM = {"RICH": taxonomy.PREMIUM_RICH, "CHEAP": taxonomy.PREMIUM_CHEAP, "FAIR": taxonomy.PREMIUM_FAIR}

_REQUIRED_EVIDENCE_NAMES = ("psi", "mssi", "mdi", "mppi")


def _derive_premium_environment(vsb, volatility_intelligence) -> Tuple[str, str]:
    if volatility_intelligence is not None and volatility_intelligence.iv_state in _VI_IV_TO_PREMIUM:
        return (
            _VI_IV_TO_PREMIUM[volatility_intelligence.iv_state],
            f"volatility_intelligence.iv_state={volatility_intelligence.iv_state} "
            f"(Phase 2's own corroborated read, preferred over raw VSB when available).",
        )
    if vsb is not None and vsb.iv_state in _VSB_IV_TO_PREMIUM:
        return (
            _VSB_IV_TO_PREMIUM[vsb.iv_state],
            f"VSB iv_state={vsb.iv_state} (no volatility_intelligence supplied).",
        )
    return taxonomy.PREMIUM_UNKNOWN, "Neither volatility_intelligence nor VSB provided a real iv_state read."


def _derive_expected_move_environment(vsb) -> Tuple[str, str]:
    if vsb is None:
        return "UNKNOWN", "No VolatilityStructureAssessment supplied."
    return vsb.expected_move_state, f"VSB expected_move_state={vsb.expected_move_state} (expected_move_pct={vsb.expected_move_pct})."


def _derive_positioning_environment(mppi) -> Tuple[str, str]:
    return (
        mppi.positioning_bias,
        f"MPPI positioning_bias={mppi.positioning_bias}, exposed directly (msi_trade_thesis folds this "
        f"into directional supporting/conflicting only, never as its own field).",
    )


def _derive_liquidity_environment(liquidity) -> Tuple[str, str]:
    if liquidity is None:
        return "UNKNOWN", "No LiquidityReading supplied."
    tightness = str(liquidity.tightness).rsplit(".", 1)[-1]
    return tightness, f"LiquidityReading.tightness={tightness} (SSF has this per-family only; exposed here as a thesis-level read)."


def _group_families(family_assessments) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    preferred = tuple(sorted(a.strategy_family for a in family_assessments if a.suitability == ssf_taxonomy.SUITABLE))
    rejected = tuple(sorted(a.strategy_family for a in family_assessments if a.suitability == ssf_taxonomy.UNSUITABLE))
    insufficient = tuple(sorted(a.strategy_family for a in family_assessments if a.suitability == ssf_taxonomy.INSUFFICIENT_EVIDENCE))
    return preferred, rejected, insufficient


def _assessment_id(fields: Tuple[str, ...], timestamp: str) -> str:
    seed = "###".join(fields) + f"###{timestamp}"
    return "MTA-" + hashlib.md5(seed.encode()).hexdigest()[:24]


def _unknown_assessment(missing: List[str], available_ids: Tuple[str, ...], *, timestamp: str, schema_version: str) -> MarketThesisAssessment:
    reasons = (
        f"UNKNOWN: derive_trade_thesis() requires real psi/mssi/mdi/mppi objects; "
        f"the following were not supplied: {', '.join(missing)}.",
    )
    assessment_id = _assessment_id(("UNKNOWN", ",".join(missing), schema_version), timestamp)
    return MarketThesisAssessment(
        assessment_id=assessment_id, timestamp=timestamp,
        market_regime="UNKNOWN", directional_bias="UNKNOWN", volatility_environment="UNKNOWN",
        premium_environment=taxonomy.PREMIUM_UNKNOWN, expected_move_environment="UNKNOWN",
        positioning_environment="UNKNOWN", liquidity_environment="UNKNOWN",
        preferred_strategy_families=(), rejected_strategy_families=(), insufficient_evidence_families=(),
        confidence="NONE", reasons=reasons, supporting_assessment_ids=available_ids,
        provenance=PROVENANCE, schema_version=schema_version,
    )


def assess(
    psi=None, mssi=None, mdi=None, mppi=None, vsb=None, consensus=None,
    liquidity=None, volatility_intelligence=None,
    *, timestamp: str, schema_version: str = taxonomy.MARKET_THESIS_VERSION,
) -> MarketThesisAssessment:
    """Pure function of its inputs. Calls derive_trade_thesis() and
    assess_all_families() exactly once each, unmodified. Never invents
    a strategy family, never ranks, never scores profitability -- the
    only judgment made HERE (as opposed to reused from those two real
    engines) is the small premium/positioning/liquidity translation
    documented in taxonomy.py."""
    provided = {"psi": psi, "mssi": mssi, "mdi": mdi, "mppi": mppi}
    missing = [name for name in _REQUIRED_EVIDENCE_NAMES if provided[name] is None]
    available_ids = tuple(sorted({
        obj.assessment_id for obj in (psi, mssi, mdi, mppi, vsb, consensus, volatility_intelligence) if obj is not None
    }))
    if missing:
        return _unknown_assessment(missing, available_ids, timestamp=timestamp, schema_version=schema_version)

    trade_thesis = derive_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus, timestamp=timestamp)

    family_assessments: Tuple = ()
    family_reason: str
    if consensus is not None:
        family_assessments = assess_all_families(mdi, mssi, consensus, vsb, liquidity, timestamp=timestamp)
        family_reason = None
    else:
        family_reason = "Strategy family suitability could not be assessed -- assess_all_families() requires a real ConsensusAssessment, none was supplied."

    preferred, rejected, insufficient = _group_families(family_assessments)

    premium_environment, premium_reason = _derive_premium_environment(vsb, volatility_intelligence)
    expected_move_environment, expected_move_reason = _derive_expected_move_environment(vsb)
    positioning_environment, positioning_reason = _derive_positioning_environment(mppi)
    liquidity_environment, liquidity_reason = _derive_liquidity_environment(liquidity)

    reasons: List[str] = list(trade_thesis.explanation.why_this_thesis)
    reasons.append(f"premium_environment={premium_environment}: {premium_reason}")
    reasons.append(f"expected_move_environment={expected_move_environment}: {expected_move_reason}")
    reasons.append(f"positioning_environment={positioning_environment}: {positioning_reason}")
    reasons.append(f"liquidity_environment={liquidity_environment}: {liquidity_reason}")
    if family_reason is not None:
        reasons.append(family_reason)
    else:
        reasons.append(
            f"{len(preferred)} strategy family(ies) preferred, {len(rejected)} rejected, "
            f"{len(insufficient)} insufficient evidence, out of {len(family_assessments)} assessed."
        )

    supporting_ids = set(available_ids)
    supporting_ids.add(trade_thesis.assessment_id)
    supporting_ids.update(a.assessment_id for a in family_assessments)
    supporting_assessment_ids = tuple(sorted(supporting_ids))

    fields_for_id = (
        trade_thesis.thesis_type, trade_thesis.directional_expectation, trade_thesis.volatility_expectation,
        premium_environment, expected_move_environment, positioning_environment, liquidity_environment,
        ",".join(preferred), ",".join(rejected), ",".join(insufficient), trade_thesis.conviction, schema_version,
    )
    assessment_id = _assessment_id(fields_for_id, timestamp)

    return MarketThesisAssessment(
        assessment_id=assessment_id, timestamp=timestamp,
        market_regime=trade_thesis.thesis_type, directional_bias=trade_thesis.directional_expectation,
        volatility_environment=trade_thesis.volatility_expectation, premium_environment=premium_environment,
        expected_move_environment=expected_move_environment, positioning_environment=positioning_environment,
        liquidity_environment=liquidity_environment,
        preferred_strategy_families=preferred, rejected_strategy_families=rejected,
        insufficient_evidence_families=insufficient,
        confidence=trade_thesis.conviction, reasons=tuple(reasons),
        supporting_assessment_ids=supporting_assessment_ids, provenance=PROVENANCE, schema_version=schema_version,
    )

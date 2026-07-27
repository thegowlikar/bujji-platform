"""Trade Thesis Engine — Series 92.

Deliverable 1 audit summary (full detail in docs/TRADE_THESIS_ENGINE.md):
consumes ONLY the real, already-computed outputs of Price Structure
(Series 78), Market Structure (Series 79), Market Direction (Series 85),
Participant Positioning (Series 86), Volatility Structure (Series 88),
and Consensus (Series 81) -- never re-derives any of their reasoning,
never imports Strategy Selection Foundation's own logic (this package
sits UPSTREAM of it; SSF remains completely unmodified, per the user's
explicit constraint).

Method: each contributing domain "votes" for at most one thesis type
from its own already-computed state (a real reapplication of the same
lens-reconciliation IDEA already used by
`bujji.msi_market_direction.engine.reconcile_lenses` and
`bujji.msi_strategy_selector`'s market-state derivation -- not
duplicated code, the same pattern re-applied to a new evidence set).
Market Structure and Volatility Structure vote first (breakout/failed-
breakout and volatility regime are the most information-dense, specific
reads); Price Structure votes last among the structural cascade. Market
Direction and Participant Positioning never vote for a thesis TYPE --
they only supply `directional_expectation` and participate in the
supporting/conflicting split for directional thesis types.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from . import config as _config
from . import taxonomy
from .models import Explanation, TradeThesisAssessment


# ---------------------------------------------------------------------------
# Per-domain votes -- each returns one taxonomy.ALL_THESIS_TYPES value or None.
# ---------------------------------------------------------------------------

def _market_structure_vote(mssi) -> Optional[str]:
    if mssi.breakout_state == "FAILED" or mssi.breakdown_state == "FAILED":
        return taxonomy.THESIS_FAILED_BREAKOUT
    if mssi.breakout_state == "CONFIRMED" or mssi.breakdown_state == "CONFIRMED":
        return taxonomy.THESIS_BREAKOUT
    if mssi.structural_balance == "RANGE_BOUND" and mssi.structure_location in ("NEAR_SUPPORT", "NEAR_RESISTANCE"):
        return taxonomy.THESIS_MEAN_REVERSION
    if mssi.structural_balance == "RANGE_BOUND":
        return taxonomy.THESIS_RANGE_PERSISTENCE
    return None


def _volatility_vote(vsb) -> Optional[str]:
    if vsb is None:
        return None
    if vsb.expansion_state == "CONFIRMED" or vsb.volatility_regime in ("TRANSITIONING", "HIGH_VOLATILITY"):
        return taxonomy.THESIS_VOLATILITY_EXPANSION
    if vsb.compression_state == "CONFIRMED" or vsb.volatility_regime == "COMPRESSED":
        return taxonomy.THESIS_VOLATILITY_COMPRESSION
    return None


def _price_structure_vote(psi) -> Optional[str]:
    if psi.compression_state == "CONFIRMED":
        return taxonomy.THESIS_VOLATILITY_COMPRESSION
    if psi.expansion_state == "CONFIRMED":
        return taxonomy.THESIS_VOLATILITY_EXPANSION
    if psi.structure_state == "TRENDING" and psi.trend_state == "WEAKENING_TREND":
        return taxonomy.THESIS_TREND_REVERSAL
    if psi.structure_state == "TRENDING" and psi.trend_state in ("EMERGING_TREND", "ESTABLISHED_TREND"):
        return taxonomy.THESIS_TREND_CONTINUATION
    if psi.structure_state == "CORRECTING":
        return taxonomy.THESIS_TREND_REVERSAL
    if psi.structure_state == "BALANCE":
        return taxonomy.THESIS_RANGE_PERSISTENCE
    return None


_VOL_STATE_MAP = {
    taxonomy.THESIS_VOLATILITY_EXPANSION: taxonomy.VOL_EXPECTATION_EXPANSION,
    taxonomy.THESIS_VOLATILITY_COMPRESSION: taxonomy.VOL_EXPECTATION_COMPRESSION,
}


def _volatility_expectation(vsb) -> str:
    if vsb is None:
        return taxonomy.VOL_EXPECTATION_UNKNOWN
    if vsb.expansion_state == "CONFIRMED" or vsb.volatility_regime in ("TRANSITIONING", "HIGH_VOLATILITY"):
        return taxonomy.VOL_EXPECTATION_EXPANSION
    if vsb.compression_state == "CONFIRMED" or vsb.volatility_regime == "COMPRESSED":
        return taxonomy.VOL_EXPECTATION_COMPRESSION
    if vsb.volatility_regime == "STABLE":
        return taxonomy.VOL_EXPECTATION_STABLE
    return taxonomy.VOL_EXPECTATION_UNKNOWN


_MARKET_EXPECTATION_TEXT = {
    taxonomy.THESIS_TREND_CONTINUATION: "the current trend is likely to continue",
    taxonomy.THESIS_TREND_REVERSAL: "the current trend shows signs of reversing",
    taxonomy.THESIS_RANGE_PERSISTENCE: "the market is likely to remain range-bound",
    taxonomy.THESIS_VOLATILITY_EXPANSION: "the market is likely to experience a volatility expansion",
    taxonomy.THESIS_VOLATILITY_COMPRESSION: "the market is likely to experience a volatility compression",
    taxonomy.THESIS_BREAKOUT: "a structural breakout is confirmed and likely to extend",
    taxonomy.THESIS_FAILED_BREAKOUT: "a recent breakout attempt has failed and is likely to reverse back into range",
    taxonomy.THESIS_MEAN_REVERSION: "price is near a range boundary and likely to revert toward the range",
    taxonomy.THESIS_EVENT_RISK: "multiple real domains genuinely disagree with each other -- treat this as elevated, undiagnosed event-like risk",
    taxonomy.THESIS_NO_TRADE: "there is not enough evidence to form any market thesis today",
}

_INVALIDATION_TEXT = {
    taxonomy.THESIS_TREND_CONTINUATION: "invalidated if Price Structure's trend weakens or Market Structure's balance turns range-bound",
    taxonomy.THESIS_TREND_REVERSAL: "invalidated if the prior trend re-establishes or Market Structure shows no rejection",
    taxonomy.THESIS_RANGE_PERSISTENCE: "invalidated if a confirmed breakout or breakdown occurs",
    taxonomy.THESIS_VOLATILITY_EXPANSION: "invalidated if realised volatility contracts and consensus weakens",
    taxonomy.THESIS_VOLATILITY_COMPRESSION: "invalidated if volatility structure confirms expansion",
    taxonomy.THESIS_BREAKOUT: "invalidated if the breakout/breakdown state turns FAILED on the next assessment",
    taxonomy.THESIS_FAILED_BREAKOUT: "invalidated if price re-attempts and confirms the breakout/breakdown",
    taxonomy.THESIS_MEAN_REVERSION: "invalidated if structural balance turns UNBOUNDED (a genuine breakout away from the range)",
    taxonomy.THESIS_EVENT_RISK: "invalidated once the conflicting domains converge on a single, coherent read",
    taxonomy.THESIS_NO_TRADE: "invalidated (i.e. a thesis becomes possible) once evidence sufficiency improves",
}


def _assessment_id(thesis_type: str, supporting: Tuple[str, ...], conflicting: Tuple[str, ...],
                    conviction: str, schema_version: str) -> str:
    content = "|".join([thesis_type, ",".join(supporting), ",".join(conflicting), conviction, schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def derive_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus, *, timestamp: str) -> TradeThesisAssessment:
    """Produces EXACTLY ONE thesis. Never ranks, never scores
    profitability, never touches Strategy Selection Foundation."""
    schema_version = taxonomy.MSI_TRADE_THESIS_VERSION

    # Deliverable 3 EVENT_RISK gate: genuine multi-domain contradiction,
    # checked BEFORE the normal vote cascade -- PSI's own structure_integrity
    # already flags this exact condition (real evidence, not invented).
    mssi_vote = _market_structure_vote(mssi)
    vsb_vote = _volatility_vote(vsb)
    psi_vote = _price_structure_vote(psi)
    votes = {
        taxonomy.DOMAIN_MARKET_STRUCTURE: mssi_vote,
        taxonomy.DOMAIN_VOLATILITY_STRUCTURE: vsb_vote,
        taxonomy.DOMAIN_PRICE_STRUCTURE: psi_vote,
    }
    distinct_non_none_votes = {v for v in votes.values() if v is not None}

    if psi.structure_integrity == "CONFLICTED" and len(distinct_non_none_votes) >= 2:
        thesis_type = taxonomy.THESIS_EVENT_RISK
    elif mssi_vote is not None:
        thesis_type = mssi_vote
    elif vsb_vote is not None:
        thesis_type = vsb_vote
    elif psi_vote is not None:
        thesis_type = psi_vote
    else:
        thesis_type = taxonomy.THESIS_NO_TRADE

    supporting = [dom for dom, vote in votes.items() if vote == thesis_type]
    conflicting = [dom for dom, vote in votes.items() if vote is not None and vote != thesis_type]

    # Market Direction / Participant Positioning: never vote for a thesis
    # TYPE, but for DIRECTIONAL thesis types they participate in
    # supporting/conflicting via real sign agreement -- never re-deriving
    # direction, only comparing already-computed leans.
    if thesis_type in taxonomy.DIRECTIONAL_THESIS_TYPES:
        bullish = ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH")
        bearish = ("STRONG_BEARISH", "BEARISH", "WEAK_BEARISH")
        mdi_sign = 1 if mdi.overall_direction in bullish else -1 if mdi.overall_direction in bearish else 0
        if mdi_sign != 0:
            supporting.append(taxonomy.DOMAIN_MARKET_DIRECTION)
        if mppi.positioning_bias == "BULLISH_POSITIONING" and mdi_sign >= 0:
            supporting.append(taxonomy.DOMAIN_PARTICIPANT_POSITIONING)
        elif mppi.positioning_bias == "BEARISH_POSITIONING" and mdi_sign <= 0:
            supporting.append(taxonomy.DOMAIN_PARTICIPANT_POSITIONING)
        elif mppi.positioning_bias in ("BULLISH_POSITIONING", "BEARISH_POSITIONING"):
            conflicting.append(taxonomy.DOMAIN_PARTICIPANT_POSITIONING)  # disagrees with MDI's sign.

    supporting_t = tuple(sorted(set(supporting)))
    conflicting_t = tuple(sorted(set(conflicting)))

    # Deliverable 4 conviction rubric (config.py, structural, never tuned).
    rank = 0
    if len(supporting_t) >= _config.MIN_SUPPORTING_DOMAINS_FOR_CONVICTION_POINT:
        rank += 1
    if not conflicting_t:
        rank += 1
    consensus_rank = {"NO_CONSENSUS": 0, "WEAK_CONSENSUS": 1, "MODERATE_CONSENSUS": 2,
                       "STRONG_CONSENSUS": 3, "UNANIMOUS_CONSENSUS": 4}.get(consensus.consensus_level, 0) if consensus else 0
    if consensus_rank >= 2:
        rank += 1
    conviction = _config.CONVICTION_RANK_TO_LEVEL[rank] if thesis_type != taxonomy.THESIS_NO_TRADE else taxonomy.CONVICTION_NONE

    expected_move = vsb.expected_move_pct if vsb else None
    volatility_expectation = _volatility_expectation(vsb)
    horizon = taxonomy.HORIZON_UNKNOWN if thesis_type == taxonomy.THESIS_NO_TRADE else taxonomy.HORIZON_NEXT_SESSION
    directional_expectation = mdi.overall_direction

    market_expectation = _MARKET_EXPECTATION_TEXT[thesis_type]
    invalidation = (_INVALIDATION_TEXT[thesis_type],)
    if thesis_type in taxonomy.DIRECTIONAL_THESIS_TYPES and directional_expectation in ("MIXED", "UNKNOWN", "NEUTRAL"):
        invalidation = invalidation + (
            "directional evidence is currently mixed/unknown -- only the structural/volatility view is expressed, not a directional one",
        )

    aid = _assessment_id(thesis_type, supporting_t, conflicting_t, conviction, schema_version)
    why = [f"{dom} voted {thesis_type}" for dom, vote in votes.items() if vote == thesis_type]
    if thesis_type == taxonomy.THESIS_EVENT_RISK:
        why = [f"Price Structure reported CONFLICTED structure_integrity while {len(distinct_non_none_votes)} "
               f"domains produced distinct, disagreeing votes"]
    if not why:
        why = ["no domain produced a thesis vote -- insufficient real evidence today"]

    explanation = Explanation(
        assessment_id=aid, why_this_thesis=tuple(why),
        supporting_evidence=tuple(f"{dom} supports {thesis_type}" for dom in supporting_t),
        conflicting_evidence=tuple(f"{dom} disagrees with {thesis_type} (voted {votes.get(dom)})" for dom in conflicting_t if votes.get(dom)) +
                             tuple(f"{dom} positioning disagrees with the directional lean" for dom in conflicting_t if dom == taxonomy.DOMAIN_PARTICIPANT_POSITIONING and votes.get(dom) is None),
        what_would_invalidate=invalidation, schema_version=schema_version,
    )

    return TradeThesisAssessment(
        assessment_id=aid, timestamp=timestamp, thesis_type=thesis_type, market_expectation=market_expectation,
        expected_move=expected_move, expected_time_horizon=horizon, volatility_expectation=volatility_expectation,
        directional_expectation=directional_expectation, conviction=conviction,
        invalidation_conditions=invalidation, supporting_domains=supporting_t, conflicting_domains=conflicting_t,
        explanation=explanation, provenance="bujji.msi_trade_thesis.engine.derive_trade_thesis",
        schema_version=schema_version,
    )

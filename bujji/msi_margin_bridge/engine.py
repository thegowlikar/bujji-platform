"""Margin Bridge & Capital Fidelity engine — Series 97.

ONE public function, `estimate_margin`, is the entire bridge interface,
used identically in both environments:

    PRODUCTION: pass a real `bujji.capital.models.MarginRequirement`
    (already fetched live elsewhere, e.g. via
    `CertifiedBrokerMarginProvider` -- this function never calls a
    broker itself) as `live_margin_requirement`.

    REPLAY: omit it (`None`, the default) -- a deterministic estimate
    is computed from Series 90's own real `TradeConstructionAssessment`
    (legs, premiums, risk profile). No live/network call is ever made
    by this module in either path.

Deliverable 1 reuse: this module does NOT duplicate
`bujji.capital.engine.compute_lot_sizing` -- it produces a PER-LOT
margin figure only (matching `MarginRequirement.margin_per_lot`'s own
existing convention exactly); multiplying by lot count remains
Portfolio Construction's job, unchanged.
"""
from __future__ import annotations

import hashlib
from typing import Dict, Optional, Tuple

from bujji.capital.models import MarginRequirement
from bujji.msi_trade_construction.models import TradeConstructionAssessment
from bujji.msi_trade_construction import taxonomy as tc_taxonomy

from . import config as _config
from . import taxonomy
from .models import Explanation, MarginEstimate


def _assessment_id(methodology: str, estimated_margin: Optional[float], confidence: str,
                    data_source: str, schema_version: str) -> str:
    content = "|".join([methodology, str(estimated_margin), confidence, data_source, schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _wing_width(trade: TradeConstructionAssessment) -> Optional[float]:
    """Real, structural fact of a defined-risk credit spread: the widest
    strike distance between a SELL leg and a BUY leg of the SAME option
    type -- this IS the maximum possible loss width for a European,
    cash-settled index option spread (NIFTY options are exactly this),
    not an approximation."""
    by_type: Dict[str, list] = {}
    for leg in trade.legs:
        by_type.setdefault(leg.option_type, []).append(leg)
    widths = []
    for legs in by_type.values():
        sells = [l for l in legs if l.side == "SELL"]
        buys = [l for l in legs if l.side == "BUY"]
        for s in sells:
            for b in buys:
                widths.append(abs(b.strike - s.strike))
    return max(widths) if widths else None


def _replay_estimate(trade: Optional[TradeConstructionAssessment], lot_size: int, spot: Optional[float]) -> Tuple[str, Optional[float], str, Tuple[str, ...], Tuple[str, ...]]:
    """Returns (methodology, estimated_margin, confidence, why_exists, assumptions)."""
    if trade is None or not trade.constructed:
        return (taxonomy.METHODOLOGY_UNKNOWN, None, taxonomy.CONFIDENCE_UNKNOWN,
                ("no constructed trade was provided -- there is nothing to estimate margin for",), ())

    if trade.risk_profile == tc_taxonomy.RISK_DEFINED:
        if trade.expected_credit_debit is not None and trade.expected_credit_debit < 0:
            debit_paid = abs(trade.expected_credit_debit) * lot_size
            return (
                taxonomy.METHODOLOGY_DEBIT_MAX_LOSS_EXACT, debit_paid, taxonomy.CONFIDENCE_EXACT,
                ("this is a net-debit, defined-risk construction -- maximum possible loss equals the "
                 "debit paid, a mathematical fact of the structure, not an approximation",),
                ("assumes European, cash-settled exercise (true for NIFTY index options) -- no early "
                 "assignment risk changes the max-loss figure",),
            )
        width = _wing_width(trade)
        if width is not None and trade.expected_credit_debit is not None:
            credit = max(trade.expected_credit_debit, 0.0) * lot_size
            max_loss = width * lot_size - credit
            return (
                taxonomy.METHODOLOGY_CREDIT_SPREAD_MAX_LOSS_EXACT, max(max_loss, 0.0), taxonomy.CONFIDENCE_EXACT,
                ("this is a defined-risk credit/wing-bearing construction -- maximum possible loss equals "
                 "the wing width minus the credit received, a mathematical fact of the structure",),
                ("assumes European, cash-settled exercise (true for NIFTY index options); "
                 "assumes both wing legs are held to the same expiry",),
            )
        return (
            taxonomy.METHODOLOGY_UNKNOWN, None, taxonomy.CONFIDENCE_UNKNOWN,
            ("construction is DEFINED_RISK but neither a net-debit figure nor a computable wing width "
             "was available -- refusing to guess a max-loss figure",), (),
        )

    if trade.risk_profile == tc_taxonomy.RISK_UNDEFINED:
        candidates = []
        if trade.expected_credit_debit is not None:
            premium_estimate = abs(trade.expected_credit_debit) * lot_size * _config.UNDEFINED_RISK_PREMIUM_MULTIPLE
            candidates.append((premium_estimate, taxonomy.METHODOLOGY_PREMIUM_MULTIPLE_APPROXIMATION))
        if spot is not None and spot > 0:
            notional_estimate = spot * lot_size * _config.UNDEFINED_RISK_NOTIONAL_PERCENTAGE
            candidates.append((notional_estimate, taxonomy.METHODOLOGY_NOTIONAL_PERCENTAGE_APPROXIMATION))
        if not candidates:
            return (taxonomy.METHODOLOGY_UNKNOWN, None, taxonomy.CONFIDENCE_UNKNOWN,
                    ("construction is UNDEFINED_RISK but neither real premium nor real spot was available "
                     "to approximate from",), ())
        estimate, methodology = max(candidates, key=lambda c: c[0])
        return (
            methodology, estimate, taxonomy.CONFIDENCE_APPROXIMATE,
            (f"this is an UNDEFINED_RISK construction -- no mathematical max-loss figure exists; the "
             f"higher of {_config.UNDEFINED_RISK_NOTIONAL_PERCENTAGE:.0%} of notional or "
             f"{_config.UNDEFINED_RISK_PREMIUM_MULTIPLE}x premium collected is used, a disclosed, "
             f"standard retail-margin rule of thumb -- never an exchange-certified SPAN figure",),
            (f"assumes a flat {_config.UNDEFINED_RISK_NOTIONAL_PERCENTAGE:.0%}-of-notional or "
             f"{_config.UNDEFINED_RISK_PREMIUM_MULTIPLE}x-premium heuristic approximates real SPAN margin "
             f"reasonably -- NOT verified against a real broker figure",),
        )

    return (taxonomy.METHODOLOGY_UNKNOWN, None, taxonomy.CONFIDENCE_UNKNOWN,
            ("construction's own risk_profile is UNKNOWN -- refusing to guess a methodology",), ())


def estimate_margin(
    trade: Optional[TradeConstructionAssessment] = None, lot_size: int = 1, spot: Optional[float] = None,
    *, live_margin_requirement: Optional[MarginRequirement] = None, timestamp: str,
) -> MarginEstimate:
    """The entire Margin Bridge interface. Exactly one of the two paths
    runs per call: PRODUCTION (live_margin_requirement provided) or
    REPLAY (trade provided, live_margin_requirement omitted)."""
    schema_version = taxonomy.MSI_MARGIN_BRIDGE_VERSION

    if live_margin_requirement is not None:
        if live_margin_requirement.margin_per_lot is None or live_margin_requirement.margin_per_lot <= 0:
            methodology, estimated_margin, confidence = taxonomy.METHODOLOGY_UNKNOWN, None, taxonomy.CONFIDENCE_UNKNOWN
            why_exists = ("a live broker margin call was made, but returned no usable figure",)
            why_avail = (f"source={live_margin_requirement.source} returned no positive margin_per_lot",)
            assumptions: Tuple[str, ...] = ()
        else:
            methodology = taxonomy.METHODOLOGY_LIVE_BROKER_SPAN
            estimated_margin = live_margin_requirement.margin_per_lot
            confidence = taxonomy.CONFIDENCE_EXACT if live_margin_requirement.verified else taxonomy.CONFIDENCE_APPROXIMATE
            why_exists = (f"a live broker margin figure was supplied (source={live_margin_requirement.source})",)
            why_avail = (
                ("exact margin IS available: a CERTIFIED broker margin-calculator response backs this figure"
                 if live_margin_requirement.verified else
                 "margin is available but UNCERTIFIED -- the broker response has not been human-confirmed "
                 "against a real account, so this is treated as approximate, not exact"),
            )
            assumptions = ()
        aid = _assessment_id(methodology, estimated_margin, confidence, taxonomy.DATA_SOURCE_LIVE_BROKER_SPAN, schema_version)
        explanation = Explanation(
            assessment_id=aid, why_this_estimate_exists=why_exists,
            why_exact_margin_is_or_isnt_available=why_avail, assumptions_required=assumptions,
            schema_version=schema_version,
        )
        return MarginEstimate(
            assessment_id=aid, timestamp=timestamp, methodology=methodology, estimated_margin=estimated_margin,
            confidence=confidence, data_source=taxonomy.DATA_SOURCE_LIVE_BROKER_SPAN, replay_safe=False,
            explanation=explanation, provenance="bujji.msi_margin_bridge.engine.estimate_margin",
            schema_version=schema_version,
        )

    methodology, estimated_margin, confidence, why_exists, assumptions = _replay_estimate(trade, lot_size, spot)
    data_source = taxonomy.DATA_SOURCE_REPLAY_TRADE_CONSTRUCTION if trade is not None and trade.constructed else taxonomy.DATA_SOURCE_REPLAY_INSUFFICIENT_DATA
    why_avail = (
        ("exact margin is NOT available in replay -- no live/certified broker call is made here by design; "
         "the figure above is the best deterministic estimate this methodology allows",)
        if confidence != taxonomy.CONFIDENCE_UNKNOWN else
        ("neither an exact nor an approximate figure could be computed from the real data available today",)
    )
    aid = _assessment_id(methodology, estimated_margin, confidence, data_source, schema_version)
    explanation = Explanation(
        assessment_id=aid, why_this_estimate_exists=why_exists,
        why_exact_margin_is_or_isnt_available=why_avail, assumptions_required=assumptions,
        schema_version=schema_version,
    )
    return MarginEstimate(
        assessment_id=aid, timestamp=timestamp, methodology=methodology, estimated_margin=estimated_margin,
        confidence=confidence, data_source=data_source, replay_safe=True,
        explanation=explanation, provenance="bujji.msi_margin_bridge.engine.estimate_margin",
        schema_version=schema_version,
    )

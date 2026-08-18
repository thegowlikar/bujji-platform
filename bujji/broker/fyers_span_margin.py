"""FYERS whole-book SPAN margin transport — the missing network leg of Gate C.

WHAT EXISTED, AND WHAT WAS MISSING: `whole_book_margin_provider.py` (Gate C)
carries the complete provider chain -- request builder, defensive parser,
Uncertified/Certified providers -- around an INJECTED `HttpCaller` that nothing
ever implemented. Meanwhile `FyersBroker.get_order_margin()` live-certified the
real endpoint on 2026-07-19 (docs/CAPITAL_MANAGEMENT_ENGINE.md, "span_margin
Live Certification"; docs/AUDIT_LOG.md Pass 8) but is hardcoded to a 2-leg
CE+PE straddle. This module is the bridge: the certified endpoint mechanics,
generalized to the whole-book leg array, shaped as the `HttpCaller` Gate C
already expects. It lives in `bujji/broker/` because `bujji/broker/fyers.py`
is byte-pinned against baseline b148e39 by the safety guards and must not
grow this code.

THE NORMALIZATION, AND WHY IT IS NOT A HACK: the 2026-07-19 certification
proved the real response nests its figures under "data"
(`{"s": "ok", "data": {"span", "expo", "total", "benefit"},
"individual_info": {...}}`) -- and explicitly records that reading top-level
"total"/"span" keys was a pre-certification BUG. `parse_span_margin_response`
still expects those keys at top level. Rather than edit that parser (a
protected, pure, separately-tested module), this caller flattens the certified
shape into the parser's documented contract and keeps the complete raw broker
response under "broker_response" so nothing is discarded.

WHAT THIS MODULE MUST NEVER BE MISTAKEN FOR: a trading permission. The margin
number it fetches flows into `capital_check.assess_capital()`, which remains
the sole ALLOW/VETO authority. An UNCERTIFIED provider is structurally
incapable of `margin_verified=True`, so wiring it VETOes every entry -- that
is designed behavior, not a defect. Flipping to the certified provider is a
HUMAN, config-level decision (`providers.margin: fyers_certified` in the
session YAML), exactly as `CertifiedWholeBookMarginProvider`'s own docstring
demands.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

# Reuses the ONE module-level pacer every FYERS call already goes through --
# never a second rate-limit implementation.
from bujji.broker.fyers import _wait_for_slot
from bujji.trading_brain.risk_governor.simulated_margin_provider import (
    LegMarginContribution,
    MarginExplanation,
    RISK_INVALID_STATE,
    _classify_book,
)
from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
    CertifiedWholeBookMarginProvider,
    MarginLegRequest,
    MarginSnapshot,
    UncertifiedWholeBookMarginProvider,
)

# The endpoint the 2026-07-19 certification hit, verbatim. The provider passes
# its own SPAN_MARGIN_ENDPOINT constant (same string, marked UNCONFIRMED
# there); this caller honors the passed URL so the provider stays in charge.
CERTIFIED_SPAN_MARGIN_ENDPOINT = "https://api.fyers.in/api/v2/span_margin"

_REQUEST_TIMEOUT_SECONDS = 30.0


class SpanMarginCallError(RuntimeError):
    """Raised for any transport/broker failure. The provider catches ALL
    exceptions from its caller and fails closed (QUERY_FAILED,
    margin_verified=False) -- this type just makes logs diagnosable."""


class FyersSpanMarginCaller:
    """`HttpCaller` implementation: (url, body) -> parser-shaped dict.

    `post` is injectable for tests; defaults to `requests.post`. Every call
    takes a slot on the shared FYERS pacer first -- margin quotes compete
    with chain capture for the same 10/s ceiling.
    """

    def __init__(self, app_id: str, access_token: str,
                 post: Optional[Callable[..., Any]] = None,
                 timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS) -> None:
        if not app_id or not access_token:
            raise ValueError("FyersSpanMarginCaller requires both app_id and access_token")
        self._app_id = app_id
        self._access_token = access_token
        self._post = post or requests.post
        self._timeout = timeout_seconds

    def __call__(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        _wait_for_slot()
        response = self._post(
            url,
            json=body,
            # Live-certified auth format: "{app_id}:{access_token}" -- NOT a
            # Bearer scheme (get_order_margin's certification evidence).
            headers={"Authorization": f"{self._app_id}:{self._access_token}"},
            timeout=self._timeout,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SpanMarginCallError(
                f"span_margin returned non-JSON (HTTP {response.status_code})") from exc
        if response.status_code != 200 or str(payload.get("s", "")).lower() != "ok":
            # Certified error shapes: -310 invalid symbol, -50 malformed
            # payload, -17 expired auth. Raise -> provider fails closed.
            raise SpanMarginCallError(
                f"span_margin rejected: HTTP {response.status_code} "
                f"code={payload.get('code')!r} message={payload.get('message')!r}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SpanMarginCallError(
                f"span_margin ok-response carried no 'data' object: {list(payload)!r}")
        # Flatten to parse_span_margin_response's documented contract
        # (top-level total/span/expo/benefit + individual_info), keeping the
        # untouched broker response alongside -- it lands in
        # WholeBookMarginQuoteResult.raw_response, so nothing is discarded.
        return {
            **data,
            "individual_info": payload.get("individual_info"),
            "broker_response": payload,
        }


def _leg_notional(leg: MarginLegRequest) -> float:
    price = leg.limit_price if isinstance(leg.limit_price, (int, float)) else 0.0
    return abs(leg.qty) * float(price)


def _explain(snapshot: MarginSnapshot, legs: List[MarginLegRequest],
             source_label: str) -> MarginExplanation:
    """Pure. Exposure figures are LEG-NOTIONAL ARITHMETIC -- the identical
    covered/naked rule `SimulatedMarginProvider` documents (min() of
    notionals) -- applied to the real legs. Only the margin NUMBER itself is
    broker-quoted; this function never invents a second margin figure."""
    if snapshot.required_margin is None:
        return MarginExplanation(
            total_required_margin=None, total_long_exposure=0.0,
            total_short_exposure=0.0, covered_exposure=0.0, naked_exposure=0.0,
            contributing_legs=(), highest_margin_contributor=None,
            risk_flags=(snapshot.margin_source,), risk_classification=RISK_INVALID_STATE,
            explanation_source=snapshot.margin_source,
        )

    long_notional = sum(_leg_notional(l) for l in legs if l.side == 1)
    short_notional = sum(_leg_notional(l) for l in legs if l.side == -1)
    covered_short = min(short_notional, long_notional)
    naked_short = short_notional - covered_short

    total_notional = sum(_leg_notional(l) for l in legs)
    contributions = []
    for leg in legs:
        notional = _leg_notional(leg)
        share = (notional / total_notional) if total_notional > 0 else 0.0
        contributions.append(LegMarginContribution(
            symbol=leg.symbol, side=leg.side, qty=leg.qty, notional=notional,
            margin_contribution=snapshot.required_margin * share,
        ))
    highest = max(contributions, key=lambda c: c.margin_contribution, default=None)

    risk_flags = ["BROKER_QUOTED"]
    if naked_short > 0:
        risk_flags.append("NAKED_SHORT_EXPOSURE")
    if long_notional > 0 and short_notional > 0:
        risk_flags.append("HEDGE_PRESENT")

    return MarginExplanation(
        total_required_margin=snapshot.required_margin,
        total_long_exposure=long_notional, total_short_exposure=short_notional,
        covered_exposure=covered_short, naked_exposure=naked_short,
        contributing_legs=tuple(contributions), highest_margin_contributor=highest,
        risk_flags=tuple(risk_flags),
        risk_classification=_classify_book(
            required_margin=snapshot.required_margin,
            naked_short=naked_short, short_notional=short_notional),
        explanation_source=source_label,
    )


class FyersWholeBookMarginProvider:
    """Gate-C-interface adapter (`get_portfolio_margin` AND
    `get_portfolio_margin_with_explanation`) over a real whole-book provider.
    Composition, not inheritance: the certified/uncertified distinction stays
    entirely inside the wrapped Gate C class -- this wrapper cannot flip
    `margin_verified` and never constructs a `MarginSnapshot` of its own."""

    def __init__(self, inner: UncertifiedWholeBookMarginProvider) -> None:
        self._inner = inner

    @property
    def margin_source_label(self) -> str:
        return self._inner.margin_source_label

    def get_portfolio_margin(self, legs, clock) -> MarginSnapshot:
        return self._inner.get_portfolio_margin(legs, clock)

    def get_portfolio_margin_with_explanation(
        self, legs, clock,
    ) -> Tuple[MarginSnapshot, MarginExplanation]:
        snapshot = self._inner.get_portfolio_margin(legs, clock)
        return snapshot, _explain(snapshot, legs, self._inner.margin_source_label)


def build_fyers_margin_provider(*, app_id: str, access_token: str,
                                certified: bool,
                                post: Optional[Callable[..., Any]] = None,
                                ) -> FyersWholeBookMarginProvider:
    """The one construction point. `certified=True` may only be passed on a
    deliberate operator decision (session YAML `providers.margin:
    fyers_certified`) -- see CertifiedWholeBookMarginProvider's docstring for
    the human-confirmation contract, and
    data_certification/fyers_whole_book_span_margin_discovery_*.json for the
    evidence collected to support that decision."""
    caller = FyersSpanMarginCaller(app_id, access_token, post=post)
    inner_cls = CertifiedWholeBookMarginProvider if certified else UncertifiedWholeBookMarginProvider
    return FyersWholeBookMarginProvider(inner_cls(caller))

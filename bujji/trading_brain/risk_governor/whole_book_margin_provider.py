"""Whole-book certified margin provider — BUJJI Options OS v3, Numeric
Risk Governor Gate C.

Mirrors bujji.capital.providers.py's own established tiering
(Uncertified -> Certified via a config decision, never a code change)
and its own documented FYERS span_margin research verbatim:

    POST https://api.fyers.in/api/v2/span_margin
    Body: {"data": [{"symbol": ..., "qty": ..., "side": 1|-1, "type": ...,
                     "productType": ..., "limitPrice": ..., "stopLoss": ...}, ...]}
    Response (reported): {"benefit": ..., "expo": ..., "span": ...,
                          "total": ..., "individual_info": {...}}

That research was itself never confirmed against a live account (the
same source flags at least one community-reported "Invalid input"
error on an apparently well-formed request), and this module has NOT
performed any new live verification either. Everything here is
STRUCTURAL SCAFFOLDING only:

  - request-building and response-parsing are pure functions, fully
    testable against fixtures, never making a network call themselves
  - the actual HTTP call is injected as a plain callable (`HttpCaller`)
    -- this module contains NO networking code of its own
  - `UncertifiedWholeBookMarginProvider.get_portfolio_margin()` always
    returns `margin_verified=False`, regardless of what a live call
    (through the injected caller) returns
  - `CertifiedWholeBookMarginProvider` is the ONLY class that can ever
    produce `margin_verified=True`, and it exists ONLY to be
    constructed after a human has confirmed, against a real live
    account, that this exact request shape is accepted and this exact
    response shape is what comes back -- there is no code-level check
    that enforces this, exactly as bujji.capital.providers.py's own
    CertifiedBrokerMarginProvider already establishes: "never hidden,
    never guessed."

Per the Gate B design this feeds: bujji.trading_brain.risk_governor.
capital_check.CapitalCheckInput requires `margin_verified` and
`required_margin` -- MarginSnapshot below carries exactly those plus
audit provenance, ready to be converted into a CapitalCheckInput once
a real, certified provider exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from bujji.trading_brain.risk_governor.capital_check import CapitalCheckInput
from bujji.trading_brain.risk_governor.position_group_fold import (
    LIFECYCLE_CONSTRUCTED,
    LIFECYCLE_OPEN,
    LIFECYCLE_PARTIALLY_OPEN,
    PositionGroupState,
    net_quantity,
)

_ACTIVE_LIFECYCLE_STATES = (LIFECYCLE_CONSTRUCTED, LIFECYCLE_OPEN, LIFECYCLE_PARTIALLY_OPEN)

SPAN_MARGIN_ENDPOINT = "https://api.fyers.in/api/v2/span_margin"  # UNCONFIRMED, see module docstring

HttpCaller = Callable[[str, Dict[str, Any]], Dict[str, Any]]   # (url, body) -> parsed JSON response
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class MarginLegRequest:
    """One leg of the request payload. `side` follows the FYERS-reported
    convention: 1 = buy, -1 = sell. Field names match the reported
    request shape verbatim -- see module docstring."""

    symbol: str
    qty: int
    side: int                      # 1 (buy) | -1 (sell)
    instrument_type: str            # reported field name: "type"
    product_type: str
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None


@dataclass(frozen=True)
class WholeBookMarginQuoteResult:
    parsed_successfully: bool
    parse_error: Optional[str]
    total_margin: Optional[float]
    benefit: Optional[float]
    expo: Optional[float]
    span: Optional[float]
    individual_info: Optional[Dict[str, Any]]
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarginSnapshot:
    required_margin: Optional[float]
    margin_verified: bool
    margin_source: str              # e.g. "WHOLE_BOOK_UNCERTIFIED" | "WHOLE_BOOK_CERTIFIED" | "QUERY_FAILED"
    as_of: datetime
    quote: Optional[WholeBookMarginQuoteResult]


def build_span_margin_request(legs: List[MarginLegRequest]) -> Dict[str, Any]:
    """Pure. Builds the request body per the documented-but-unconfirmed
    shape -- an arbitrary-length leg array, so the COMPLETE projected
    book (existing positions netted with proposed orders) can be priced
    in one call, never per-leg summed. Never validates leg content
    beyond basic shape -- callers are responsible for supplying a
    correctly-netted, already-validated ProjectedPortfolio leg set."""
    if not legs:
        raise ValueError("build_span_margin_request requires at least one leg")
    return {
        "data": [
            {
                "symbol": leg.symbol,
                "qty": leg.qty,
                "side": leg.side,
                "type": leg.instrument_type,
                "productType": leg.product_type,
                "limitPrice": leg.limit_price,
                "stopLoss": leg.stop_loss,
            }
            for leg in legs
        ]
    }


def parse_span_margin_response(response_json: Any) -> WholeBookMarginQuoteResult:
    """Pure, defensive. NEVER raises on malformed input -- returns
    parsed_successfully=False with a specific parse_error instead, so a
    caller can distinguish 'the broker said no margin available' from
    'we don't understand what the broker just sent us'. Both are
    treated as untrusted by the provider layer either way, but the
    distinction matters for diagnosing a shape mismatch versus a real
    broker-side rejection."""
    if not isinstance(response_json, dict):
        return WholeBookMarginQuoteResult(
            parsed_successfully=False, parse_error=f"response is not a JSON object: {type(response_json)!r}",
            total_margin=None, benefit=None, expo=None, span=None, individual_info=None,
            raw_response={} if not isinstance(response_json, dict) else response_json,
        )

    def _num(key: str) -> Optional[float]:
        value = response_json.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    total_margin = _num("total")
    if total_margin is None:
        return WholeBookMarginQuoteResult(
            parsed_successfully=False, parse_error="response missing or non-numeric 'total' field",
            total_margin=None, benefit=_num("benefit"), expo=_num("expo"), span=_num("span"),
            individual_info=response_json.get("individual_info"), raw_response=response_json,
        )

    individual_info = response_json.get("individual_info")
    if individual_info is not None and not isinstance(individual_info, dict):
        individual_info = None

    return WholeBookMarginQuoteResult(
        parsed_successfully=True, parse_error=None,
        total_margin=total_margin, benefit=_num("benefit"), expo=_num("expo"), span=_num("span"),
        individual_info=individual_info, raw_response=response_json,
    )


class UncertifiedWholeBookMarginProvider:
    """Real request/response mechanics (through an injected caller --
    no networking code of its own), but STRUCTURALLY incapable of
    returning margin_verified=True. This is the class every automated
    test and every non-certified deployment must use."""

    margin_source_label = "WHOLE_BOOK_UNCERTIFIED"

    def __init__(self, http_caller: HttpCaller) -> None:
        self._http_caller = http_caller

    def get_portfolio_margin(
        self, legs: List[MarginLegRequest], clock: Clock,
    ) -> MarginSnapshot:
        as_of = clock()
        try:
            request_body = build_span_margin_request(legs)
        except ValueError:
            return MarginSnapshot(
                required_margin=None, margin_verified=False, margin_source="QUERY_FAILED_EMPTY_LEGS",
                as_of=as_of, quote=None,
            )

        try:
            raw_response = self._http_caller(SPAN_MARGIN_ENDPOINT, request_body)
        except Exception:  # noqa: BLE001 -- a failed query must never crash the caller, only fail closed
            return MarginSnapshot(
                required_margin=None, margin_verified=False, margin_source="QUERY_FAILED",
                as_of=as_of, quote=None,
            )

        quote = parse_span_margin_response(raw_response)
        if not quote.parsed_successfully:
            return MarginSnapshot(
                required_margin=None, margin_verified=False, margin_source="QUERY_FAILED_UNPARSEABLE",
                as_of=as_of, quote=quote,
            )

        return MarginSnapshot(
            required_margin=quote.total_margin, margin_verified=self._verified(),
            margin_source=self.margin_source_label, as_of=as_of, quote=quote,
        )

    def _verified(self) -> bool:
        return False


class IllegalMarginProjectionInputError(Exception):
    """Raised by project_whole_book_to_margin_legs on missing/invalid
    caller-supplied data -- never silently drops a leg from a margin
    calculation, since that would understate the true whole-book
    requirement."""


def project_whole_book_to_margin_legs(
    active_states: List[PositionGroupState],
    contracts_by_client_order_id: Dict[str, Any],   # NiftyOptionContract, read via .contract_symbol
    sides_by_client_order_id: Dict[str, str],        # "BUY" | "SELL", per client_order_id
    reference_prices_by_client_order_id: Dict[str, Optional[float]],
    instrument_type: str,
    product_type: str,
) -> List[MarginLegRequest]:
    """Pure. Projects Gate A's tracked whole-book state (every ACTIVE
    position group -- CONSTRUCTED/OPEN/PARTIALLY_OPEN, the exact same
    set portfolio_limits.py already treats as active) into the flat
    leg list build_span_margin_request needs, so the COMPLETE book can
    be priced in one call rather than summed per-trade.

    Gate A's own journal deliberately carries NO contract data and NO
    buy/sell side -- LegState.requested_quantity and net_quantity() are
    always UNSIGNED magnitudes (verified directly against the fold
    logic and msi_entry_bridge's own construction code: requested_
    quantities[coid] = leg.ratio * lot_size, always positive, with
    side tracked in a completely separate core_sides map). So this
    function requires the caller to supply contract/side/price data
    for the WHOLE BOOK explicitly, exactly symmetric with how
    msi_entry_bridge.py already supplies these per-trade -- never
    inferred, never defaulted, never guessed at.

    Quantity convention mirrors Gate B's own established pre-trade/
    post-fill split (defined_risk.py's is_pre_trade branch): a
    CONSTRUCTED leg (nothing has filled yet) uses requested_quantity;
    an OPEN/PARTIALLY_OPEN leg uses net_quantity().

    A resulting zero quantity is handled DIFFERENTLY depending on
    which side of that split it comes from -- audit finding: an
    earlier version of this function treated both cases identically
    (silently skip), which let a missing requested_quantity on ONE leg
    of a multi-leg CONSTRUCTED structure silently produce a margin
    request for only the REMAINING legs (e.g. dropping a short leg
    from a spread and pricing only the long leg as if it were a naked
    position) -- a dangerously understated, misleading partial figure.
    - Post-fill (OPEN/PARTIALLY_OPEN) net_quantity() == 0 is a
      LEGITIMATE state (this leg has been fully closed out within an
      otherwise-active group, e.g. after a partial adjustment) and is
      correctly skipped.
    - Pre-trade (CONSTRUCTED) requested_quantity missing or zero is a
      DATA INTEGRITY problem -- a genuinely constructed multi-leg
      trade should never have an unset leg quantity -- and raises
      instead of silently omitting that leg.

    Raises IllegalMarginProjectionInputError if any active leg is
    missing a contract, side, reference_price, or (pre-trade only) a
    valid requested_quantity -- never silently omits a leg, since a
    margin call missing a real position would UNDERSTATE the true
    whole-book requirement, the opposite of fail-closed."""
    legs: List[MarginLegRequest] = []
    for state in active_states:
        if state.lifecycle_state not in _ACTIVE_LIFECYCLE_STATES:
            continue
        is_pre_trade = state.lifecycle_state == LIFECYCLE_CONSTRUCTED
        for coid, leg in state.legs.items():
            if is_pre_trade:
                if not leg.requested_quantity:
                    raise IllegalMarginProjectionInputError(
                        f"active CONSTRUCTED leg {coid!r} in position group {state.position_group_id!r} "
                        "has no requested_quantity -- cannot silently omit one leg of a multi-leg "
                        "structure from a whole-book margin request, since that would understate the "
                        "true requirement (e.g. dropping a short leg from a spread and sending only the "
                        "long leg as if it were a naked position)"
                    )
                qty = leg.requested_quantity
            else:
                qty = net_quantity(leg)
                if qty == 0:
                    continue  # legitimate: this leg has been fully closed out within an otherwise-active group
            contract = contracts_by_client_order_id.get(coid)
            if contract is None:
                raise IllegalMarginProjectionInputError(
                    f"missing contract for active leg {coid!r} in position group {state.position_group_id!r}"
                )
            side_str = sides_by_client_order_id.get(coid)
            if side_str not in ("BUY", "SELL"):
                raise IllegalMarginProjectionInputError(
                    f"missing or invalid side for active leg {coid!r} -- got {side_str!r}, expected 'BUY' or 'SELL'"
                )
            if coid not in reference_prices_by_client_order_id:
                raise IllegalMarginProjectionInputError(
                    f"missing reference_price for active leg {coid!r} in position group {state.position_group_id!r}"
                )
            legs.append(MarginLegRequest(
                symbol=contract.contract_symbol,
                qty=abs(qty),
                side=1 if side_str == "BUY" else -1,
                instrument_type=instrument_type,
                product_type=product_type,
                limit_price=reference_prices_by_client_order_id[coid],
                stop_loss=None,
            ))
    return legs


class CertifiedWholeBookMarginProvider(UncertifiedWholeBookMarginProvider):
    """Identical mechanism to UncertifiedWholeBookMarginProvider, but
    `margin_verified=True`. Construct this ONLY after a human has
    confirmed, against a real live account, that build_span_margin_request's
    request shape is accepted by the real endpoint and
    parse_span_margin_response correctly interprets the real response
    -- there is no code-level check that enforces this. Exactly the
    same deliberate, config-level operator decision as
    bujji.capital.providers.CertifiedBrokerMarginProvider."""

    margin_source_label = "WHOLE_BOOK_CERTIFIED"

    def _verified(self) -> bool:
        return True


def margin_snapshot_to_capital_check_input(
    snapshot: MarginSnapshot, available_capital: Optional[float], configured_risk_capital: float,
) -> CapitalCheckInput:
    """Pure. The connective tissue between this module's producer-side
    output and capital_check.py's consumer-side contract -- described
    in this module's own docstring as the eventual next step, and
    built now as pure scaffolding (no live call happens here or is
    triggered by calling this).

    `available_capital` and `configured_risk_capital` are NOT part of
    a MarginSnapshot -- a margin PROVIDER only ever reports what a
    proposed book would require, never what capital is actually on
    hand or how much of it policy allows risking. Both must be
    supplied explicitly by the caller from their own real source
    (broker balance query, runtime config); this function never
    infers, defaults, or hardcodes either.

    A straight field passthrough for `margin_verified`/`required_margin`
    /`margin_source` -- this function performs no certification
    judgment of its own; that decision was already made (or not) when
    the snapshot was produced by an Uncertified/CertifiedWholeBook
    MarginProvider. capital_check.assess_capital independently
    re-validates `required_margin` (None/negative both fail closed)
    regardless of `margin_verified`, so this function does not need to
    duplicate that guard."""
    return CapitalCheckInput(
        margin_verified=snapshot.margin_verified,
        required_margin=snapshot.required_margin,
        available_capital=available_capital,
        configured_risk_capital=configured_risk_capital,
        margin_source=snapshot.margin_source,
    )

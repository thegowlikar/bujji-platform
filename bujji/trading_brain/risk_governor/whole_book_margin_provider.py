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

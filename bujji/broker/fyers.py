"""FYERS broker adapter.

Maps the broker-agnostic :class:`Broker` interface onto the real FYERS API,
via the official ``fyers-apiv3`` Python SDK. Network calls are isolated
behind ``_call`` so the transport can be swapped without touching mapping
logic.

TRANSPORT: uses ``fyersModel.FyersModel`` (the official SDK), which is
synchronous — each call is run via ``asyncio.to_thread`` so it doesn't block
the event loop. This was verified end-to-end with a live, authenticated
session (see ``docs/FYERS_TRANSPORT_READINESS.md``): real profile, quote,
history, positions, orderbook, and funds calls all returned live data through
this exact SDK from this codebase's own environment. (An earlier revision of
this file assumed no direct network path existed and left ``_call`` as an
unwired stub — that assumption was wrong and has been corrected; MCP tools
were never the transport, only a verification aid used once.)

Every mapping method has been corrected against REAL response shapes and, for
place_order/cancel_order, the real SDK parameter names (which differ from
what an earlier revision assumed) — see
``docs/FYERS_TRANSPORT_READINESS.md`` for the full verification record and the
one still-unresolved limitation (ATM option contract resolution — see
``resolve_atm_contract``'s docstring, now solved by ``instrument_master.py``).
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fyers_apiv3 import fyersModel

from ..core.clock import epoch_to_ist
from ..core.config import BrokerConfig
from ..core.enums import Direction, OrderStatus, Side
from ..core.models import Candle, OptionContract, OrderRequest, OrderResult
from .base import Broker
from .errors import (AuthenticationError, PositionReadError,
                     UnverifiedPositionSchemaError)
from .fyers_token_manager import FyersTokenManager

# Best-effort FYERS error-code classification for auth/session failures
# (E1/E2). These codes are commonly documented for FYERS API v3 as token/
# authorization related, but MUST be verified against the live FYERS error-code
# reference before go-live — treat this set as a starting point, not gospel.
# The keyword fallback below is the more robust signal and does not depend on
# getting the exact code list right.
# NOTE: -300 was removed after live verification — it is FYERS's "Invalid
# symbol provided" code, NOT an auth failure. Misclassifying it here caused a
# real bug: a bad symbol in get_recent_candles raised AuthenticationError
# instead of surfacing as the (correct) plain error it is.
_FYERS_AUTH_ERROR_CODES: frozenset[int] = frozenset({-8, -15, -16, -17})
_AUTH_KEYWORDS = (
    "token", "auth", "unauthoriz", "unauthenticated", "session expired",
    "login again", "invalid access", "not logged in",
)

# Verified live (see docs/FYERS_TRANSPORT_READINESS.md): FYERS order status
# codes. NOT independently re-derivable from a live order in this pass
# (doing so would require placing a real order, which this codebase will not
# do) — carried over from the prior implementation and flagged here as
# UNVERIFIED. Cross-check against FYERS's official order-status documentation
# before relying on it in full-live mode.
_ORDER_STATUS_MAP = {
    2: OrderStatus.FILLED,
    1: OrderStatus.PENDING,
    5: OrderStatus.REJECTED,
    6: OrderStatus.CANCELLED,
}

# BUG FOUND DURING LIVE VERIFICATION: the index symbol is NOT simply
# "NSE:{underlying}-INDEX" — that produces "NSE:NIFTY-INDEX", which is not a
# real FYERS instrument. The verified-live canonical symbol is
# "NSE:NIFTY50-INDEX", confirmed directly against the real fyers-apiv3 SDK
# for BOTH quotes() and history() (a prior pass believed these needed two
# different forms — "NSE:NIFTY 50" for history — based on a verification
# session mediated through an MCP tool that silently normalizes that alias;
# calling the raw SDK directly proved "NSE:NIFTY 50" is REJECTED by history()
# with "Invalid symbol provided". That earlier conclusion is corrected here.)
# Only NIFTY is mapped/verified — anything else falls back to the old,
# unverified "-INDEX" construction and must not be trusted without the same
# live check.
_INDEX_SYMBOL = {"NIFTY": "NSE:NIFTY50-INDEX"}


def _index_symbol(underlying: str) -> str:
    return _INDEX_SYMBOL.get(underlying, f"NSE:{underlying}-INDEX")


def _futures_symbol(underlying: str, now: Optional[datetime.datetime] = None) -> str:
    """Best-effort near-month futures symbol construction, following
    FYERS's documented convention NSE:{UNDERLYING}{YY}{MON}FUT (e.g.
    NSE:NIFTY26AUGFUT). Unlike _index_symbol()/get_quote()/get_vix()/
    get_option_chain() (each carrying a live-verified date in their own
    docstrings), this has NOT been live-verified against a real FYERS
    response this session -- no futures quote call has been made yet.
    Treat as provisional until confirmed against a real response."""
    dt = now or datetime.datetime.now(datetime.timezone.utc)
    return f"NSE:{underlying}{dt.strftime('%y')}{dt.strftime('%b').upper()}FUT"


# --- Transport pacing ---------------------------------------------------
# FYERS enforces a per-account request-rate ceiling and answers a burst with
# {"s": "error", "code": 429, ...} -- already classified by
# _raise_if_error() below, which deliberately raises rather than letting a
# starved caller fall through to an empty result.
#
# Measured live on 2026-08-17: a single MarketDataAdapter.build_snapshot()
# issues 85 calls in 2.98s (peak 31 within a 1s window), because the option
# chain adapter quotes each contract individually -- 82 contracts, one call
# each. The NEXT history call was then refused with 429 and the trading
# session died during startup, before it could form a regime.
#
# The ceiling is per ACCOUNT, not per object, and a session constructs more
# than one FyersBroker against the same credentials (one for the chain, one
# for the intelligence cycle). This state is therefore deliberately MODULE
# level, and guarded by a threading.Lock rather than an asyncio.Lock:
# the runner calls asyncio.run() repeatedly, and a lock bound to one
# event loop is invalid in the next.
_MIN_SECONDS_BETWEEN_CALLS = 0.12  # ~8.3/s, under the documented 10/s ceiling.
from .rate_budget import DEFAULT_BUDGET_PATH

_pacing_lock = threading.Lock()
_next_call_allowed_at = 0.0


# CP-D: the ceiling is per ACCOUNT, and Bujji now runs FOUR processes against
# one account (spot/VIX capture, option chain, depth poller, trading session
# -- three of them waking together at 09:14). Each pacing itself to ~8.3/s
# presented the account with up to ~33/s. Schedule separation was standing in
# for a resource budget, which is why the trading unit's fire time carried a
# rate-limit offset for months. The budget below is HOST-WIDE and applied
# FIRST; the module-level pacer stays behind it as a second layer, so a
# budget that cannot be reached degrades to exactly the old behaviour rather
# than to no pacing at all.
_rate_budget = None
_rate_budget_warned = False


def _host_rate_budget():
    global _rate_budget
    if _rate_budget is None:
        from .rate_budget import CrossProcessRateBudget

        _rate_budget = CrossProcessRateBudget(
            path=os.environ.get("BUJJI_FYERS_RATE_BUDGET_PATH", DEFAULT_BUDGET_PATH),
            min_interval_seconds=_MIN_SECONDS_BETWEEN_CALLS,
        )
    return _rate_budget


def _wait_for_slot() -> None:
    """Block the CALLING THREAD until this ACCOUNT may issue another call.

    Two layers, in order:
      1. the host-wide budget -- every Bujji process on this box shares it;
      2. this interpreter's own pacer -- unchanged, and the fallback when
         the shared budget is unreachable.

    In both layers the slot is reserved under a lock and the wait happens
    OUTSIDE it, so concurrent callers queue behind one another instead of
    all waking against the same timestamp and bursting together.
    """
    global _next_call_allowed_at, _rate_budget_warned

    outcome = _host_rate_budget().reserve()
    if not outcome.shared and not _rate_budget_warned:
        # Once per process: a rate ceiling is a throughput protection, not a
        # safety guard, so this degrades rather than refusing -- but it must
        # not degrade silently, or the account is over-driven invisibly.
        _rate_budget_warned = True
        logging.getLogger(__name__).warning(
            "FYERS host-wide rate budget unavailable (%s) -- falling back to this process's "
            "own pacer only. Concurrent Bujji processes can now exceed the account ceiling.",
            outcome.reason)

    with _pacing_lock:
        slot = max(time.monotonic(), _next_call_allowed_at)
        _next_call_allowed_at = slot + _MIN_SECONDS_BETWEEN_CALLS
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


# --- Rate-limit retry -----------------------------------------------------
# Pacing lowers the ODDS of a refusal; it cannot remove them. The ceiling is
# per ACCOUNT and this pacer is per interpreter, so a second Bujji process --
# a capture session, an operator running a script by hand -- can push the
# account over on its own. A refusal is also the one error class where
# "wait, then ask again" is unambiguously the right answer, so it is retried
# here rather than ending a trading session.
#
# ONLY READ ACTIONS ARE RETRIED, as an allowlist. A refused write must NOT be
# repeated automatically: this codebase cannot prove from a refusal alone
# whether the exchange rejected the instruction or accepted it and refused
# only the acknowledgement, and repeating it under the second reading would
# duplicate it. An action absent from this set is therefore never retried,
# including any added later -- the safe default is the automatic one.
_RETRYABLE_ACTIONS = frozenset({
    "profile", "positions", "orders", "funds", "holdings",
    "ltp", "historical", "optionchain", "depth",
})

_RATE_LIMIT_CODE = 429
# Two ceilings exist, verified live on 2026-08-17 against the real account:
# a per-SECOND one answering {"code": 429, "message": "Bad request"}, which a
# short wait clears, and a per-MINUTE one answering {"code": 429, "message":
# "request limit reached"}, which needs most of a minute. Hence one short
# wait, then progressively longer -- at most ~40s added, against a 300s
# management cadence.
_RATE_LIMIT_BACKOFF_SECONDS = (2.0, 8.0, 30.0)


def _is_rate_limited(data) -> bool:
    """True only for a rate-limit refusal. Every other failure -- auth,
    bad symbol, malformed request -- is left alone: retrying those just
    repeats the same failure more slowly."""
    if not isinstance(data, dict):
        return False
    try:
        return int(data.get("code")) == _RATE_LIMIT_CODE
    except (TypeError, ValueError):
        return False


def _paced(method):
    """Wrap an SDK method so the pacing wait runs in the worker thread.

    Pacing must not happen on the event loop: these calls are dispatched via
    asyncio.to_thread, and sleeping on the loop would stall every other
    coroutine rather than just the caller waiting for its slot.
    """
    def call(*args):
        _wait_for_slot()
        return method(*args)
    return call


# THE RAW POSITION SCHEMA IS NOT VERIFIED (Rule 13, 2026-08-21).
#
# get_open_positions() normalizes FYERS rows to {symbol, side, qty, avg_price}
# by reading `netQty` and `netAvg` off each row of `netPositions`. The
# top-level shape was confirmed live against an EMPTY position book; the
# per-row field names were carried over from an earlier implementation and
# have never been seen against a real open position, as that method's own
# comment states.
#
# EVERY safety property built on top of it -- EOD flat verification, residual
# sizing, orphan detection, emergency-close verification -- depends on those
# two names being right. If `netQty` were actually named something else, each
# row would read as qty 0, every position would be filtered out as flat, and
# the account would look EMPTY. That failure is silent and points the wrong
# way: it manufactures flatness.
#
# This flag exists so that fact is a gate rather than a comment. It must not
# be flipped by reasoning; only by an operator observing a REAL open position
# and confirming the field names against the live payload.
FYERS_POSITION_SCHEMA_VERIFIED = False


class FyersBroker(Broker):
    # The EXCHANGE holds the order book, not this process. A not-found from
    # here is real evidence about the order's fate, so startup recovery may
    # treat it as authoritative.
    order_book_survives_restart = True

    # Mirrors the module-level gate above so callers can read it off the
    # broker instance they already hold.
    position_schema_verified = FYERS_POSITION_SCHEMA_VERIFIED

    name = "fyers"

    def __init__(self, config: BrokerConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger
        self._connected = False
        self._client: Optional[fyersModel.FyersModel] = None
        # C3 idempotency bridge: FYERS's order-lookup tools key on FYERS's own
        # order_id, not on an arbitrary client tag (see get_order's docstring
        # for why). This in-memory map lets get_order/cancel_order resolve our
        # client_order_id to the FYERS order_id once place_order has seen it.
        # It does NOT survive a process restart — recovery after a restart
        # relies on get_open_positions()/reconcile (C1), not this cache.
        self._cid_to_order_id: dict[str, str] = {}
        self._instruments = None  # Lazily built InstrumentMaster (Phase C).
        self._token_manager = FyersTokenManager(
            config.app_id, config.app_secret, config.refresh_token, config.pin,
            logger, config.credentials_file,
        )

    def live_tick_credentials(self) -> Optional[tuple]:
        if not self._cfg.app_id or not self._cfg.access_token:
            return None
        return (self._cfg.app_id, self._cfg.access_token)

    def _get_client(self) -> fyersModel.FyersModel:
        if self._client is None:
            self._client = fyersModel.FyersModel(
                client_id=self._cfg.app_id,
                is_async=False,
                token=self._cfg.access_token,
                log_path="logs",
            )
        return self._client

    def _invalidate_client(self) -> None:
        """Force _get_client() to rebuild against the current access_token —
        used after an automatic refresh replaces it."""
        self._client = None

    # Dispatch table: action name -> (sdk_method_name, takes_data_arg).
    # Verified live against a real authenticated session — see
    # docs/FYERS_TRANSPORT_READINESS.md.
    _NO_ARG_ACTIONS = {
        "profile": "get_profile",
        "positions": "positions",
        "orders": "orderbook",
        "funds": "funds",
        "holdings": "holdings",
    }
    _DATA_ARG_ACTIONS = {
        "ltp": "quotes",
        "historical": "history",
        "place_order": "place_order",
        "cancel_order": "cancel_order",
        "optionchain": "optionchain",
        "depth": "depth",
    }

    async def _call(self, action: str, **params: Any) -> dict:
        """Single choke-point for all FYERS transport.

        Runs the synchronous ``fyers-apiv3`` SDK call in a thread so it
        doesn't block the event loop. Every response MUST be passed through
        :meth:`_raise_if_auth_error` before being used (every call site below
        already does this) — do not bypass it for a "quick" new call site.

        Retries a rate-limit refusal on READ actions only (see
        ``_RETRYABLE_ACTIONS``). ERROR SEMANTICS ARE UNCHANGED: once the
        retries are spent the final response is returned exactly as received,
        so the caller's own ``_raise_if_error`` still raises as it always
        has. Nothing is swallowed, and no caller needs to know this happens.
        """
        for wait_seconds in _RATE_LIMIT_BACKOFF_SECONDS:
            data = await self._invoke_once(action, **params)
            if action not in _RETRYABLE_ACTIONS or not _is_rate_limited(data):
                return data
            self._log.warning(
                "fyers_rate_limited_retrying",
                extra={"data": {"action": action, "wait_seconds": wait_seconds,
                                "message": data.get("message")}},
            )
            # asyncio.sleep, not time.sleep: this waits on the event loop so
            # other coroutines keep running, unlike the pacer's wait which
            # deliberately blocks its own worker thread.
            await asyncio.sleep(wait_seconds)
        return await self._invoke_once(action, **params)

    async def _invoke_once(self, action: str, **params: Any) -> dict:
        """One paced SDK round-trip. No retry logic lives here."""
        client = self._get_client()
        if action in self._NO_ARG_ACTIONS:
            method = getattr(client, self._NO_ARG_ACTIONS[action])
            return await asyncio.to_thread(_paced(method))
        if action in self._DATA_ARG_ACTIONS:
            method = getattr(client, self._DATA_ARG_ACTIONS[action])
            return await asyncio.to_thread(_paced(method), params)
        raise ValueError(f"Unknown FYERS action: {action}")

    def _raise_if_auth_error(self, data: dict,
                             http_status: Optional[int] = None) -> None:
        """Classify a FYERS response and raise AuthenticationError if it
        signals an invalid/expired token or invalidated session (E1/E2).

        Never raises for anything else — this is purely a classifier, not a
        general error handler. A response that fails for other reasons (bad
        symbol, insufficient margin, etc.) passes through untouched for the
        normal mapping/handling logic to deal with.

        Verified live: every FYERS response shape observed (profile, ltp,
        historical, positions, orders) carries `s`/`code`/`message` at the
        top level, matching what this classifier inspects.
        """
        if http_status in (401, 403):
            raise AuthenticationError(
                f"FYERS auth failure: HTTP {http_status}: {data}"
            )
        code = data.get("code")
        if isinstance(code, int) and code in _FYERS_AUTH_ERROR_CODES:
            raise AuthenticationError(
                f"FYERS auth failure: code={code} message={data.get('message')}"
            )
        if str(data.get("s", "")).lower() == "error":
            message = str(data.get("message", "")).lower()
            if any(kw in message for kw in _AUTH_KEYWORDS):
                raise AuthenticationError(
                    f"FYERS auth failure (keyword match): {data.get('message')}"
                )

    def _raise_if_error(self, data: dict, action: str) -> None:
        """Raise on ANY non-auth FYERS error response (e.g. rate limiting —
        verified live: ``{"s": "error", "code": 429, "message": "request
        limit reached"}``), rather than letting the caller silently fall
        through to an empty/default result.

        Transport-behavior fix only: `get_recent_candles` previously treated
        an error response identically to "no candles in this window" —
        returning an empty list instead of surfacing the failure — so a
        rate-limited fetch was silently skipped rather than retried, unlike
        get_spot/get_ltp (which already raise via a missing-key lookup and so
        already get retried by ExecutionEngine). This makes that behavior
        consistent without touching any strategy or orchestration logic —
        the caller (ExecutionEngine._with_retry) already retries any
        exception; this only ensures one is actually raised.
        """
        if str(data.get("s", "")).lower() == "error":
            raise RuntimeError(
                f"FYERS {action} error: code={data.get('code')} "
                f"message={data.get('message')}"
            )

    async def connect(self) -> None:
        if self._connected:
            return
        # Fail fast and unambiguously when credentials are simply absent —
        # do not let this fall through to a transport-level error (which,
        # with `_call` unwired, would otherwise surface as a confusing
        # `NotImplementedError` regardless of whether credentials were ever
        # provided at all).
        if not self._cfg.app_id or not self._cfg.access_token:
            raise AuthenticationError(
                "FYERS credentials missing: FYERS_APP_ID and/or "
                "FYERS_ACCESS_TOKEN are not set. Set both environment "
                "variables and restart."
            )
        # Verified live shape: {"s": "ok", "code": 200, "message": "",
        # "data": {...profile fields...}}.
        try:
            data = await self._call("profile")  # Validates the access token.
            self._raise_if_auth_error(data)
        except AuthenticationError as exc:
            # The stored access_token is invalid/expired. Attempt one
            # automatic renewal (see docs/FYERS_TOKEN_LIFECYCLE.md) before
            # giving up — only proceeds if refresh_token/secret/pin are
            # actually configured; otherwise raises the same clear,
            # actionable error as before, unchanged.
            if not self._token_manager.can_refresh:
                raise
            self._log.warning("fyers_access_token_invalid_attempting_refresh: %s", exc)
            new_token = await self._token_manager.refresh()
            self._cfg.access_token = new_token
            self._invalidate_client()
            data = await self._call("profile")
            self._raise_if_auth_error(data)
        self._connected = True

    async def get_spot(self, underlying: str) -> float:
        symbol = _index_symbol(underlying)
        return await self._quote(symbol)

    async def _quote(self, symbol: str) -> float:
        # Verified live against the REAL fyers-apiv3 SDK (not the MCP tool's
        # own reshaped output, which an earlier pass mistakenly treated as
        # the raw shape): `quotes({"symbols": "A,B"})` -> a LIST under "d",
        # each item `{"n": symbol, "v": {"lp": price, ...}}`.
        data = await self._call("ltp", symbols=symbol)
        self._raise_if_auth_error(data)
        for row in data.get("d", []):
            if row.get("n") == symbol:
                return float(row["v"]["lp"])
        raise KeyError(f"symbol {symbol} not found in quotes response: {data}")

    async def get_spot_raw(self, underlying: str) -> dict:
        """Phase 17I.6.1 — raw pass-through of the FYERS 'ltp' action's full
        response for the spot index, mirroring `get_option_chain_raw()`'s
        and `get_depth()`'s own discipline: no extraction, no renaming, no
        discarding.

        DIAGNOSTIC / SOURCE DISCOVERY ONLY. Unlike `get_spot()`/`_quote()`
        (which extract only `lp` and raise `KeyError` if the symbol isn't
        found), this returns whatever FYERS actually sent, untouched, so a
        human can inspect it for fields `_quote()` has never looked at --
        this project has never verified whether the real `v` dict carries
        any timestamp beyond `lp`. Must NOT be used to construct a
        `RawObservation` directly -- that remains `get_spot()`'s job,
        through the existing certified capture path.
        """
        symbol = _index_symbol(underlying)
        data = await self._call("ltp", symbols=symbol)
        self._raise_if_auth_error(data)
        self._raise_if_error(data, "ltp")
        return data

    async def get_recent_candles(
        self, underlying: str, minutes: int, count: int
    ) -> list[Candle]:
        # NOTE ON VWAP/VOLUME: the FYERS live quote returns volume=0 and atp=0
        # for the index, so there is no broker-provided VWAP to consume. The
        # HISTORICAL endpoint, however, returns genuine per-candle volume (the
        # 6th field). We therefore compute a true volume-weighted VWAP from
        # these candles. Do not swap this for the quote's volume/atp.
        #
        # The real SDK's history() has no "give me the last N candles" mode —
        # it takes an explicit date range. Request from a few calendar days
        # back (comfortably covers weekends/holidays for any `count` this
        # codebase actually uses — 1, for the live candle loop) and take the
        # tail. Verified live: rows are [epoch, open, high, low, close,
        # volume] (6 elements) under a top-level "candles" key.
        from datetime import timedelta
        from ..core.clock import now_ist
        today = now_ist().date()
        lookback_days = max(5, (count // 75) + 3)  # ~75 five-min bars/session.
        data = await self._call(
            "historical",
            symbol=_index_symbol(underlying),
            resolution=str(minutes),
            date_format="1",
            range_from=(today - timedelta(days=lookback_days)).isoformat(),
            range_to=today.isoformat(),
            cont_flag="1",
        )
        self._raise_if_auth_error(data)
        self._raise_if_error(data, "historical")
        candles = [
            Candle(
                # D2: MUST be explicit IST — `datetime.fromtimestamp(row[0])`
                # would interpret the epoch using the host's local timezone,
                # silently wrong the moment the host isn't configured for IST.
                timestamp=epoch_to_ist(row[0]),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5] if len(row) > 5 else 0.0,  # Real index volume.
            )
            for row in data.get("candles", [])
        ]
        return candles[-count:] if count else candles

    async def get_option_candles(
        self, contract: OptionContract, minutes: int, count: int
    ) -> list[Candle]:
        """Real per-candle OHLC+volume for a SPECIFIC option contract --
        used to build a genuine volume-weighted Premium VWAP (as opposed to
        get_ltp(), which only returns a last-traded-price snapshot with no
        volume attached at all).

        VERIFIED LIVE 2026-07-19 (during the volume-weighted VWAP design
        pass): real FYERS ATM option 5-minute candles carry genuine,
        substantial, non-zero volume throughout the trading day -- e.g. a
        real NIFTY ATM CE showed 75/75 candles with real volume (2M-12M
        range) across a full session, zero zero-volume candles. This
        contradicts get_recent_candles' index-only note above (index
        QUOTES report volume=0; option candles genuinely do not, at least
        for ATM strikes) -- see docs/AUDIT_LOG.md.
        """
        from datetime import timedelta
        from ..core.clock import now_ist
        today = now_ist().date()
        lookback_days = max(5, (count // 75) + 3)
        data = await self._call(
            "historical",
            symbol=contract.symbol,
            resolution=str(minutes),
            date_format="1",
            range_from=(today - timedelta(days=lookback_days)).isoformat(),
            range_to=today.isoformat(),
            cont_flag="1",
        )
        self._raise_if_auth_error(data)
        self._raise_if_error(data, "historical")
        candles = [
            Candle(
                timestamp=epoch_to_ist(row[0]),
                open=row[1], high=row[2], low=row[3], close=row[4],
                volume=row[5] if len(row) > 5 else 0.0,
            )
            for row in data.get("candles", [])
        ]
        return candles[-count:] if count else candles

    async def resolve_atm_contract(
        self, underlying, spot, direction, strike_interval, lot_size
    ) -> OptionContract:
        """Resolve the nearest-expiry ATM option contract.

        Backed by :class:`InstrumentMaster` — FYERS's real, public NFO
        symbol-master CSV (downloaded, cached 24h, parsed locally). No
        hardcoded symbols. A prior pass could not verify this at all: the
        MCP-tool-mediated instrument search only covered the cash-market
        segment (no F&O), and manual symbol guesses failed. Downloading the
        real NFO file directly (verified live — see
        docs/FYERS_TRANSPORT_READINESS.md) resolved it properly.
        """
        opt = self.option_type_for(direction)
        return await self._instrument_master().resolve_atm(
            underlying, spot, opt, strike_interval, lot_size
        )

    def _instrument_master(self):
        if self._instruments is None:
            from .instrument_master import InstrumentMaster
            self._instruments = InstrumentMaster(Path("data/instrument_master"), self._log)
        return self._instruments

    async def get_ltp(self, contract: OptionContract) -> float:
        return await self._quote(contract.symbol)

    async def get_quote(self, contract: OptionContract) -> Optional[dict]:
        """LIVE-VERIFIED (2026-07-20, see docs/MARKET_INTELLIGENCE_CORE.md's
        Liquidity Brain section): the real quotes response's `v` dict
        includes `bid`, `ask`, `spread`, with `spread == ask - bid`
        confirmed to hold exactly on real NIFTY weekly ATM CE/PE quotes.
        Deliberately does NOT use the same response's `volume` field
        (see the Liquidity Brain's docstring for why -- it returned an
        implausible per-symbol figure that was never corroborated).
        """
        data = await self._call("ltp", symbols=contract.symbol)
        self._raise_if_auth_error(data)
        for row in data.get("d", []):
            if row.get("n") == contract.symbol:
                v = row.get("v", {})
                bid, ask = v.get("bid"), v.get("ask")
                if bid is None or ask is None or bid <= 0 or ask <= 0:
                    return None
                spread = v.get("spread")
                return {
                    "bid": float(bid), "ask": float(ask),
                    "spread": float(spread) if spread is not None else float(ask) - float(bid),
                }
        return None

    async def get_vix(self) -> Optional[dict]:
        """LIVE-VERIFIED (2026-07-20, see docs/MARKET_INTELLIGENCE_CORE.md's
        Event Brain section): NSE:INDIAVIX-INDEX is a real, live-quotable
        symbol via the same 'quotes' endpoint get_quote() uses -- confirmed
        live with lp=13.02, prev_close_price=13.15, a plausible historical
        India VIX level. Uses the same 'ltp' action/symbol-list response
        shape already verified for get_spot()/_quote().
        """
        data = await self._call("ltp", symbols="NSE:INDIAVIX-INDEX")
        self._raise_if_auth_error(data)
        for row in data.get("d", []):
            if row.get("n") == "NSE:INDIAVIX-INDEX":
                v = row.get("v", {})
                level, prev_close = v.get("lp"), v.get("prev_close_price")
                if level is None or level <= 0:
                    return None
                result = {"level": float(level)}
                if prev_close is not None and prev_close > 0:
                    result["prev_close"] = float(prev_close)
                return result
        return None

    async def get_vix_raw(self) -> dict:
        """Phase 17I.6.1 — raw pass-through of the FYERS 'ltp' action's full
        response for India VIX, mirroring `get_option_chain_raw()`'s and
        `get_depth()`'s own discipline.

        DIAGNOSTIC / SOURCE DISCOVERY ONLY. Unlike `get_vix()` (which
        extracts only `lp`/`prev_close_price` and returns `None` on a
        missing/invalid level), this returns whatever FYERS actually sent,
        untouched -- including any field `get_vix()` has never looked at.
        Must NOT be used to construct a `RawObservation` directly -- that
        remains `get_vix()`'s job, through the existing certified capture
        path.
        """
        data = await self._call("ltp", symbols="NSE:INDIAVIX-INDEX")
        self._raise_if_auth_error(data)
        self._raise_if_error(data, "ltp")
        return data

    async def get_option_chain_raw(self, underlying: str, strike_count: int = 5) -> Optional[dict]:
        """Raw pass-through of the FYERS 'optionchain' action's full
        response -- deliberately does NOT extract/rename/discard any
        field, unlike get_option_chain() below (which only ever reads
        strike_price/option_type/oi and drops the rest).

        This exists for the same reason get_depth() does (Phase 17F.1.2):
        get_option_chain()'s narrow extraction was written for one
        purpose (OI reconciliation) and has never been proof that OTHER
        fields (LTP, bid, ask, volume, greeks-if-any) are absent from the
        real response -- only that this codebase has never looked. A
        caller needing the full row shape (e.g. building a real
        OptionObservation with a premium price, Phase 17F.7) must not
        guess field names; this method returns the response exactly as
        FYERS sent it so those names can be read from a real, dated
        capture instead.

        Returns None if the response carries no "data" key at all
        (mirrors this file's other raw-passthrough methods' handling of
        a missing/invalid result).
        """
        data = await self._call(
            "optionchain", symbol=_index_symbol(underlying),
            strikecount=strike_count, timestamp="",
        )
        self._raise_if_auth_error(data)
        self._raise_if_error(data, "optionchain")
        return data if "data" in data else None

    async def get_option_chain(
        self, underlying: str, spot: float, strike_count: int = 5
    ) -> Optional[list[tuple[float, float, float]]]:
        """LIVE-VERIFIED (2026-07-20, see docs/MARKET_INTELLIGENCE_CORE.md's
        Structure Brain section): the real `optionchain` endpoint (distinct
        from the plain `quotes` call `get_quote` uses above) returns
        per-strike `oi`/`prev_oi`/`oich` for both CE and PE, with
        `oich == oi - prev_oi` confirmed to hold exactly on real NIFTY
        strikes -- genuine, internally consistent open interest.
        """
        data = await self._call(
            "optionchain", symbol=_index_symbol(underlying),
            strikecount=strike_count, timestamp="",
        )
        self._raise_if_auth_error(data)
        self._raise_if_error(data, "optionchain")
        # Verified live (2026-07-20): unlike the plain `quotes` endpoint
        # (which nests its list under "d"), `optionchain`'s payload is
        # nested under a top-level "data" key -- confirmed by direct
        # inspection of the raw response, not assumed from the SDK docstring.
        rows = data.get("data", {}).get("optionsChain", [])
        by_strike: dict[float, dict[str, float]] = {}
        for row in rows:
            strike = row.get("strike_price")
            opt_type = row.get("option_type")
            oi = row.get("oi")
            if strike is None or strike < 0 or opt_type not in ("CE", "PE") or oi is None:
                continue  # Skips the underlying/VIX rows (strike_price=-1, option_type="").
            entry = by_strike.setdefault(float(strike), {})
            entry["ce_oi" if opt_type == "CE" else "pe_oi"] = float(oi)
        return [
            (strike, values.get("ce_oi", 0.0), values.get("pe_oi", 0.0))
            for strike, values in sorted(by_strike.items())
        ]

    async def get_futures_quote(self, underlying: str) -> Optional[dict]:
        """Mirrors _quote()'s response-shape handling for the 'ltp' quotes
        endpoint for ltp/volume. LIVE-VERIFIED (2026-08-12, Phase 17B
        certification investigation): the 'ltp'/'quotes' v-dict has NO 'oi'
        key at all for futures symbols (confirmed on real NSE:NIFTY26AUGFUT
        data) -- volume IS present there and is used as before. OI is
        fetched via a second call to the 'depth' action (FYERS's market-depth
        endpoint, `client.depth()`), which DOES carry real, live OI
        (`oi`/`pdoi`/`oipercent`) for futures on this same account --
        confirmed live: oi=12645685 against the same symbol/session that had
        no oi field via 'ltp'. The depth call is best-effort: if it fails or
        returns no usable oi, "oi" is simply None (matching this method's
        prior behavior when oi was never obtainable at all), and the ltp-derived
        fields (symbol/ltp/volume) are still returned -- a depth-call failure
        must never turn a valid quote into None.
        Returns None on any missing/invalid ltp, exactly like
        get_vix()/get_quote() do for their own required fields."""
        symbol = _futures_symbol(underlying)
        data = await self._call("ltp", symbols=symbol)
        self._raise_if_auth_error(data)
        for row in data.get("d", []):
            if row.get("n") == symbol:
                v = row.get("v", {})
                lp = v.get("lp")
                if lp is None or lp <= 0:
                    return None
                result = {
                    "symbol": symbol, "ltp": float(lp),
                    "volume": v.get("volume"), "oi": None,
                }
                try:
                    depth_data = await self._call("depth", symbol=symbol, ohlcv_flag=1)
                    self._raise_if_auth_error(depth_data)
                    depth_row = (depth_data or {}).get("d", {}).get(symbol)
                    if depth_row and depth_row.get("oi") is not None:
                        result["oi"] = depth_row["oi"]
                except AuthenticationError:
                    raise  # A dead token is a real signal -- must not be swallowed.
                except Exception as e:  # noqa: BLE001
                    self._log.warning(
                        "get_futures_quote: depth() OI cross-check failed for %s "
                        "(quote itself is still valid, oi stays None): %s",
                        symbol, e,
                    )
                return result
        return None

    async def get_futures_quote_raw(self, underlying: str) -> dict:
        """Phase 17I.6.1 — raw pass-through of BOTH legs
        `get_futures_quote()` internally calls (the 'ltp' quote and the
        'depth' OI cross-check), mirroring `get_option_chain_raw()`'s and
        `get_depth()`'s own discipline: no extraction, no renaming, no
        discarding, and -- unlike `get_futures_quote()` -- the depth leg's
        failure is NOT swallowed here. `get_futures_quote()` treats a
        failed depth call as best-effort (OI is optional for a valid
        quote); this diagnostic method exists purely to inspect what FYERS
        actually sends, so both legs must succeed or the caller finds out.

        DIAGNOSTIC / SOURCE DISCOVERY ONLY. Must NOT be used to construct a
        `RawObservation` directly -- that remains `get_futures_quote()`'s
        job, through the existing certified capture path.

        Returns `{"symbol": ..., "ltp_response": <raw ltp dict>,
        "depth_response": <raw depth dict>}` -- both legs preserved
        untouched, including OI/depth-related and any unknown fields.
        """
        symbol = _futures_symbol(underlying)
        ltp_data = await self._call("ltp", symbols=symbol)
        self._raise_if_auth_error(ltp_data)
        self._raise_if_error(ltp_data, "ltp")
        depth_data = await self._call("depth", symbol=symbol, ohlcv_flag=1)
        self._raise_if_auth_error(depth_data)
        self._raise_if_error(depth_data, "depth")
        return {"symbol": symbol, "ltp_response": ltp_data, "depth_response": depth_data}

    async def get_depth(self, symbol: str) -> Optional[dict]:
        """Raw pass-through of the FYERS 'depth' action's per-symbol row.

        UNVERIFIED beyond `oi`/`pdoi`/`ltp` (see `get_futures_quote`'s
        docstring, which cross-checks exactly those three fields live,
        2026-08-12). This method does NOT rename, restructure, wrap, or
        invent a "bids"/"asks" shape -- it returns the depth row exactly
        as FYERS sent it. A caller needing Layer 0's MARKET_DEPTH payload
        shape (`taxonomy.REQUIRED_PAYLOAD_FIELDS[KIND_MARKET_DEPTH] =
        ("bids", "asks")`) MUST NOT assume this row already has those
        exact keys -- confirm the real field names against a live response
        first (see `scripts/discover_futures_depth_fields.py`) and map
        explicitly. Fabricating that shape from an unverified guess would
        be exactly the kind of invented field this codebase's Layer 0
        discipline forbids.

        Returns None if the symbol has no row in the response (mirrors
        every other broker read here for a missing/invalid result).
        """
        data = await self._call("depth", symbol=symbol, ohlcv_flag=1)
        self._raise_if_auth_error(data)
        row = (data or {}).get("d", {}).get(symbol)
        return dict(row) if row else None

    async def place_order(self, request: OrderRequest) -> OrderResult:
        # THE POSITION-SCHEMA GATE. `FYERS_POSITION_SCHEMA_VERIFIED` said of
        # itself: "This flag exists so that fact is a gate rather than a
        # comment." It was a comment. Nothing in production read it -- only
        # tests asserting it stayed False -- so the safeguard against
        # manufactured flatness protected nothing.
        #
        # Placed HERE, on the one call that can create a position, rather than
        # at construction (harmless, and a construction-only test legitimately
        # builds this broker) or on every call (which would block the EXIT of a
        # position and strand it, turning a guard into the hazard it exists to
        # prevent).
        if not self.position_schema_verified:
            raise UnverifiedPositionSchemaError(
                "refusing to place a REAL order: FYERS_POSITION_SCHEMA_VERIFIED "
                "is False, so get_open_positions() cannot be trusted to report "
                "an open position. A wrong field name reads every row as qty 0 "
                "and manufactures flatness -- this system would sell options it "
                "could never prove it had closed. Clear this by observing a real "
                "open position and confirming the payload field names, then set "
                "the flag; never by reasoning about it."
            )
        # C3 IDEMPOTENCY REQUIREMENT: the ExecutionEngine guarantees at-most-once
        # placement per client_order_id ONLY IF this id is round-trippable —
        # it is sent as `orderTag` here and matched back in get_order via the
        # `_cid_to_order_id` cache / tag-scan fallback below.
        #
        # Verified against the real fyers-apiv3 SDK's place_order() signature
        # (introspected directly, not guessed): combined `symbol` string,
        # `side` as int (1=Buy, -1=Sell), `type` as int (2=Market, 1=Limit),
        # `productType` string ("INTRADAY", not "MIS" — the SDK's own name).
        # `orderTag`: NOT in the SDK's documented param list; passed anyway
        # since it's a documented FYERS v3 REST field the SDK passes through
        # verbatim. UNVERIFIED whether it's echoed back in orderbook() entries
        # — cannot confirm without placing a real order (deliberately not
        # done here); flagged in the readiness report.
        data = await self._call(
            "place_order",
            symbol=request.contract.symbol,
            side=1 if request.side is Side.BUY else -1,
            qty=request.quantity,
            type=2 if request.limit_price is None else 1,
            limitPrice=request.limit_price or 0,
            productType="INTRADAY",
            validity="DAY",
            orderTag=request.client_order_id,  # <-- idempotency key.
        )
        self._raise_if_auth_error(data)
        result = self._map_order(request.client_order_id, data)
        if result.broker_order_id:
            self._cid_to_order_id[request.client_order_id] = result.broker_order_id
        return result

    async def get_order(self, client_order_id: str) -> OrderResult:
        """Look up an order by OUR client_order_id.

        The real SDK has no separate order-history-by-id method (confirmed
        by introspection: `fyersModel.FyersModel` defines no such method) —
        only `orderbook()` (today's full order book, no filter). So: if we
        already know the FYERS order id for this client_order_id (cached from
        a prior successful `place_order` in this process), filter locally by
        id. Otherwise — the exact scenario C3 exists for, e.g. a crash/
        timeout right after placing — filter by `orderTag` instead.

        ⚠ UNVERIFIED: whether `orderbook()` entries actually expose the
        `orderTag` we set at placement (and under what response field name)
        could not be confirmed without placing a real order, which this
        codebase deliberately does not do. Flagged in the readiness report.
        """
        order_id = self._cid_to_order_id.get(client_order_id)
        data = await self._call("orders")  # orderbook() — today's orders.
        self._raise_if_auth_error(data)
        for order in data.get("orderBook", []):
            matched = (order.get("id") == order_id if order_id
                      else order.get("orderTag") == client_order_id)
            if matched:
                self._cid_to_order_id[client_order_id] = order.get("id", "")
                return self._map_order(client_order_id, order)
        return OrderResult(client_order_id, OrderStatus.UNKNOWN,
                           message="not_found_in_todays_order_book")

    async def cancel_order(self, client_order_id: str) -> OrderResult:
        # FYERS's cancel takes its own order `id`, not our client tag.
        order_id = self._cid_to_order_id.get(client_order_id)
        if not order_id:
            lookup = await self.get_order(client_order_id)
            order_id = self._cid_to_order_id.get(client_order_id)
            if not order_id:
                return lookup  # Nothing to cancel; report what we found.
        data = await self._call("cancel_order", id=order_id)
        self._raise_if_auth_error(data)
        return self._map_order(client_order_id, data)

    async def get_open_positions(self) -> list[dict]:
        # Normalize to the shape recovery/reconciliation expects (C1):
        #   {symbol, side: "BUY"|"SELL", qty: >0, avg_price}.
        # Verified live: top-level "netPositions" list + "overall" summary
        # (confirmed with an empty position book). Per-row field names
        # (netQty/netAvg) are carried over from the prior implementation and
        # are UNVERIFIED — no live position existed to inspect actual row
        # contents without placing a real order.
        data = await self._call("positions")
        self._raise_if_auth_error(data)

        # A FAILED READ IS NOT AN EMPTY BOOK.
        #
        # This went straight to `data.get("netPositions", [])`. An error
        # response, a malformed body, or a shape change all yielded `[]`, and
        # `[]` means FLAT to every caller above. get_funds(), twenty lines
        # below, has always checked `s == "ok"` and returned None otherwise;
        # this method never checked anything.
        if str(data.get("s", "")).lower() != "ok":
            raise PositionReadError(
                f"FYERS positions response was not ok (s={data.get('s')!r}, "
                f"message={data.get('message')!r}) -- refusing to report a "
                f"failed read as a flat account")
        if "netPositions" not in data:
            # Live-verified as present even for an empty book (see the note
            # above). Absent means the shape changed, which is exactly when
            # guessing is most dangerous.
            raise PositionReadError(
                "FYERS positions response carried no 'netPositions' key -- the "
                "payload shape changed; refusing to infer flatness from a "
                "response this adapter no longer understands")

        normalized: list[dict] = []
        for p in data.get("netPositions", []):
            if "netQty" not in p:
                # `netQty` is the UNVERIFIED field name this module's own gate
                # is about: if it is wrong, every row reads as qty 0, every
                # position is filtered out, and the account looks EMPTY. That
                # is the documented "manufactures flatness" failure, and a
                # default of 0 is how it happens silently.
                raise PositionReadError(
                    f"position row has no 'netQty' field (keys={sorted(p)!r}) -- "
                    f"reading it as flat would manufacture flatness, which is "
                    f"the exact failure FYERS_POSITION_SCHEMA_VERIFIED names")
            net = int(p.get("netQty", 0))
            if net == 0:
                continue  # Flat legs are not open positions.
            normalized.append({
                "symbol": p.get("symbol"),
                "side": Side.BUY.value if net > 0 else Side.SELL.value,
                "qty": abs(net),
                "avg_price": p.get("netAvg", p.get("avgPrice", 0.0)),
            })
        return normalized

    async def get_funds(self) -> Optional[dict]:
        """Account funds snapshot for the Capital Management Engine.

        ✅ LIVE-CERTIFIED 2026-07-19 against a real FYERS account (see
        docs/CAPITAL_MANAGEMENT_ENGINE.md). The real "fund_limit" row
        titles, confirmed live (NOT the guessed "Clear Cash" this method
        used before certification -- the actual title is "Clear Balance"):

            id=1  "Total Balance"              -> account_equity
            id=2  "Utilized Amount"             -> used_margin
            id=3  "Clear Balance"                -> cash_balance
            id=4  "Realized Profit and Loss"
            id=5  "Collaterals"                  -> collateral
            id=6  "Fund Transfer"
            id=7  "Receivables"
            id=8  "Adhoc Limit"
            id=9  "Limit at start of the day"
            id=10 "Available Balance"            -> available_funds / available_margin

        `available_exposure`/`peak_margin` have no corresponding row in the
        real response and remain unmapped (None) rather than guessed.
        """
        data = await self._call("funds")
        try:
            self._raise_if_auth_error(data)
        except AuthenticationError:
            raise
        if str(data.get("s", "")).lower() != "ok":
            return None
        rows = data.get("fund_limit", [])
        by_title = {str(r.get("title", "")).strip(): r for r in rows if isinstance(r, dict)}

        def _amount(title: str) -> Optional[float]:
            row = by_title.get(title)
            if row is None:
                return None
            try:
                return float(row.get("equityAmount"))
            except (TypeError, ValueError):
                return None

        return {
            "account_equity": _amount("Total Balance"),
            "available_funds": _amount("Available Balance"),
            "available_margin": _amount("Available Balance"),
            "cash_balance": _amount("Clear Balance"),
            "used_margin": _amount("Utilized Amount"),
            "collateral": _amount("Collaterals"),
            # available_exposure/peak_margin: no corresponding row in the
            # real, live-verified response -- left unmapped (None) rather
            # than guessed.
        }

    async def get_order_margin(self, ce_contract: OptionContract,
                               pe_contract: OptionContract) -> Optional[dict]:
        """Broker-quoted margin required for one lot of this exact CE+PE
        straddle.

        ✅ LIVE-CERTIFIED 2026-07-19 against a real FYERS account (see
        docs/CAPITAL_MANAGEMENT_ENGINE.md, "span_margin Live Certification"
        section, for the full raw request/response evidence and
        docs/AUDIT_LOG.md Pass 8). Endpoint, auth, request schema, response
        schema, multi-leg hedging benefit, error codes, and repeatability
        were all independently verified with real API calls, not
        documentation or community reports.

            POST https://api.fyers.in/api/v2/span_margin
            Header: Authorization: "{app_id}:{access_token}"
            Body:   {"data": [{"symbol", "qty", "side" (1=buy/-1=sell),
                              "type" (2=market), "productType"
                              ("INTRADAY"), "limitPrice", "stopLoss"}, ...]}

        VERIFIED response shape (the figures are nested under "data" — this
        was WRONG in the pre-certification implementation, which read
        top-level "total"/"span" keys that do not exist; that bug is what
        this fix corrects):

            {"code": 200, "message": "", "s": "ok", "latency": "",
             "data": {"span": <float>, "expo": <float>, "total": <float>,
                     "benefit": <float>},
             "individual_info": {"<internal_id>": {"ltp_info", "span",
                                                    "expo", "total"}, ...}}

        VERIFIED error responses:
            invalid symbol      -> HTTP 400, {"s":"error","code":-310,
                                   "message":"Please provide valid symbols"}
            malformed payload   -> HTTP 400, {"s":"error","code":-50,
                                   "message":"Invalid input"}
            invalid/expired auth-> HTTP 401, {"s":"error","code":-17,
                                   "message":"Could not authenticate the user"}

        VERIFIED: `data.total` for a real CE+PE short straddle correctly
        reflects the exchange's SPAN hedging benefit — combined margin was
        barely above a single leg's margin (not additive), with `benefit`
        showing the exact SPAN credit applied. Two identical back-to-back
        calls returned byte-identical responses (deterministic).

        `verified` is still returned as False here — certification is a
        human, config-level decision (`bujji.capital.providers
        .CertifiedBrokerMarginProvider`, gated by
        `risk.margin_provider_certified: true`), never automatic inside the
        broker adapter itself, even after this live verification.
        """
        import asyncio as _asyncio
        import requests as _requests

        def _call_span_margin() -> dict:
            payload = {"data": [
                {
                    "symbol": ce_contract.symbol, "qty": ce_contract.lot_size,
                    "side": -1, "type": 2, "productType": "INTRADAY",
                    "limitPrice": 0, "stopLoss": 0,
                },
                {
                    "symbol": pe_contract.symbol, "qty": pe_contract.lot_size,
                    "side": -1, "type": 2, "productType": "INTRADAY",
                    "limitPrice": 0, "stopLoss": 0,
                },
            ]}
            headers = {"Authorization": f"{self._cfg.app_id}:{self._cfg.access_token}"}
            resp = _requests.post(
                "https://api.fyers.in/api/v2/span_margin",
                json=payload, headers=headers, timeout=15.0,
            )
            return resp.json()

        try:
            response = await _asyncio.to_thread(_call_span_margin)
        except Exception as exc:  # noqa: BLE001 - network/transport failure.
            self._log.warning("fyers_span_margin_call_failed", extra={"data": {"err": str(exc)}})
            return None

        if str(response.get("s", "")).lower() != "ok":
            self._log.warning("fyers_span_margin_error_response",
                              extra={"data": {"response": response}})
            return None
        try:
            total_for_both_legs = float(response["data"]["total"])
        except (KeyError, TypeError, ValueError):
            self._log.warning("fyers_span_margin_unexpected_shape",
                              extra={"data": {"response": response}})
            return None
        return {
            "margin_per_lot": total_for_both_legs,
            "verified": False,  # Certification promotion happens at the
                                 # provider tier, not here — see docstring.
            "source": "fyers_span_margin",
        }

    def _map_order(self, client_order_id: str, data: dict) -> OrderResult:
        status = _ORDER_STATUS_MAP.get(data.get("status"), OrderStatus.UNKNOWN)
        return OrderResult(
            client_order_id=client_order_id,
            status=status,
            broker_order_id=data.get("id"),
            filled_quantity=int(data.get("filledQty", 0)),
            average_price=data.get("tradedPrice"),
            message=data.get("message", ""),
            raw=data,
        )

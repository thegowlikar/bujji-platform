"""Phase 17A §11 / Phase 17A.5 — prepared, NOT-YET-EXECUTED direct-SDK F&O
certification script.

Purpose: prove, once and only once the operator has refreshed the FYERS access
token, exactly what futures/options data reality Bujji can receive — by calling
`bujji/broker/fyers.py` directly (the real fyers-apiv3 SDK path), bypassing the
MCP connector entirely, since the connector was found to corrupt derivative-
instrument symbols before they reach FYERS (see docs/PHASE_17A_DATA_REALITY_AUDIT.md
amendment).

This script is READ-ONLY. It places no orders, mutates no broker/trading state
(its only writes are its own JSON certification artifacts under
data_certification/), and does not attempt to acquire, refresh, or prompt for
a token/PIN itself beyond what `FyersBroker.connect()` already does when
refresh_token/secret/pin are present in the environment (its existing,
pre-built renewal path — this script adds no new auth mechanism). If
connect() cannot obtain a valid token, this script aborts immediately and
writes nothing.

DO NOT RUN THIS AUTOMATICALLY. Prepared for manual execution by the operator,
on the VPS, after their own explicit go-ahead, per Phase 17A / 17A.5's
"no implementation, no execution until authorized" constraint. See
docs/PHASE_17A5_CERTIFICATION_RUNBOOK.md for the full before/during/after
operator procedure this script is meant to be run under.

Usage (manual, on the VPS, after token refresh):
    cd /opt/bujji/app
    python -m scripts.verify_fo_access

Output: prints a JSON summary to stdout AND writes one certification artifact
per instrument to data_certification/, in the schema Phase 17A.5 specifies.
`validation_result` is always exactly one of CERTIFIED_AVAILABLE,
PARTIAL_CERTIFICATION, or NOT_CERTIFIED — never left ambiguous.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import sys
from pathlib import Path

# Real, currently-listed instrument confirmed to exist in FYERS's public
# symbol master (public.fyers.in/sym_details/NSE_FO.csv) as of the Phase 17A
# audit session — RE-CHECK this is still the live near-month contract before
# running; futures symbols roll over monthly.
FUTURES_SYMBOL = "NSE:NIFTY26AUGFUT"
UNDERLYING = "NIFTY"

# NOTE: there is deliberately no hardcoded OPTION_SYMBOL constant anymore.
# A 2026-08-12 investigation found the previous fixed strike (29350) had
# drifted ~5000 points out-of-the-money as spot moved after it was first
# chosen, producing a dead/illiquid contract (lp=0.05, zero bid/ask/volume)
# that read as a false PARTIAL_CERTIFICATION — not a FYERS data gap. The
# option leg below resolves a live near-ATM contract from the real spot
# price at run time instead, via the same InstrumentMaster.resolve_atm()
# path the production runtime itself uses (bujji/broker/instrument_master.py).
NIFTY_STRIKE_INTERVAL = 50
NIFTY_LOT_SIZE_FALLBACK = 75  # resolve_atm() prefers the live NFO row's own lot size.

CERT_DIR = Path(__file__).resolve().parent.parent / "data_certification"

ACCESS_METHOD = "direct_sdk_fyers_broker_py"  # NOT the MCP connector.

CERTIFIED = "CERTIFIED_AVAILABLE"
PARTIAL = "PARTIAL_CERTIFICATION"
NOT_CERTIFIED = "NOT_CERTIFIED"


def _now() -> datetime.datetime:
    # Deliberately real wall-clock — this script runs manually, once, so the
    # `Date.now()`-style prohibition that applies to orchestrated workflow
    # scripts does not apply here.
    return datetime.datetime.now(datetime.timezone.utc)


def _write_cert(name: str, cert: dict) -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    path = CERT_DIR / f"{name}.json"
    path.write_text(json.dumps(cert, indent=2, default=str))
    print(f"wrote {path}")


def _validate_candles(candles: list, now: datetime.datetime) -> dict:
    """Integrity check per Phase 17A.5 §3: no future timestamps, no
    duplicate timestamps, correct (ascending) ordering, no impossible OHLC
    (high/low bounds violated, non-positive prices). Returns a dict the
    caller folds into `limitations` and the certification decision — never
    silently assumed valid."""
    issues: list[str] = []
    if not candles:
        return {"ok": None, "issues": ["no candles to validate"]}

    timestamps = [c.timestamp for c in candles]
    future = [t for t in timestamps if t > now]
    if future:
        issues.append(f"{len(future)} candle(s) with a future timestamp (e.g. {future[0]}).")

    dupes = len(timestamps) - len(set(timestamps))
    if dupes:
        issues.append(f"{dupes} duplicate timestamp(s) found.")

    if timestamps != sorted(timestamps):
        issues.append("candles are not in ascending timestamp order.")

    bad_ohlc = 0
    for c in candles:
        if c.high < c.low or c.high < c.open or c.high < c.close \
           or c.low > c.open or c.low > c.close \
           or min(c.open, c.high, c.low, c.close) <= 0:
            bad_ohlc += 1
    if bad_ohlc:
        issues.append(f"{bad_ohlc} candle(s) with impossible OHLC (high<low, bounds violated, or non-positive price).")

    return {"ok": not issues, "issues": issues}


def _classify(*, symbol_match: bool | None, quote_status: str, historical_status: str,
              required_fields_present: bool, integrity: dict) -> tuple[str, list[str]]:
    """Three-state classification only — CERTIFIED_AVAILABLE,
    PARTIAL_CERTIFICATION, or NOT_CERTIFIED. No ambiguous/UNKNOWN result is
    ever returned, per Phase 17A.5 §Step 2 requirement."""
    reasons: list[str] = []

    if symbol_match is False:
        reasons.append("symbol_requested != symbol_returned — treated as a hard fail regardless of any other signal.")
        return NOT_CERTIFIED, reasons

    if quote_status == "ERROR" and historical_status == "ERROR":
        reasons.append("both quote and historical calls errored — no usable observation at all.")
        return NOT_CERTIFIED, reasons

    if quote_status not in ("OK",) and historical_status not in ("OK",):
        reasons.append(f"neither quote ({quote_status}) nor historical ({historical_status}) succeeded.")
        return NOT_CERTIFIED, reasons

    if integrity.get("ok") is False:
        reasons.append(f"candle integrity check failed: {integrity.get('issues')}")
        return NOT_CERTIFIED, reasons

    # At least one of quote/historical succeeded and integrity (if checked)
    # didn't hard-fail. Decide CERTIFIED vs PARTIAL on completeness.
    partial_reasons = []
    if quote_status != "OK":
        partial_reasons.append(f"quote_status={quote_status} (historical succeeded, live quote did not).")
    if historical_status != "OK":
        partial_reasons.append(f"historical_status={historical_status} (quote succeeded, historical did not).")
    if not required_fields_present:
        partial_reasons.append("one or more required fields (per §3 Market Fields) missing or null.")
    if integrity.get("ok") is None:
        partial_reasons.append("candle integrity could not be checked (no candles returned to validate).")

    if partial_reasons:
        return PARTIAL, partial_reasons

    return CERTIFIED, ["quote OK, historical OK, symbol match confirmed, integrity checks passed, required fields present."]


async def main() -> int:
    from bujji.broker.fyers import FyersBroker
    from bujji.core.config import AppConfig
    from bujji.broker.errors import AuthenticationError

    log = logging.getLogger("verify_fo_access")
    logging.basicConfig(level=logging.INFO)

    # AppConfig.load() is the app's real env-loading path (see
    # bujji/core/config.py:201-224): it overlays FYERS_APP_ID,
    # FYERS_ACCESS_TOKEN, FYERS_APP_SECRET, FYERS_REFRESH_TOKEN, FYERS_PIN,
    # FYERS_CREDENTIALS_FILE from the environment onto a BrokerConfig. Pass
    # the same config YAML path the production runtime normally uses (adjust
    # if this deployment's path differs) so `name: fyers` and any other
    # broker settings are picked up consistently with production.
    app_cfg = AppConfig.load("config/config.yaml")
    cfg = app_cfg.broker
    if cfg.name != "fyers":
        cfg.name = "fyers"
    broker = FyersBroker(cfg, log)

    # --- Step 1: token health --------------------------------------------
    # connect() itself validates the token (a "profile" call) and attempts
    # the built-in refresh_token-based renewal if refresh_token/secret/pin
    # are configured. This IS the token-health check — no separate call,
    # and no PIN/refresh is attempted beyond what connect() already does.
    try:
        await broker.connect()
        token_ok = True
        token_note = "connect() succeeded: access_token valid (or renewed via refresh_token)."
    except AuthenticationError as e:
        token_ok = False
        token_note = str(e)
    except Exception as e:  # noqa: BLE001
        token_ok = False
        token_note = f"unexpected error during connect(): {e}"

    if not token_ok:
        print(json.dumps({"1_token_health": {"ok": False, "note": token_note}}, indent=2))
        print(
            "\nABORT: token is not valid and could not be auto-renewed. This "
            "script will not attempt to obtain a token itself (no PIN prompt, "
            "no interactive login) and writes NO certification artifacts on "
            "an aborted run — a missing token must never be silently recorded "
            "as a data-availability finding. Operator must refresh the FYERS "
            "token before re-running.",
            file=sys.stderr,
        )
        return 1

    print(json.dumps({"1_token_health": {"ok": True, "note": token_note}}, indent=2))

    now = _now()
    timestamp = now.isoformat()

    # --- NIFTY Futures: quote + historical -----------------------------
    # Uses the raw `_call("ltp", ...)` action directly (rather than the
    # get_futures_quote() wrapper) SPECIFICALLY so the broker's echoed
    # symbol ("n" field) is visible for comparison against what was
    # requested. get_futures_quote() only returns a populated result when
    # its internal `row.get("n") == symbol` match succeeds — a silent
    # mismatch there degrades to an indistinguishable "no data", exactly
    # the failure mode that hid the MCP connector's symbol corruption in
    # the original Phase 17A audit. This script must not repeat that.
    fut_quote_status = "UNTESTED"
    fut_symbol_returned = None
    fut_oi_available = None
    fut_volume_available = None
    fut_symbol_match = None
    fut_limitations: list[str] = []
    try:
        raw_fut_quote = await broker._call("ltp", symbols=FUTURES_SYMBOL)
        row = next((r for r in raw_fut_quote.get("d", []) if r.get("n")), None)
        if row is None:
            fut_quote_status = "NO_DATA"
            fut_limitations.append(f"no usable row in ltp response: {raw_fut_quote}")
        else:
            fut_symbol_returned = row.get("n")
            fut_symbol_match = fut_symbol_returned == FUTURES_SYMBOL
            v = row.get("v", {})
            lp = v.get("lp")
            if not fut_symbol_match:
                fut_quote_status = "SYMBOL_MISMATCH"
                fut_limitations.append(
                    f"requested {FUTURES_SYMBOL}, broker echoed {fut_symbol_returned} — "
                    "symbol-translation corruption; NOT_CERTIFIED regardless of any other field."
                )
            elif lp is None or lp <= 0:
                fut_quote_status = "NO_DATA"
                fut_limitations.append(f"symbol matched but no valid lp in v-dict: {v}")
            else:
                fut_quote_status = "OK"
                # NOTE: `oi` is deliberately NOT read from this v-dict — the
                # `quotes`/`ltp` action never carries it for futures
                # (confirmed 2026-08-12). See the depth() cross-check below,
                # which sets the real fut_oi_available/fut_oi_value.
                fut_volume_available = v.get("volume") is not None
                if not fut_volume_available:
                    fut_limitations.append("volume field absent/null on futures quote — required field per §3 missing.")
    except Exception as e:  # noqa: BLE001
        fut_quote_status = "ERROR"
        fut_limitations.append(f"futures ltp call raised: {e}")

    # OI cross-check via the SDK's `depth` method (market depth / 5-level
    # book + OI), NOT via the `ltp`/`quotes` action above. Investigated
    # 2026-08-12: the `quotes` endpoint's v-dict has no `oi` key at all for
    # futures symbols (confirmed on real NIFTY26AUGFUT data), and the
    # `optionchain` endpoint's underlying/futures row (strike_price=-1) also
    # has no `oi` field — only a futures price (`fp`). `depth` is a genuine
    # third endpoint that DOES carry `oi`/`pdoi`/`oipercent` for futures on
    # this same account (confirmed live). It was never wired into
    # FyersBroker._call()'s action tables, so this reaches the SDK client
    # directly — same read-only, no-order-capability guarantee as every
    # other call in this script, just via `client.depth()` instead of
    # `broker._call()`.
    fut_oi_source = "depth_endpoint"
    if fut_quote_status == "OK":
        try:
            client = broker._get_client()
            depth_raw = await asyncio.to_thread(
                client.depth, {"symbol": FUTURES_SYMBOL, "ohlcv_flag": 1}
            )
            depth_row = (depth_raw or {}).get("d", {}).get(FUTURES_SYMBOL)
            if depth_row and depth_row.get("oi") is not None:
                fut_oi_available = True
                fut_oi_value = depth_row.get("oi")
            else:
                fut_oi_available = False
                fut_oi_value = None
                fut_limitations.append(
                    f"depth() call succeeded but returned no usable 'oi' field: {depth_raw}"
                )
        except Exception as e:  # noqa: BLE001
            fut_oi_available = False
            fut_oi_value = None
            fut_limitations.append(f"depth() OI cross-check raised: {e}")
    else:
        fut_oi_value = None

    fut_hist_status = "UNTESTED"
    fut_candles: list = []
    try:
        # No get_futures_candles() exists yet in bujji/broker/fyers.py.
        # Probe the same low-level "historical" action get_recent_candles()/
        # get_option_candles() use, via the private _call(), mirroring their
        # exact action name/param shape (fyers.py:282-364) rather than
        # guessing a different endpoint. Requests a 30-day window so §3's
        # "multiple dates, current + an older date" check has something to
        # evaluate, not just a single day.
        today = datetime.date.today()
        raw = await broker._call(
            "historical",
            symbol=FUTURES_SYMBOL,
            resolution="D",
            date_format="1",
            range_from=(today - datetime.timedelta(days=30)).isoformat(),
            range_to=today.isoformat(),
            cont_flag="1",
        )
        if raw.get("s") == "ok" and raw.get("candles"):
            fut_hist_status = "OK"
            from bujji.core.models import Candle
            from bujji.core.clock import epoch_to_ist as _epoch_to_ist
            fut_candles = [
                Candle(timestamp=_epoch_to_ist(r[0]), open=r[1], high=r[2], low=r[3], close=r[4],
                       volume=r[5] if len(r) > 5 else 0.0)
                for r in raw["candles"]
            ]
            if len(fut_candles) < 2:
                fut_limitations.append(f"only {len(fut_candles)} historical candle(s) returned over a 30-day window — cannot confirm multi-date coverage per §3.")
        elif raw.get("s") == "no_data":
            fut_hist_status = "NO_DATA"
        else:
            fut_hist_status = "ERROR"
            fut_limitations.append(f"unexpected historical response: {raw}")
    except Exception as e:  # noqa: BLE001
        fut_hist_status = "ERROR"
        fut_limitations.append(f"historical futures probe raised: {e}")

    if fut_oi_available:
        fut_limitations.append(
            f"oi={fut_oi_value} obtained via depth() (source={fut_oi_source}); "
            "not available via the quotes/ltp or optionchain actions."
        )

    fut_integrity = _validate_candles(fut_candles, now)
    if fut_integrity.get("issues"):
        fut_limitations.extend(fut_integrity["issues"])
    fut_result, fut_class_reasons = _classify(
        symbol_match=fut_symbol_match, quote_status=fut_quote_status, historical_status=fut_hist_status,
        required_fields_present=bool(fut_oi_available and fut_volume_available),
        integrity=fut_integrity,
    )
    fut_limitations.extend(fut_class_reasons)

    futures_cert = {
        "timestamp": timestamp,
        "broker": "fyers",
        "access_method": ACCESS_METHOD,
        "instrument": "NIFTY_FUTURES",
        "symbol_requested": FUTURES_SYMBOL,
        "symbol_returned": fut_symbol_returned,
        "quote_status": fut_quote_status,
        "historical_status": fut_hist_status,
        "volume_available": fut_volume_available,
        "oi_available": fut_oi_available,
        "timestamp_valid": fut_integrity.get("ok"),
        "validation_result": fut_result,
        "limitations": fut_limitations or ["none observed"],
    }
    _write_cert("fyers_nifty_future_certification", futures_cert)

    # --- Options: resolve a LIVE near-ATM contract, then identity/quote/historical
    opt_quote_status = "UNTESTED"
    opt_symbol_returned = None
    opt_symbol_match = None
    opt_bid_available = None
    opt_ask_available = None
    opt_limitations: list[str] = []
    resolved_contract = None
    OPTION_SYMBOL = None
    try:
        spot_symbol = "NSE:NIFTY50-INDEX"
        raw_spot = await broker._call("ltp", symbols=spot_symbol)
        spot_row = next(
            (r for r in raw_spot.get("d", []) if r.get("n") == spot_symbol), None
        )
        spot_price = (spot_row or {}).get("v", {}).get("lp")
        if spot_price is None or spot_price <= 0:
            opt_quote_status = "ERROR"
            opt_limitations.append(
                f"could not resolve a live NIFTY spot price to pick an ATM "
                f"strike from — spot ltp response: {raw_spot}"
            )
        else:
            from bujji.core.enums import OptionType
            resolved_contract = await broker._instrument_master().resolve_atm(
                UNDERLYING, spot_price, OptionType.CE,
                NIFTY_STRIKE_INTERVAL, NIFTY_LOT_SIZE_FALLBACK,
            )
            OPTION_SYMBOL = resolved_contract.symbol
            opt_limitations.append(
                f"strike resolved live from spot={spot_price} (was a hardcoded, "
                f"since-drifted strike in the prior version of this script) — "
                f"resolved contract: {OPTION_SYMBOL}, expiry={resolved_contract.expiry}."
            )
    except Exception as e:  # noqa: BLE001
        opt_quote_status = "ERROR"
        opt_limitations.append(f"ATM strike resolution raised: {e}")

    try:
        if OPTION_SYMBOL is None:
            raise RuntimeError("no OPTION_SYMBOL resolved — see limitations above.")
        raw_quote = await broker._call("ltp", symbols=OPTION_SYMBOL)
        row = next((r for r in raw_quote.get("d", []) if r.get("n")), None)
        if row is None:
            opt_quote_status = "NO_DATA"
            opt_limitations.append(f"no usable row in quotes response: {raw_quote}")
        else:
            opt_symbol_returned = row.get("n")
            opt_symbol_match = opt_symbol_returned == OPTION_SYMBOL
            v = row.get("v", {})
            if not opt_symbol_match:
                opt_quote_status = "SYMBOL_MISMATCH"
                opt_limitations.append(
                    f"requested {OPTION_SYMBOL}, broker echoed {opt_symbol_returned} — "
                    "symbol-translation corruption; NOT_CERTIFIED regardless of any other field."
                )
            else:
                opt_quote_status = "OK"
                opt_bid_available = v.get("bid") is not None and v.get("bid", 0) > 0
                opt_ask_available = v.get("ask") is not None and v.get("ask", 0) > 0
                if not opt_bid_available:
                    opt_limitations.append("bid field absent/zero on option quote — required field per §3 missing.")
                if not opt_ask_available:
                    opt_limitations.append("ask field absent/zero on option quote — required field per §3 missing.")
    except Exception as e:  # noqa: BLE001
        opt_quote_status = "ERROR"
        opt_limitations.append(f"option quote raised: {e}")

    opt_hist_status = "UNTESTED"
    opt_candles: list = []
    opt_volume_available = None
    try:
        if resolved_contract is None:
            raise RuntimeError("no ATM contract was resolved — see limitations above.")
        opt_candles = await broker.get_option_candles(resolved_contract, minutes=1, count=75)
        opt_hist_status = "OK" if opt_candles else "NO_DATA"
        if opt_candles:
            opt_volume_available = any(c.volume for c in opt_candles)
            if not opt_volume_available:
                opt_limitations.append("all returned option candles have zero volume — required field per §3 effectively missing.")
    except Exception as e:  # noqa: BLE001
        opt_hist_status = "ERROR"
        opt_limitations.append(f"get_option_candles() raised: {e}")

    opt_oi_available = None
    try:
        chain = await broker.get_option_chain(UNDERLYING, spot=0.0, strike_count=1)
        opt_oi_available = bool(chain)
        if not opt_oi_available:
            opt_limitations.append("get_option_chain() OI cross-check returned empty/None.")
    except Exception as e:  # noqa: BLE001
        opt_oi_available = False
        opt_limitations.append(f"get_option_chain() OI cross-check raised: {e}")

    opt_integrity = _validate_candles(opt_candles, now)
    if opt_integrity.get("issues"):
        opt_limitations.extend(opt_integrity["issues"])
    opt_result, opt_class_reasons = _classify(
        symbol_match=opt_symbol_match, quote_status=opt_quote_status, historical_status=opt_hist_status,
        required_fields_present=bool(opt_bid_available and opt_ask_available and opt_volume_available and opt_oi_available),
        integrity=opt_integrity,
    )
    opt_limitations.extend(opt_class_reasons)

    options_cert = {
        "timestamp": timestamp,
        "broker": "fyers",
        "access_method": ACCESS_METHOD,
        "instrument": "NIFTY_OPTION_CE",
        "symbol_requested": OPTION_SYMBOL,
        "symbol_returned": opt_symbol_returned,
        "quote_status": opt_quote_status,
        "historical_status": opt_hist_status,
        "volume_available": opt_volume_available,
        "oi_available": opt_oi_available,
        "timestamp_valid": opt_integrity.get("ok"),
        "validation_result": opt_result,
        "limitations": opt_limitations or ["none observed"],
    }
    _write_cert("fyers_option_chain_certification", options_cert)

    # --- NIFTY Spot: control case — should already be AVAILABLE, proves
    #     the harness itself works even if futures/options are blocked. ---
    spot_limitations: list[str] = []
    spot_candles: list = []
    try:
        spot_candles = await broker.get_recent_candles(UNDERLYING, minutes=1, count=5)
        spot_hist_status = "OK" if spot_candles else "NO_DATA"
    except Exception as e:  # noqa: BLE001
        spot_hist_status = "ERROR"
        spot_limitations.append(str(e))

    spot_integrity = _validate_candles(spot_candles, now)
    if spot_integrity.get("issues"):
        spot_limitations.extend(spot_integrity["issues"])
    spot_result, spot_class_reasons = _classify(
        symbol_match=True if spot_hist_status == "OK" else None,  # spot symbol is hardcoded/constant, not echoed per-call
        quote_status="OK" if spot_hist_status == "OK" else spot_hist_status,  # no separate spot quote call in this script
        historical_status=spot_hist_status,
        required_fields_present=True,  # spot has no volume/OI requirement (index; see §3 Spot fields = OHLC + timestamp only)
        integrity=spot_integrity,
    )
    spot_limitations.extend(spot_class_reasons)

    spot_cert = {
        "timestamp": timestamp,
        "broker": "fyers",
        "access_method": ACCESS_METHOD,
        "instrument": "NIFTY_SPOT",
        "symbol_requested": "NSE:NIFTY50-INDEX",
        "symbol_returned": "NSE:NIFTY50-INDEX" if spot_hist_status == "OK" else None,
        "quote_status": "NOT_TESTED_SEPARATELY",
        "historical_status": spot_hist_status,
        "volume_available": False,  # index candles: known volume=0 per fyers.py's own documented finding
        "oi_available": False,  # spot has no OI — not applicable, not "missing"
        "timestamp_valid": spot_integrity.get("ok"),
        "validation_result": spot_result,
        "limitations": spot_limitations or ["none observed"],
    }
    _write_cert("fyers_nifty_spot_certification", spot_cert)

    summary = {
        "1_token_health": {"ok": token_ok, "note": token_note},
        "futures": futures_cert,
        "options": options_cert,
        "spot_control": spot_cert,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

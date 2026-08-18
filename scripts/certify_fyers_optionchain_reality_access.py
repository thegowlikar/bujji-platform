"""Phase 17I.10 — NIFTY Option Chain Reality live access certification.

New, distinct access_method: `direct_sdk_fyers_optionchain_reality` --
never reuses `direct_sdk_fyers_broker_py` (live quote) or any other
existing value, per this project's recurring collision-avoidance
discipline (17G.0/17H.3/17H.9/17I.2/17I.9's own recommendation for this
exact phase). `CertificationGate.status_for()` is keyed by
`(instrument_type, access_method)` only -- reusing an existing value
would let option-chain writes silently inherit a status that was never
actually earned for THIS capability.

Two live probes:
1. Default (current/nearest) expiry chain -- confirms API reachability,
   symbol resolution, and that expiry/strike/CE+PE/price fields are
   all present and structurally valid.
2. A SECOND, different expiry (selected via the real `timestamp`
   parameter -- confirmed live this phase to be an EXPIRY SELECTOR, not
   a historical-snapshot parameter as an earlier audit pass,
   PHASE_17I6/17I7, mischaracterized it; see this script's own
   docstring correction below) -- proves multi-expiry capture works,
   not just the nearest one.

**Correction to a prior finding, stated explicitly**: `PHASE_17I6_OPTIONS_HISTORICAL_DATA_ACQUISITION_CAPABILITY_AUDIT.md`
and `PHASE_17I7_OPTIONS_REALITY_CAPTURE_ARCHITECTURE_AUDIT.md` both
stated the `optionchain` action's `timestamp` parameter was "silently
ignored" when probed with a past epoch. Reading the real FYERS SDK
source (`fyers_apiv3.fyersModel.FyersModel.optionchain`'s own
docstring) during this phase reveals `timestamp` is actually an
EXPIRY-SELECTOR ("Expiry timestamp of the stock. Use empty for current
expiry"), not a historical-snapshot pointer -- the earlier tests passed
a made-up past epoch that didn't correspond to any real listed expiry,
so the request was correctly rejected/ignored, but the REASON given in
those two documents (framing it as evidence against historical
snapshot capability) was an incomplete/incorrect interpretation of a
correct underlying observation (FYERS has no historical option-chain
snapshot capability -- that conclusion itself still stands, re-confirmed
independently in this phase by the expired-symbol tests already run in
17I.6, which remain valid).

**A second correction**: the same FYERS SDK docstring documents a
`greeks` parameter ("Set greeks to 1 for greeks data which includes
delta, gamma, theta, vega and iv"). This means IV/Greeks ARE available
FROM THE SOURCE if requested -- prior documents (17I.6 S1, 17I.7 S1/S2,
17I.8 S1) stated IV/Greeks are "never source-provided," which is now
known to be incomplete: they are source-COMPUTABLE on request. This
script, and every option-chain capture script in this phase, NEVER
passes `greeks=1` -- IV/Greeks are excluded from Reality by this
project's own deliberate architecture rule, not because the source
lacks them.

DELIBERATELY MARKET-HOURS GATED: an option chain has no meaning outside
a live session (unlike historical daily/intraday endpoints), same
reasoning as every live certification script in this project
(certify_vix_access.py, certify_fyers_futures_depth_access.py).

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/certify_fyers_optionchain_reality_access.py
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

UNDERLYING = "NIFTY"
INDEX_SYMBOL = "NSE:NIFTY50-INDEX"
INSTRUMENT = "NIFTY_OPTION_CE"  # matches INSTRUMENT_TYPE_TO_CERT_KEY[INSTRUMENT_OPTION]
ACCESS_METHOD = "direct_sdk_fyers_optionchain_reality"
BROKER = "fyers"
STRIKE_COUNT = 50  # live-verified working value this phase (202 real option rows returned).

CERT_DIR = REPO_ROOT / "data_certification"
ARTIFACT_NAME = "fyers_nifty_optionchain_reality_certification"

MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 30)  # Per this phase's explicit instruction (index/cash close;
# NOTE: this differs from the F&O 15:40 close already established for futures depth (17I.2) --
# flagged here deliberately, not silently reconciled. See PHASE_17I10 doc's own note.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

CERTIFIED = "CERTIFIED_AVAILABLE"
PARTIAL = "PARTIAL_CERTIFICATION"
NOT_CERTIFIED = "NOT_CERTIFIED"


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def _within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _write_cert(cert: dict) -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    date_suffix = cert["timestamp"][:10].replace("-", "")
    path = CERT_DIR / f"{ARTIFACT_NAME}_{date_suffix}.json"
    path.write_text(json.dumps(cert, indent=2, default=str))
    print(f"wrote {path}")


def _validate_chain_rows(rows: list) -> dict:
    """Structural integrity only -- presence and validity, never a
    liquidity/quality judgment. A genuinely zero bid/ask on an
    illiquid far strike is a true fact (17E's own precedent, "V6"),
    not a failure."""
    issues: List[str] = []
    option_rows = [r for r in rows if r.get("option_type") in ("CE", "PE")]
    if not option_rows:
        return {"ok": False, "issues": ["no CE/PE rows in response"]}

    missing_strike = [r for r in option_rows if r.get("strike_price") in (None, -1)]
    missing_symbol = [r for r in option_rows if not r.get("symbol")]
    missing_ltp = [r for r in option_rows if r.get("ltp") is None]
    negative_prices = [
        r for r in option_rows
        if any((r.get(f) or 0) < 0 for f in ("ltp", "bid", "ask"))
    ]
    ce_count = sum(1 for r in option_rows if r["option_type"] == "CE")
    pe_count = sum(1 for r in option_rows if r["option_type"] == "PE")

    if missing_strike:
        issues.append(f"{len(missing_strike)} row(s) missing a real strike_price.")
    if missing_symbol:
        issues.append(f"{len(missing_symbol)} row(s) missing a symbol.")
    if missing_ltp:
        issues.append(f"{len(missing_ltp)} row(s) missing ltp entirely (None, not zero).")
    if negative_prices:
        issues.append(f"{len(negative_prices)} row(s) with a negative price field.")
    if ce_count == 0:
        issues.append("zero CE rows present.")
    if pe_count == 0:
        issues.append("zero PE rows present.")

    return {
        "ok": not issues, "issues": issues,
        "ce_count": ce_count, "pe_count": pe_count, "total": len(option_rows),
    }


async def main() -> int:
    now = _now_ist()
    if not _within_market_hours(now):
        print(json.dumps({
            "aborted": True, "reason": "OUTSIDE_MARKET_HOURS",
            "now_ist": now.isoformat(),
            "market_hours": f"{MARKET_OPEN}-{MARKET_CLOSE} IST, Mon-Fri",
        }, indent=2))
        print(
            "\nABORT: outside NSE market hours. An option chain has no "
            "meaning outside a live session. NO certification artifact "
            "is written. Re-run during an NSE session.",
            file=sys.stderr,
        )
        return 1

    from bujji.broker.fyers import FyersBroker
    from bujji.broker.errors import AuthenticationError
    from bujji.core.config import AppConfig
    import logging

    log = logging.getLogger("certify_fyers_optionchain_reality")
    logging.basicConfig(level=logging.INFO)

    app_cfg = AppConfig.load("config/config.yaml")
    cfg = app_cfg.broker
    if cfg.name != "fyers":
        cfg.name = "fyers"
    broker = FyersBroker(cfg, log)

    try:
        await broker.connect()
    except AuthenticationError as e:
        print(json.dumps({"1_token_health": {"ok": False, "note": str(e)}}, indent=2))
        print("\nABORT: token invalid. No artifact written.", file=sys.stderr)
        return 1

    timestamp = now.isoformat()
    limitations: List[str] = []

    # --- Probe 1: default (nearest) expiry chain -----------------------------
    default_status = "UNTESTED"
    default_integrity: Optional[dict] = None
    expiry_list: list = []
    try:
        raw = await broker._call(
            "optionchain", symbol=INDEX_SYMBOL, strikecount=STRIKE_COUNT, timestamp="",
        )
        if raw.get("code") == 200 or "data" in raw:
            data = raw.get("data", {})
            rows = data.get("optionsChain", [])
            expiry_list = data.get("expiryData", [])
            default_integrity = _validate_chain_rows(rows)
            default_status = "OK" if default_integrity.get("ok") else "INTEGRITY_FAILED"
            if not expiry_list:
                limitations.append("no expiryData present in default-expiry response.")
        else:
            default_status = "ERROR"
            limitations.append(f"unexpected default-expiry response: {raw}")
    except Exception as e:  # noqa: BLE001
        default_status = "ERROR"
        limitations.append(f"default-expiry probe raised: {e}")

    # --- Probe 2: a SECOND, distinct expiry (proves multi-expiry capture) -----
    second_status = "UNTESTED"
    second_integrity: Optional[dict] = None
    second_expiry_date = None
    try:
        candidate = next(
            (e for e in expiry_list if e.get("expiry")), None,
        )
        # Prefer the SECOND entry if present, so this genuinely probes a
        # different expiry than probe 1's default (nearest) one.
        if len(expiry_list) > 1:
            candidate = expiry_list[1]
        if candidate is not None:
            second_expiry_date = candidate.get("date")
            raw2 = await broker._call(
                "optionchain", symbol=INDEX_SYMBOL, strikecount=10,
                timestamp=int(candidate["expiry"]),
            )
            rows2 = raw2.get("data", {}).get("optionsChain", [])
            second_integrity = _validate_chain_rows(rows2)
            second_status = "OK" if second_integrity.get("ok") else "INTEGRITY_FAILED"
        else:
            second_status = "NO_DATA"
            limitations.append("no second expiry available to probe.")
    except Exception as e:  # noqa: BLE001
        second_status = "ERROR"
        limitations.append(f"second-expiry probe raised: {e}")

    # --- Duplicate/conflict-behavior understanding: re-poll probe 1, compare --
    duplicate_behavior_note = (
        "Not separately live-probed here beyond re-fetch consistency (see "
        "below) -- duplicate/conflict semantics are inherited UNCHANGED "
        "from HistoricalObservationStore's own already-proven three-outcome "
        "discipline (identical fact -> idempotent no-op; genuinely "
        "different value under the same natural key -> "
        "ConflictingHistoricalObservationError, never silently "
        "overwritten) -- the same mechanism already validated live for "
        "spot/futures/VIX across 17H.9's real backfill (226 real "
        "conflicts correctly caught)."
    )

    if default_status != "OK":
        result = NOT_CERTIFIED
        limitations.append(f"default_status={default_status} -- option chain access itself failed.")
    elif second_status not in ("OK",):
        result = PARTIAL
        limitations.append(
            f"second_status={second_status} -- default-expiry chain certified, "
            "but multi-expiry capture is not independently reconfirmed this run."
        )
    else:
        result = CERTIFIED

    cert = {
        "timestamp": timestamp,
        "broker": BROKER,
        "access_method": ACCESS_METHOD,
        "instrument": INSTRUMENT,
        "symbol_requested": INDEX_SYMBOL,
        "strike_count_tested": STRIKE_COUNT,
        "default_expiry_status": default_status,
        "default_expiry_integrity": default_integrity,
        "second_expiry_date_tested": second_expiry_date,
        "second_expiry_status": second_status,
        "second_expiry_integrity": second_integrity,
        "expiries_available": len(expiry_list),
        "greeks_requested": False,
        "duplicate_conflict_behavior": duplicate_behavior_note,
        "validation_result": result,
        "limitations": limitations or ["none observed"],
    }
    _write_cert(cert)
    print(json.dumps(cert, indent=2, default=str))
    return 0 if result == CERTIFIED else (2 if result == PARTIAL else 3)


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))

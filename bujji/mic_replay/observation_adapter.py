"""Historical Observation Adapter — BUJJI Options OS v3, Engineering
Series 61 (genuine intraday OHLC transport added by Series 67).

Converts a Series 59 `HistoricalSessionRecord` into the plain,
JSON-serializable observation payload shape MIC v2's own
`Candle`/`OptionChainLevel` dataclasses expect
(`mic_v2.models.evidence`, a completely separate repo/venv --
`/opt/bujji-mic-v2/`).

This module NEVER imports `mic_v2`. MIC v2 lives in a fully separate
Python process (Series 54's own disclosed process-boundary finding,
restated and respected here): this module only produces plain dicts
matching MIC v2's own field names exactly. `replay_driver.py` is the
only place those dicts cross into MIC v2's real process, via
subprocess -- never an in-process import.

Series 67: `HistoricalSessionRecord.open/high/low/close` (Series 67's
own schema extension) carry a genuine intraday range for the
underlying, sourced separately from `spot` (a different provider than
Bhavcopy, which has no underlying-index OHLC column). When all four
are present, `build_candle_payload()` uses them verbatim. When any one
is missing, this function falls back to the original Series 61
degenerate representation (`open=high=low=close=spot`) rather than
inventing the missing value(s) -- a session is never a mix of one real
OHLC field and three guessed ones.

Series 68: `build_option_chain_payload()` now reads real OI/change-in-OI
from `HistoricalSessionRecord.option_chain_liquidity` (Series 64/65)
into `ce_oi`/`pe_oi`, and `build_observation_payload()` now includes
`vix`/`vix_prev_close` from `record.vix` (Series 64/65) when present --
both were previously discarded even though the underlying record
already carried real values (a genuine bujji-side transport gap, fixed
here). Bid/ask remain `None` -- `OptionLiquiditySnapshot` never carries
them (no real source exists; see Series 64/Data Acquisition Sprint C).

Disclosed, NOT fixable within this repository's own transport code:
Series 68's own investigation found that MIC v2's own frozen
replay-mode entrypoints (`run_replay`, `run_replay_with_contract`,
`run_replay_with_qualifications`, every stage's own `runner.py`, 17
call sites total) call `mic_v2.observation_builder.build_observation(candle,
candle_history)` with exactly two positional arguments -- never
`option_chain=` or `vix_level=`/`vix_prev_close=`, even though
`build_observation()` and `ObservationInput` both already support
them. So even with this fix, OI/VIX still never reach
`ObservationInput` in a replay run -- the final break is inside MIC
v2's own frozen runner call sites, out of this sprint's authorized
scope (`No MIC reasoning changes`). See
`docs/OI_VIX_SHADOW_INVESTIGATION.md` for the full evidence-flow map.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..replay.validator import HistoricalSessionRecord


def build_candle_payload(record: HistoricalSessionRecord) -> Optional[Dict]:
    """Build the plain-dict payload for MIC v2's `Candle`. Returns
    `None` when `record.spot` is absent -- never a fabricated price.

    Uses `record.open/high/low/close` (Series 67) verbatim when all
    four are present; otherwise falls back to the original degenerate
    `open=high=low=close=spot` representation -- never a partial mix
    of real and fabricated OHLC values.
    """
    if record.spot is None:
        return None

    has_real_ohlc = None not in (record.open, record.high, record.low, record.close)
    if has_real_ohlc:
        open_, high, low, close = record.open, record.high, record.low, record.close
    else:
        open_ = high = low = close = record.spot

    return {
        "timestamp": record.timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 0.0,
    }


def build_option_chain_payload(record: HistoricalSessionRecord) -> List[Dict]:
    """Build the plain-dict payload for MIC v2's `OptionChainLevel`,
    one per distinct strike present in the record. Strike order is
    preserved as encountered (ascending, since Data Acquisition Sprint
    A's ingestion adapter emits bhavcopy rows in the provider's own
    order) -- this function never reorders or deduplicates beyond
    grouping CE/PE onto the same strike level, and never invents a
    strike that was not in the source record.

    `ce_oi`/`pe_oi` (Series 68) are populated from
    `record.option_chain_liquidity` when a matching
    `(strike, option_type)` snapshot exists and its `open_interest` is
    not `None`; otherwise they remain `0` (MIC v2's own
    `OptionChainLevel` default -- never a fabricated non-zero guess,
    and indistinguishable at the payload layer from "no liquidity data
    available," exactly the same honesty tradeoff `OptionLiquiditySnapshot`
    itself already makes). Bid/ask remain `None` -- never sourced.
    """
    liquidity_by_key = {(l.strike, l.option_type): l for l in record.option_chain_liquidity}

    levels: Dict[float, Dict] = {}
    for strike, option_type, expiry, symbol in record.option_chain_entries:
        level = levels.setdefault(
            strike,
            {"strike": int(strike), "ce_oi": 0, "pe_oi": 0, "ce_bid": None, "ce_ask": None, "pe_bid": None, "pe_ask": None},
        )
        snapshot = liquidity_by_key.get((int(strike), option_type))
        if snapshot is not None and snapshot.open_interest is not None:
            if option_type == "CE":
                level["ce_oi"] = snapshot.open_interest
            elif option_type == "PE":
                level["pe_oi"] = snapshot.open_interest
        del expiry, symbol
    return [levels[strike] for strike in sorted(levels)]


def build_observation_payload(record: HistoricalSessionRecord) -> Optional[Dict]:
    """Build the complete payload for one replay session: the current
    candle, its option chain, and (Series 68) VIX, ready to be handed
    to `replay_driver.py`'s subprocess bridge. Returns `None` when no
    candle can be built (missing spot) -- the caller must record that
    as a gap, never silently skip it.

    `vix`/`vix_prev_close` are included only when `record.vix` is not
    `None` -- omitted entirely otherwise, never a fabricated `0.0`.
    """
    candle = build_candle_payload(record)
    if candle is None:
        return None
    payload = {
        "session_id": record.session_id,
        "trading_date": record.trading_date,
        "candle": candle,
        "option_chain": build_option_chain_payload(record),
    }
    if record.vix is not None:
        payload["vix"] = record.vix
    return payload

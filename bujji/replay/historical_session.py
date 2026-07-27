"""Historical Session Record — BUJJI Options OS v3, Engineering Series
64 (evolving Series 59's original schema).

`HistoricalSessionRecord` is the one input shape the entire historical
replay pipeline validates and converts into a `ReplayScenario`
(Series 46). This module is the schema's canonical home as of Series
64; `bujji.replay.validator` re-exports it unchanged for every
existing importer (Data Acquisition Sprint A's
`option_chain_ingestion.py`, `corpus_builder.py`, and every existing
test), so no caller needs to change.

Series 64 extends the schema to *transport* option open interest,
bid/ask, India VIX, and session metadata -- evidence Qualification
Data Enrichment Sprint B proved sourceable but which the v1 schema had
no field for. Every new field is optional, defaults to "not supplied"
(`None` / empty tuple -- never a guessed value), and is additive only:
every v1 field keeps its exact name, position, type, and default, so
every existing v1-shaped construction (by keyword, as every caller in
this codebase already does) continues to work byte-for-byte
unchanged. No new field is read by any validator rule, corpus-builder
transformation, or downstream consumer introduced by this sprint --
transport only, per this sprint's own critical principle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from .schema_version import CURRENT_SCHEMA_VERSION


@dataclass(frozen=True)
class OptionLiquiditySnapshot:
    """One strike/expiry/option-type's open-interest and bid/ask
    evidence for one session -- transport only, never consumed by any
    decision-making logic in this sprint. Keyed identically to
    `HistoricalSessionRecord.option_chain_entries`'s own
    `(strike, option_type, expiry, contract_symbol)` identity, so a
    liquidity snapshot can always be matched back to its contract
    identity without ambiguity.

    Every field left `None` means "not supplied by the source," never
    "zero" -- a real zero open-interest reading and a genuinely absent
    reading must remain distinguishable.
    """

    strike: int
    option_type: str
    expiry: str
    contract_symbol: str
    open_interest: Optional[int] = None
    change_in_open_interest: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None


@dataclass(frozen=True)
class HistoricalSessionRecord:
    """One raw historical session. Immutable; never mutated by the
    validator or the builder.

    Fields above the `# --- Series 64 ---` marker are Series 59's
    original schema, unchanged in name, order, type, and default.
    Fields below it are new in Series 64: optional, transport-only,
    never required, never consumed.
    """

    session_id: str
    trading_date: str
    timestamp: str
    market_context: Optional[str] = None
    market_opinion: Optional[str] = None
    context_stability: Optional[str] = None
    calibration: Optional[str] = None
    governance: Optional[str] = None
    lifecycle: Optional[str] = None
    contract: Optional[str] = None
    spot: Optional[float] = None
    spot_as_of: Optional[str] = None
    option_chain_entries: Tuple[Tuple[int, str, str, str], ...] = ()
    option_chain_expiries: Tuple[str, ...] = ()
    option_chain_as_of: Optional[str] = None
    is_holiday: bool = False

    # --- Series 64: optional, transport-only evidence fields ---
    schema_version: str = CURRENT_SCHEMA_VERSION
    option_chain_liquidity: Tuple[OptionLiquiditySnapshot, ...] = ()
    vix: Optional[float] = None
    vix_as_of: Optional[str] = None
    session_exchange: Optional[str] = None
    session_segment: Optional[str] = None

    # --- Series 67: optional, transport-only genuine intraday OHLC.
    # `spot`/`spot_as_of` above remain the single settlement snapshot
    # (unchanged meaning, unchanged callers). These four are a
    # SEPARATE, real intraday range for the SAME underlying/session --
    # source is a different provider than Bhavcopy (Bhavcopy has no
    # underlying-index OHLC), so all four are `None` unless a caller
    # explicitly supplies them. Never partially filled with a guess:
    # if any of the four is missing, the observation adapter falls
    # back to the pre-Series-67 degenerate representation rather than
    # inventing the missing one.
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None

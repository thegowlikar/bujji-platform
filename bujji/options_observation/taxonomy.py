"""Options Observation Domain vocabulary — BUJJI Engineering Series 73C
(Options Observation Domain v1).

The second concrete domain built on top of the domain-neutral Market
Observation Contract (`bujji/market_observation/`, Series 73A), sibling
to the Futures Observation Domain (`bujji/futures_observation/`, Series
73B) which is this module's direct structural template. This module
records observed Options market facts only -- no interpretation. PCR,
max pain, gamma, delta, theta, vega, IV rank, OI buildup/unwinding,
support/resistance, and trend classification belong to future MSI
brains, never here.

Following this project's established convention for closed vocabularies
(see `bujji/market_observation/taxonomy.py`, `bujji/futures_observation/taxonomy.py`),
this is plain string constants collected into `ALL_*` tuples, not
`enum.Enum` -- matching every other *_observation adjacent module in
this codebase, trivially JSON-serializable without a codec.
"""
from __future__ import annotations

OPTIONS_OBSERVATION_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# OptionType -- the two option contract sides. Closed set, verified
# against real Bhavcopy rows (`OptnTp` column values "CE"/"PE").
# ---------------------------------------------------------------------------
OPTION_TYPE_CALL = "CE"
OPTION_TYPE_PUT = "PE"

ALL_OPTION_TYPES = (
    OPTION_TYPE_CALL,
    OPTION_TYPE_PUT,
)

# ---------------------------------------------------------------------------
# SymbolProvenance -- WHERE `instrument_symbol` CAME FROM. Closed set.
#
# WHY THIS EXISTS. `instrument_symbol` is typed `str`, is validated
# non-empty, and is hashed into `observation_id` via MOC's
# ObservationIdentity.instrument. A provider that has no real symbol
# therefore CANNOT express that fact -- the type contract forces it to
# supply something. StoreChainProvider did exactly what the contract
# forced it to do: it built `f"{underlying}{expiry}{strike}{type}"`, a
# string indistinguishable, by shape alone, from a real one. Downstream
# code could not tell a broker's own symbol from a manufactured one,
# and on 2026-08-20 that cost a live session every entry: FYERS was
# handed "NIFTY2026-08-2524500CE", could not resolve it, and Gate B
# vetoed with MARGIN_NOT_CERTIFIED. The API was never broken.
#
# The fix is NOT to guess from string shape -- a well-formed fake is
# still a fake. It is to record, at construction time, what the builder
# actually knew. This is quality/provenance metadata in exactly MOC's
# own sense: it describes the recording, never the fact recorded, so it
# does NOT participate in identity (see models.py -- it lives on the
# OptionObservation WRAPPER, structurally outside anything
# build_observation() hashes).
#
# There is deliberately NO default. A defaulted provenance is how
# SYNTHETIC silently becomes BROKER_AUTHORITATIVE six months later:
# every construction path must say what it knows, out loud.
# ---------------------------------------------------------------------------

# The execution venue itself returned this exact string (FYERS
# optionchain `symbol`, FYERS instrument master). This is the ONLY
# provenance a real-broker order path may ever accept.
SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE = "BROKER_AUTHORITATIVE"

# A real, source-native instrument identity (e.g. NSE Bhavcopy
# `FinInstrmNm`) -- genuinely observed, not manufactured. But Bujji has
# NOT established that this string is valid at the broker execution
# venue, and it is not assumed to be. Usable for replay, research and
# PaperBroker; never sufficient on its own for a real-broker order.
SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE = "SOURCE_AUTHORITATIVE"

# The builder manufactured the string from expiry/strike/type. Recorded
# so it can be refused; never produced by any path in this codebase.
#
# That last clause was FALSE from the day it was written.
# PaperBroker.resolve_atm_contract manufactured f"{underlying}{strike}{type}"
# -- a venue-SHAPED string with no expiry and no exchange prefix -- and
# ReplayBroker did the same. Both now emit the ABSENT sentinel below instead
# (2026-08-22).
#
# IT IS STILL FALSE, AND SAYING SO IS THE POINT.
# `trading_brain.risk_governor.msi_entry_bridge._leg_to_core_contract` builds
# f"{underlying}{leg.expiry}{int(leg.strike)}{leg.option_type}" -- which is
# "NIFTY2026-08-2524500CE", the exact string option_symbol_resolver's own
# module docstring records as returning verified=False, total_margin=None on
# 2026-08-20. That module is REACHABLE and declared in ARCHITECTURE.md.
# Nothing calls it on the production entry path today, and two ratchet tests
# assert the runner never does -- but the builder is still there.
#
# And note the deeper gap this comment cannot close on its own: nothing in
# this codebase TAGS a manufactured string as SYNTHETIC. A provenance that is
# never assigned cannot refuse anything. The refusal works today only because
# chain rows carry real provenance and manufactured strings never enter that
# path at all.
SYMBOL_PROVENANCE_SYNTHETIC = "SYNTHETIC"

# No symbol was available. The identity field still carries the
# UNRESOLVED_SYMBOL_PREFIX sentinel below, because the model cannot
# represent an empty instrument -- the sentinel exists ONLY to satisfy
# that constraint and is never a broker symbol.
SYMBOL_PROVENANCE_ABSENT = "ABSENT"

ALL_SYMBOL_PROVENANCES = (
    SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE,
    SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE,
    SYMBOL_PROVENANCE_SYNTHETIC,
    SYMBOL_PROVENANCE_ABSENT,
)

# The sentinel a provenance=ABSENT row carries in `instrument_symbol`.
# Pipe-delimited on purpose: no exchange symbol vocabulary in use here
# (FYERS "NSE:NIFTY2681824100CE", NSE "NIFTY26AUG24000CE") contains a
# "|", so a sentinel can never be confused with, or accidentally
# matched against, a tradable identity -- by any reader, including one
# written after this comment.
UNRESOLVED_SYMBOL_PREFIX = "UNRESOLVED|"

# The expiry counterpart. A simulator asked to "resolve the ATM contract"
# genuinely does not know which expiry is listed -- that answer lives in the
# instrument master, which is a network download this codebase deliberately
# does not make from a simulator. Stamping a plausible date would be
# fabrication; stamping "WEEKLY" (what PaperBroker did until 2026-08-22)
# asserts a contract class the simulator never established either.
#
# Hyphen-delimited, NOT pipe-delimited, so it occupies exactly one field of
# an unresolved_symbol() identity rather than splitting into two.
UNRESOLVED_EXPIRY = "UNRESOLVED-EXPIRY"


def unresolved_symbol(underlying: str, expiry: str, strike, option_type: str) -> str:
    """The deterministic non-tradable identity for a row whose source
    supplied no broker symbol.

    Deterministic for a given contract so the observation keeps a
    STABLE `observation_id` across rebuilds -- content-addressing must
    keep working for rows that simply have no symbol.
    """
    return f"{UNRESOLVED_SYMBOL_PREFIX}{underlying}|{expiry}|{strike}|{option_type}"


def is_unresolved_symbol(symbol: str) -> bool:
    """True for an identity produced by `unresolved_symbol()`.

    Exists so the sentinel can be CHECKED rather than merely produced. A
    sentinel with no predicate is a convention, and a convention is what the
    next reader is free to not know about.
    """
    return bool(symbol) and str(symbol).startswith(UNRESOLVED_SYMBOL_PREFIX)

# ---------------------------------------------------------------------------
# OptionsObservationField -- the closed set of raw option fields this
# domain records. Reuses bujji.market_observation.taxonomy's existing
# TYPE_OPTION_CHAIN / TYPE_OPTION_OPEN_INTEREST / TYPE_OPTION_VOLUME /
# TYPE_OPTION_LIQUIDITY ObservationType entries for `observation_type`
# -- this module does NOT invent a parallel ObservationType enum, only
# a field-level vocabulary describing which scalar each field name
# inside an OptionObservation's payload refers to.
#
# Evidence for which fields have a real Bhavcopy column (Engineering
# Series 73C, Step 0, verified against real files at
# /tmp/m1/BhavCopy_NSE_FO_*.csv on 2026-07-24, via
# `ssh root@139.59.76.137` inspection of
# /tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv):
#   Header (identical 34-column header used by the futures rows in the
#   same file, per 73B's own evidence): TradDt,BizDt,Sgmt,Src,
#   FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,
#   FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,
#   LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,
#   OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,
#   SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
#
#   Option rows carry FinInstrmTp in {"STO" (stock options), "IDO"
#   (index options)} -- confirmed via real STO rows for ABCAPITAL (both
#   CE and PE) in the same file 73B inspected.
#
#   STRIKE <- StrkPric, EXPIRY <- XpryDt, OPTION_TYPE <- OptnTp,
#   OPEN <- OpnPric, HIGH <- HghPric, LOW <- LwPric, CLOSE <- ClsPric,
#   VOLUME <- TtlTradgVol, OPEN_INTEREST <- OpnIntrst,
#   CHANGE_IN_OPEN_INTEREST <- ChngInOpnIntrst,
#   SETTLEMENT_PRICE <- SttlmPric, UNDERLYING_PRICE <- UndrlygPric --
#   all present and populated on every real option row inspected
#   (a real ITM/OTM 0-volume row can still carry OpnPric/HghPric/
#   LwPric == 0.00 -- a genuine "no trade occurred" value, not a
#   missing one; only a blank/absent cell is treated as missing here).
#
#   BID, ASK, BID_QUANTITY, ASK_QUANTITY -- CONFIRMED ABSENT. No column
#   in the 34-column header corresponds to a bid, ask, bid-quantity, or
#   ask-quantity value. This matches Series 73B's own finding for the
#   same file family, and matches this project's own prior, already
#   documented finding in `bujji/replay/option_chain_ingestion.py`
#   ("Bid/ask: left None in every OptionLiquiditySnapshot produced
#   here -- bhavcopy has no bid/ask column") and
#   `bujji/replay/historical_session.py`'s `OptionLiquiditySnapshot`
#   (Series 64/65), whose `bid`/`ask` fields are consistently populated
#   as `None` from this same Bhavcopy source, never fabricated. This
#   domain's BID/ASK/BID_QUANTITY/ASK_QUANTITY fields therefore always
#   resolve to `None` for Bhavcopy-sourced OptionObservations -- the
#   fields still EXIST in the schema (a future live/L2 source may one
#   day populate them), they are simply, disclosedly, never populated
#   by this ingestion path. See `missing_fields` on every such
#   Observation and `docs/OPTIONS_OBSERVATION_DOMAIN.md`.
# ---------------------------------------------------------------------------
FIELD_OPEN = "OPEN"
FIELD_HIGH = "HIGH"
FIELD_LOW = "LOW"
FIELD_CLOSE = "CLOSE"
FIELD_SETTLEMENT = "SETTLEMENT"
FIELD_VOLUME = "VOLUME"
FIELD_OPEN_INTEREST = "OPEN_INTEREST"
FIELD_CHANGE_IN_OPEN_INTEREST = "CHANGE_IN_OPEN_INTEREST"
# The broker's OWN previous-session open interest, carried verbatim.
#
# The FYERS optionchain endpoint returns oi/prev_oi/oich per strike, with
# oich == oi - prev_oi confirmed to hold exactly on real NIFTY strikes
# (bujji/broker/fyers.py, live-verified 2026-07-20). Bujji retained only oi
# and discarded the other two, so the broker handed it a verified OI change
# and the extraction threw it away.
#
# NOT MANDATORY, deliberately. Bhavcopy has no previous-OI column, so making
# it mandatory would mark every Bhavcopy-sourced observation permanently
# INCOMPLETE for a field that source structurally cannot supply -- the same
# reasoning that keeps BID/ASK out of the mandatory set. It still appears in
# missing_fields whenever absent, because disclosure is unconditional.
FIELD_PREVIOUS_OPEN_INTEREST = "PREVIOUS_OPEN_INTEREST"
FIELD_UNDERLYING_PRICE = "UNDERLYING_PRICE"
FIELD_BID = "BID"                    # Never populated from Bhavcopy -- see evidence above.
FIELD_ASK = "ASK"                    # Never populated from Bhavcopy -- see evidence above.
FIELD_BID_QUANTITY = "BID_QUANTITY"  # Never populated from Bhavcopy -- see evidence above.
FIELD_ASK_QUANTITY = "ASK_QUANTITY"  # Never populated from Bhavcopy -- see evidence above.

ALL_OPTIONS_OBSERVATION_FIELDS = (
    FIELD_OPEN,
    FIELD_HIGH,
    FIELD_LOW,
    FIELD_CLOSE,
    FIELD_SETTLEMENT,
    FIELD_VOLUME,
    FIELD_OPEN_INTEREST,
    FIELD_CHANGE_IN_OPEN_INTEREST,
    FIELD_PREVIOUS_OPEN_INTEREST,
    FIELD_UNDERLYING_PRICE,
    FIELD_BID,
    FIELD_ASK,
    FIELD_BID_QUANTITY,
    FIELD_ASK_QUANTITY,
)

# Fields that are structurally mandatory on every OptionObservation --
# a row missing one of these is a genuine, disclosed data gap (recorded
# via ObservationQualityMetadata.missing_fields), never silently
# defaulted. BID/ASK/BID_QUANTITY/ASK_QUANTITY are deliberately
# excluded from "mandatory": they are known-never-available from this
# source, so treating them as mandatory would mark every single
# Bhavcopy-sourced observation permanently INCOMPLETE for a field this
# source structurally cannot supply. They remain in `missing_fields`
# regardless (every genuinely-None field is recorded there), just not
# counted against `completeness`.
MANDATORY_OPTIONS_OBSERVATION_FIELDS = (
    FIELD_OPEN,
    FIELD_HIGH,
    FIELD_LOW,
    FIELD_CLOSE,
    FIELD_SETTLEMENT,
    FIELD_VOLUME,
    FIELD_OPEN_INTEREST,
    FIELD_CHANGE_IN_OPEN_INTEREST,
)

# Fields that are known, disclosed, permanently absent from the
# Bhavcopy source. Always contributes to `missing_fields`, never to
# the completeness denominator.
KNOWN_UNAVAILABLE_FROM_BHAVCOPY = (
    FIELD_PREVIOUS_OPEN_INTEREST,
    FIELD_BID,
    FIELD_ASK,
    FIELD_BID_QUANTITY,
    FIELD_ASK_QUANTITY,
)

# ---------------------------------------------------------------------------
# Raw NSE Bhavcopy FinInstrmTp codes identifying an option row (as
# opposed to STF/IDF futures rows, which
# bujji/futures_observation/taxonomy.py already consumes). Closed set,
# verified against real files -- never inferred. Matches
# `bujji/replay/option_chain_ingestion.py::OPTION_INSTRUMENT_TYPES`.
# ---------------------------------------------------------------------------
OPTIONS_INSTRUMENT_TYPE_STOCK = "STO"
OPTIONS_INSTRUMENT_TYPE_INDEX = "IDO"

ALL_OPTIONS_INSTRUMENT_TYPES = (
    OPTIONS_INSTRUMENT_TYPE_STOCK,
    OPTIONS_INSTRUMENT_TYPE_INDEX,
)

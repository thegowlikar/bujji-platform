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

"""Futures Observation Domain vocabulary — BUJJI Engineering Series 73B
(Futures Observation Domain v1).

The first concrete domain built on top of the domain-neutral Market
Observation Contract (`bujji/market_observation/`, Series 73A). This
module records observed Futures market facts only -- no interpretation.
Long build-up, short build-up, short covering, long unwinding, basis
interpretation, and trend classification belong to future MSI brains,
never here.

Following this project's established convention for closed vocabularies
(see `bujji/market_observation/taxonomy.py`, `bujji/runtime_safety/taxonomy.py`,
`bujji/authentication/taxonomy.py`), this is plain string constants
collected into `ALL_*` tuples, not `enum.Enum` -- matching every other
*_observation adjacent module in this codebase, trivially
JSON-serializable without a codec.
"""
from __future__ import annotations

FUTURES_OBSERVATION_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# FuturesObservationField -- the closed set of raw futures fields this
# domain records. Reuses bujji.market_observation.taxonomy's existing
# TYPE_FUTURES / TYPE_FUTURES_OPEN_INTEREST ObservationType entries for
# `observation_type` -- this module does NOT invent a parallel
# ObservationType enum, only a field-level vocabulary describing which
# scalar each field name inside a FuturesObservation's payload refers to.
#
# Evidence for which fields have a real Bhavcopy column (Engineering
# Series 73B, Step 0, verified against real files at
# /tmp/m1/BhavCopy_NSE_FO_*.csv on 2026-07-24):
#   Header: TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,
#   SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,
#   OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,
#   SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,
#   TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
#
#   Futures rows carry FinInstrmTp in {"STF" (stock futures), "IDF"
#   (index futures)} -- confirmed via a real STF row (ABCAPITAL) and a
#   real IDF row (BANKNIFTY) in
#   /tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv.
#
#   OPEN <- OpnPric, HIGH <- HghPric, LOW <- LwPric, CLOSE <- ClsPric,
#   VOLUME <- TtlTradgVol, OPEN_INTEREST <- OpnIntrst,
#   CHANGE_IN_OPEN_INTEREST <- ChngInOpnIntrst,
#   SETTLEMENT_PRICE <- SttlmPric -- all present and populated on every
#   real futures row inspected.
#
#   BASIS has no dedicated raw column. However every futures row also
#   carries `UndrlygPric` (the underlying/spot price at settlement) on
#   the SAME row. BASIS here is defined as the simple arithmetic
#   difference SETTLEMENT_PRICE - UNDERLYING_PRICE, both raw fields
#   already present on one row -- not a classification, not a
#   premium/discount *label* (that framing is MSI's interpretive job),
#   just a signed number. It is included as OPTIONAL: None whenever
#   UndrlygPric is blank on the source row (never fabricated/defaulted
#   to 0).
# ---------------------------------------------------------------------------
FIELD_OPEN = "OPEN"
FIELD_HIGH = "HIGH"
FIELD_LOW = "LOW"
FIELD_CLOSE = "CLOSE"
FIELD_VOLUME = "VOLUME"
FIELD_OPEN_INTEREST = "OPEN_INTEREST"
FIELD_CHANGE_IN_OPEN_INTEREST = "CHANGE_IN_OPEN_INTEREST"
FIELD_SETTLEMENT_PRICE = "SETTLEMENT_PRICE"
FIELD_BASIS = "BASIS"  # Derived: SETTLEMENT_PRICE - UNDERLYING_PRICE. Optional.

ALL_FUTURES_OBSERVATION_FIELDS = (
    FIELD_OPEN,
    FIELD_HIGH,
    FIELD_LOW,
    FIELD_CLOSE,
    FIELD_VOLUME,
    FIELD_OPEN_INTEREST,
    FIELD_CHANGE_IN_OPEN_INTEREST,
    FIELD_SETTLEMENT_PRICE,
    FIELD_BASIS,
)

# Fields that are structurally mandatory on every FuturesObservation --
# a row missing one of these is a genuine, disclosed data gap (recorded
# via ObservationQualityMetadata.missing_fields), never silently
# defaulted. BASIS is deliberately excluded: it depends on an
# underlying price that bhavcopy does not always carry.
MANDATORY_FUTURES_OBSERVATION_FIELDS = (
    FIELD_OPEN,
    FIELD_HIGH,
    FIELD_LOW,
    FIELD_CLOSE,
    FIELD_VOLUME,
    FIELD_OPEN_INTEREST,
    FIELD_CHANGE_IN_OPEN_INTEREST,
    FIELD_SETTLEMENT_PRICE,
)

# ---------------------------------------------------------------------------
# Raw NSE Bhavcopy FinInstrmTp codes identifying a futures row (as
# opposed to STO/IDO option rows, which
# bujji/replay/option_chain_ingestion.py already consumes). Closed set,
# verified against real files -- never inferred.
# ---------------------------------------------------------------------------
FUTURES_INSTRUMENT_TYPE_STOCK = "STF"
FUTURES_INSTRUMENT_TYPE_INDEX = "IDF"

ALL_FUTURES_INSTRUMENT_TYPES = (
    FUTURES_INSTRUMENT_TYPE_STOCK,
    FUTURES_INSTRUMENT_TYPE_INDEX,
)

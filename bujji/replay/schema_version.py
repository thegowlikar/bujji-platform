"""Historical Session Record Schema Version — BUJJI Options OS v3,
Engineering Series 64.

A finite, explicit versioning of `HistoricalSessionRecord`'s own
shape. `SCHEMA_VERSION_V1` is Series 59's original schema (spot,
option chain identity, MIC classification fields, holiday flag).
`SCHEMA_VERSION_V2` (Series 64) is strictly additive: every v1 field
is unchanged in name, position, type, and default; new fields are
appended with defaults that make every v1-shaped construction (keyword
or positional) continue to work unmodified. No v1 corpus, test, or
serialized record needs to change to remain valid under v2.
"""
from __future__ import annotations

SCHEMA_VERSION_V1 = "1.0.0"
SCHEMA_VERSION_V2 = "2.0.0"
SCHEMA_VERSION_V3 = "3.0.0"

ALL_SCHEMA_VERSIONS = (SCHEMA_VERSION_V1, SCHEMA_VERSION_V2, SCHEMA_VERSION_V3)

CURRENT_SCHEMA_VERSION = SCHEMA_VERSION_V3

SCHEMA_VERSION_DESCRIPTIONS = {
    SCHEMA_VERSION_V1: "Series 59 original: spot, option chain identity (strike/type/expiry/symbol), MIC classification fields, holiday flag. No liquidity, OI, or VIX transport.",
    SCHEMA_VERSION_V2: "Series 64: adds optional, transport-only fields for option open interest, bid/ask, India VIX, and session metadata. Every field is additive; nothing in v1 changed. Fields are carried through the pipeline but not consumed by any decision-making logic.",
    SCHEMA_VERSION_V3: "Series 67: adds optional, transport-only open/high/low/close fields for the underlying's own genuine intraday range (a separate source than Bhavcopy's settlement `spot`). Additive only; None unless explicitly supplied.",
}

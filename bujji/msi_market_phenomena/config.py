"""MPC config — Series 103. Declarative only; the phenomenon rules
themselves live in engine.py's _PHENOMENON_RULES table (also
declarative, never tuned)."""
from __future__ import annotations

from . import taxonomy as t

DEFAULT_PROVENANCE = "bujji.msi_market_phenomena.engine"
SCHEMA_VERSION = t.MSI_MARKET_PHENOMENA_VERSION

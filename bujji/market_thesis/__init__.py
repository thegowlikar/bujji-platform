"""Market Thesis — BUJJI Options OS v3, Trading Brain Intelligence
Upgrade, Phase 3.

A thin composition layer over two already-real engines
(`msi_trade_thesis.derive_trade_thesis`, `msi_strategy_selection_
foundation.assess_all_families`) plus three genuinely missing
"environment" fields (premium, positioning, liquidity). See engine.py
for the full design rationale and field-by-field provenance.
"""
from .engine import assess
from .models import MarketThesisAssessment
from . import taxonomy

__all__ = ["assess", "MarketThesisAssessment", "taxonomy"]

"""Intelligence Decision Orchestrator vocabulary — BUJJI Options OS v3,
Trading Brain Intelligence Upgrade, Phase 4.

A THIN CONDUCTOR. This package computes no new market intelligence and
no new strategy suitability logic -- it calls four already-real
engines in sequence (`market_thesis.assess`, `bujji.trading_brain.
strategy_selector.engine.select`, `bujji.trading_brain.
strategy_evaluator.engine.rank`, and this module's own minimal
should-we-trade synthesis) and narrates what each one produced. The
only genuinely new logic anywhere in this package is `decision_
status`'s TRADE/NO_TRADE rule and `confidence`'s derivation from the
evaluator's own real `high_count` -- both small, disclosed, and
documented in engine.py.
"""
from __future__ import annotations

ORCHESTRATOR_VERSION = "1.0.0"

DECISION_TRADE = "TRADE"
DECISION_NO_TRADE = "NO_TRADE"

ALL_DECISION_STATUSES = (DECISION_TRADE, DECISION_NO_TRADE)

# Confidence -- same NONE/LOW/MODERATE/HIGH convention as every other
# MSI/Trading-Brain package, derived deterministically from the winning
# candidate's own real high_count (out of 4 real dimensions scored by
# strategy_evaluator) -- never a new scoring formula, a simple,
# disclosed threshold ladder over an already-real integer.
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"

ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)

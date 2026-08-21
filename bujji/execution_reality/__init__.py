"""Execution Reality Layer -- BUJJI Options OS, Phase-0.

PHASE-0 SCOPE ONLY: quote capture, normalization, and forensic
observation logging. Nothing in this package makes, influences, or is
consulted for any trading decision -- no strategy selection, no risk
approval, no entry/exit decision, no liquidity gating. See
docs/EXECUTION_INTELLIGENCE_PHASE2_DESIGN.md for the full, frozen
architecture this package is Phase-0 of; everything beyond Market
Quote Adapter + LegQuote + QuoteObservationRecord (LiquidityGate, Fill
Expectation Model, ExecutionQualityJournal, Execution Analytics,
calibration) is deliberately NOT built here.

This is a new top-level package (matching this repo's own convention
of one top-level package per distinct architectural layer -- see
shadow_observatory/, production_runtime/, intelligence/) because the
Execution Reality Layer is a genuinely new architectural pillar, not
an extension of any existing one.
"""

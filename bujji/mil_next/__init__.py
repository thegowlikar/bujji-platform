"""MIL Next -- Continuous Dynamic-Market Intelligence, OFFLINE LABORATORY.

This package computes a `MarketIntelligenceSnapshot` entirely offline,
against replayed/historical market data, for design validation and
deterministic test coverage only. It is NOT imported by
`run_live_shadow.py`, `bujji/live_pipeline_bridge.py`,
`bujji/live_shadow_operator/`, `bujji/trading_brain/`, or
`bujji/production_runtime/`.

Its designated future live owner, if and when promoted, is
`bujji/live_pipeline_bridge.py::SessionDriver.run_decision_cadence`.

Gate 2 (a separate, future, explicitly-approved authorization) either:
  (a) promotes this package's tested logic through that owner,
      accompanied by live-wiring tests proving `run_decision_cadence`
      actually invokes it and its output reaches a real live session
      -- the same live-wiring-test discipline already required of
      every other MIL component; or
  (b) results in this package being removed or replaced without ever
      having been live-wired.

No component in this package may be described, documented, or
referenced anywhere in this codebase as a "production", "live", or
"operational" MIL capability before that promotion gate passes. Until
then, this is test-and-replay infrastructure only.

Scope note: this laboratory's regime/thesis/contradiction composition
is a simplified, self-contained, deterministic stand-in -- it does NOT
call or reimplement the real bujji.msi_price_structure /
bujji.msi_market_structure / bujji.msi_volatility_structure /
bujji.msi_trade_thesis engines. Achieving field-level parity with those
engines is explicit, out-of-scope future work for Gate 2 promotion, not
attempted here.
"""

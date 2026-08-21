"""bujji.market_data_integrity — Phase 19.20.4.

Read-only. Answers "can Bujji prove the market data it observed is
trustworthy?" — never repairs, overwrites, or fabricates data.

Reads (never writes to):
  - bujji.market_microstructure.store.MicrostructureStore
  - bujji.historical_reality.store.HistoricalObservationStore (Phase 19.19)
  - NSE bhavcopy CSV files

Reuses bujji.market_observation.engine's gap-detection arithmetic
verbatim (detect_gaps, taxonomy, ValidationResult/SeriesGap shapes) —
this package never reimplements gap-detection arithmetic, only expands
an already-detected gap into its constituent missing minute boundaries
and packages the result as IntegrityIssue records.

No broker import, no execution surface, no strategy/decision
vocabulary — enforced structurally by
tests/test_market_data_integrity/test_safety_boundary.py.
"""

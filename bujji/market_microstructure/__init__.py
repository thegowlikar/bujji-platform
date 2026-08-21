"""bujji.market_microstructure — Phase 19.20.2.

Isolated, additive, observation-only. Turns raw ticks into
`MinuteObservation` records (OHLC + microstructure texture: tick
density, silence gaps, tick-to-tick price/premium movement) without
ever storing raw ticks permanently.

Reuses `bujji.live_observation`'s window primitives (`new_window`,
`add_tick`, `close_window`) and `bujji.market_timeseries`'s own
interval/kind vocabulary verbatim -- this package adds only the
metadata-derivation step on top of an already-closed window, never a
new candle-window implementation.

Nothing here connects to a broker, opens a socket, or reads a raw
FYERS feed -- this package is fed already-decoded (instrument,
timestamp, price) ticks by a caller. Structurally incapable of
placing, modifying, or cancelling an order: no such method exists
anywhere in this package, and no broker/execution/strategy module is
imported (enforced by `tests/test_market_microstructure/test_safety_boundary.py`).
"""

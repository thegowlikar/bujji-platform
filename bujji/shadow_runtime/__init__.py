"""Shadow Runtime -- BUJJI Options OS, Phase-5.

A minimal, standalone, LIVE-CAPABLE (but not yet live-verified)
observation runtime. This is NOT a trading runtime: it observes market
data, persists observations, and produces a session summary -- it has
no concept of a trade, a strategy, a position, or risk.

Reuses bujji.execution_reality (Phase-0-4, unmodified) for quote
normalization, storage, and liquidity classification. Calls only
Broker.connect()/get_quote() -- never any order/position/margin/funds
method. Imports nothing from msi_trade_construction, trading_brain,
trading_session_governor, shadow_observatory, or production_runtime
(the Phase-5 design review's own final dependency diagram excludes
production_runtime entirely -- this package does not even read-import
its RuntimeScheduler, to keep the dependency graph exactly as
designed: shadow_runtime depends only on execution_reality and
broker).

No credentials, no .env loading, anywhere in this package -- the
broker connection is always caller-supplied (real or fake); this
package never knows or cares which.
"""

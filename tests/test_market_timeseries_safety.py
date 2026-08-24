"""Safety tests -- Phase 15Q Market Timeseries.

This package OBSERVES and STORES market data and computes statistics
over it. It must never place an order, never import execution or
strategy-selection paths, and never fabricate a data point that the
market did not actually produce."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_RELS = (
    "bujji/market_timeseries/models.py",
    "bujji/market_timeseries/store.py",
    "bujji/market_timeseries/aggregator.py",
    "bujji/market_timeseries/subscription.py",
    "bujji/market_timeseries/indicators.py",
    "bujji/market_timeseries/materializer.py",
    "bujji/market_timeseries/futures_stats_models.py",
    "bujji/market_timeseries/futures_stats_store.py",
    "bujji/market_timeseries/futures_stats_materializer.py",
)
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain", "bujji.capital_brain",
    "fyers_apiv3", "bujji.broker",
    "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_eligibility",
    "bujji.msi_trade_intent", "bujji.msi_decision_synthesis",
    "bujji.position_lifecycle", "bujji.outcome_memory",
)


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def _tree(rel):
    with open(_abs(rel)) as f:
        return ast.parse(f.read(), filename=rel)


def test_no_forbidden_imports():
    for rel in _MODULE_RELS:
        for node in ast.walk(_tree(rel)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not name.startswith(forbidden), f"{rel} imports forbidden module {name}"


def test_no_forbidden_order_calls():
    for rel in _MODULE_RELS:
        for node in ast.walk(_tree(rel)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALL_NAMES, f"{rel} calls forbidden method {name}()"


def test_package_never_opens_a_market_data_socket_itself():
    """The aggregator is FED ticks by a caller -- it must never
    subscribe or connect to a feed on its own, so the feed's lifecycle
    stays owned by one place.

    `sqlite3.connect()` is deliberately permitted and verified by AST
    rather than banned by substring: it opens a local DATABASE FILE
    handle, not a network socket, and the store legitimately needs it.
    Every OTHER `.connect(` in this package is a failure."""
    forbidden_identifiers = {"FyersTickFeed", "subscribe", "websocket", "aiohttp", "requests", "urllib", "socket"}
    for rel in _MODULE_RELS:
        tree = _tree(rel)

        # AST-only: real identifiers, imports and attribute accesses. A
        # DOCSTRING naming FyersTickFeed (to explain what the symbol
        # list is handed to) is documentation, not a dependency, and
        # must not trip this check -- so raw-text scanning is
        # deliberately not used here.
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.Import):
                used.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                used.add(node.module.split(".")[0])
                used.update(a.name for a in node.names)

        offending = used & forbidden_identifiers
        assert not offending, f"{rel} really references feed/network identifiers {offending}"

        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "connect"):
                target = node.func.value
                assert isinstance(target, ast.Name) and target.id == "sqlite3", (
                    f"{rel} calls .connect() on something other than sqlite3 -- "
                    "this package must never open a market-data socket"
                )


def test_indicators_never_fabricate_on_short_history():
    """The single most important anti-fabrication rule for TA: an
    indicator with insufficient history returns None, never a
    best-effort number computed from a partial window."""
    from bujji.market_timeseries import indicators as ta
    from bujji.market_timeseries.models import Candle, INTERVAL_FIVE_MINUTE, KIND_SPOT

    bars = [Candle(instrument="X", kind=KIND_SPOT, interval=INTERVAL_FIVE_MINUTE,
                   window_start=f"2026-08-06T09:{15 + i}:00", window_end=f"2026-08-06T09:{20 + i}:00",
                   open=100.0, high=101.0, low=99.0, close=100.0, volume=None, tick_count=1)
            for i in range(3)]
    for fn in (ta.sma, ta.ema, ta.rsi, ta.atr, ta.bollinger, ta.realised_volatility):
        assert fn(bars, 20) is None, f"{fn.__name__} fabricated a value from 3 bars"


def test_zero_tick_window_never_becomes_a_candle():
    """A window the market never traded in must produce no row -- the
    gap is real information and must not be forward-filled."""
    from bujji.market_timeseries.aggregator import CandleAggregator
    assert CandleAggregator().flush() == []


def test_store_never_silently_overwrites_history():
    """Settled candles are immutable -- a conflicting rewrite must
    raise, not replace."""
    import tempfile

    from bujji.market_timeseries.models import Candle, INTERVAL_FIVE_MINUTE, KIND_SPOT
    from bujji.market_timeseries.store import CandleStore, ConflictingCandleError

    def _c(close):
        return Candle(instrument="X", kind=KIND_SPOT, interval=INTERVAL_FIVE_MINUTE,
                      window_start="2026-08-06T09:15:00", window_end="2026-08-06T09:20:00",
                      open=100.0, high=101.0, low=99.0, close=close, volume=None, tick_count=5)

    with tempfile.TemporaryDirectory() as tmp:
        store = CandleStore(os.path.join(tmp, "c.db"))
        store.write_candle(_c(100.0))
        try:
            store.write_candle(_c(500.0))
            raise AssertionError("conflicting candle was accepted -- history is not immutable")
        except ConflictingCandleError:
            pass
        finally:
            store.close()


def test_forming_candle_cannot_masquerade_as_settled_history():
    """Structural guard: the live bar is a different type and has no
    `close` attribute, so an indicator expecting settled candles cannot
    silently consume it."""
    from bujji.market_timeseries.aggregator import CandleAggregator
    from bujji.market_timeseries.models import Candle

    agg = CandleAggregator()
    agg.ingest("X", "2026-08-06T09:15:10", 100.0)
    forming = agg.forming("X")
    assert not isinstance(forming, Candle)
    assert not hasattr(forming, "close")
    assert forming.is_closed is False


def test_no_execution_or_risk_packages_touched():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/broker/guard.py", "bujji/broker/hybrid.py",
         "bujji/trading_brain/", ":(exclude)bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py",
         # M4 (2026-08-23): a NARROW, authorised extension -- the
         # SESSION_TRANSITION event type, so session lifecycle lives in the
         # same durable journal as position lifecycle rather than a second
         # store. The authorisation is on the CHANGE, not the file:
         # tests/test_frozen_vocabulary_extension.py asserts every added line
         # belongs to that block and that nothing was removed, so an
         # unrelated edit to this same file still fails the build.
         ":(exclude)bujji/trading_brain/risk_governor/position_group_validation.py", "bujji/risk_governor/", "bujji/execution_engine/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected changes: {result.stdout}"


def test_live_observation_reused_not_reimplemented():
    """Phase 15Q must REUSE the existing tick-window primitives rather
    than fork them -- guards against a second, divergent aggregation
    implementation appearing in the codebase."""
    with open(_abs("bujji/market_timeseries/aggregator.py")) as f:
        content = f.read()
    assert "from bujji.live_observation.engine import" in content
    for reused in ("add_tick", "close_window", "new_window"):
        assert reused in content, f"aggregator.py no longer reuses live_observation.{reused}"

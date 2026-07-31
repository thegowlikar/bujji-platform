"""In-memory paper-trading broker.

Simulates fills at the last traded premium, nets positions (a BUY closes a
SELL), and serves as the reference implementation of the :class:`Broker`
contract. It also exposes small, explicit fault-injection hooks so the
capital-protection paths (idempotency, partial fills, recovery) can be tested
deterministically. All hooks default to benign behaviour.
"""
from __future__ import annotations

import random
from datetime import timedelta
from typing import Optional

from ..core.clock import now_ist
from ..core.enums import Direction, OptionType, OrderStatus, Side
from ..core.models import Candle, OptionContract, OrderRequest, OrderResult
from .base import Broker
from .errors import AuthenticationError


class PaperBroker(Broker):
    name = "paper"

    def __init__(
        self,
        seed: int = 42,
        base_spot: float = 22000.0,
        *,
        partial_fill_qty: Optional[int] = None,
        raise_on_place_after_record: bool = False,
        # Capital Management Engine test/dev knobs -- generous defaults so
        # existing callers (tests, plain paper mode) see the SAME
        # unconstrained-entry behavior as before the CME existed. Override
        # these to test capital-aware sizing specifically (increasing/
        # decreasing capital, insufficient margin, etc).
        account_equity: Optional[float] = 1_00_00_000.0,
        available_margin: Optional[float] = 1_00_00_000.0,
        margin_per_lot: Optional[float] = 1_00_000.0,
        funds_unavailable: bool = False,
        margin_unavailable: bool = False,
    ) -> None:
        self._rng = random.Random(seed)
        self._spot = base_spot
        self._orders: dict[str, OrderResult] = {}
        self._positions: dict[str, dict] = {}
        self._premium: dict[str, float] = {}
        # Live Shadow Real-Time Paper Execution sprint (Part 2): a plain
        # position LEDGER only -- entry timestamp, entry price, qty,
        # direction, and REALIZED P&L (once a position closes or is
        # reduced). No MTM/unrealized figure is computed here -- that is
        # the Portfolio Valuation engine's own, separate responsibility
        # (bujji/trading_brain/portfolio_valuation/), which this broker
        # never imports and knows nothing about.
        self._realized_pnl: dict[str, float] = {}
        # Fault-injection hooks (test-only; benign by default).
        self._partial_fill_qty = partial_fill_qty
        self._raise_on_place_after_record = raise_on_place_after_record
        self._place_calls = 0
        self._auth_expired = False  # E1/E2 simulation.
        self._auth_error_calls = 0
        # Capital Management Engine synthetic funds/margin (paper mode has
        # no real broker account -- these are configurable so tests can
        # simulate any capital scenario deterministically).
        self._account_equity = account_equity
        self._available_margin = available_margin
        self._margin_per_lot = margin_per_lot
        self._funds_unavailable = funds_unavailable
        self._option_volume = 5_000_000.0  # Overridable via set_option_volume().
        self._margin_unavailable = margin_unavailable

    def set_capital(self, *, account_equity: Optional[float] = None,
                    available_margin: Optional[float] = None,
                    margin_per_lot: Optional[float] = None) -> None:
        """Change simulated capital mid-test -- e.g. "capital changed
        overnight" scenarios (increasing/decreasing between two runs)."""
        if account_equity is not None:
            self._account_equity = account_equity
        if available_margin is not None:
            self._available_margin = available_margin
        if margin_per_lot is not None:
            self._margin_per_lot = margin_per_lot

    def set_option_volume(self, volume: float) -> None:
        """Override the synthetic per-candle option volume returned by
        get_option_candles() -- e.g. set to 0.0 to test the
        volume-unavailable/equal-weight-fallback path."""
        self._option_volume = volume

    async def get_funds(self) -> Optional[dict]:
        self._check_auth()
        if self._funds_unavailable:
            return None
        return {
            "account_equity": self._account_equity,
            "available_funds": self._available_margin,
            "available_margin": self._available_margin,
            "cash_balance": self._account_equity,
            "collateral": 0.0,
            "used_margin": 0.0,
            "available_exposure": self._available_margin,
            "peak_margin": self._available_margin,
        }

    async def get_order_margin(self, ce_contract, pe_contract) -> Optional[dict]:
        self._check_auth()
        if self._margin_unavailable or self._margin_per_lot is None:
            return None
        return {
            "margin_per_lot": self._margin_per_lot,
            "verified": True,
            "source": "paper_synthetic",
        }

    # -- Test/inspection helpers --------------------------------------- #
    @property
    def place_calls(self) -> int:
        """Number of times place_order actually reached the 'exchange'."""
        return self._place_calls

    @property
    def auth_error_calls(self) -> int:
        """Number of calls that raised the simulated auth failure."""
        return self._auth_error_calls

    def seed_position(self, symbol: str, side: str, qty: int,
                      avg_price: float = 120.0) -> None:
        """Inject a pre-existing broker position (simulates a live holding)."""
        self._positions[symbol] = {
            "symbol": symbol, "side": side, "qty": qty, "avg_price": avg_price,
            "entry_timestamp": now_ist().isoformat(),
        }
        self._premium.setdefault(symbol, avg_price)

    def simulate_auth_expiry(self, expired: bool = True) -> None:
        """Make every subsequent broker call raise AuthenticationError (E1/E2).

        Simulates a token expiring (or a session being invalidated) mid-run.
        Call with ``expired=False`` to simulate a human refreshing the token.
        """
        self._auth_expired = expired

    def _check_auth(self) -> None:
        if self._auth_expired:
            self._auth_error_calls += 1
            raise AuthenticationError(
                "simulated: access token expired / session invalidated"
            )

    # -- Broker contract ------------------------------------------------ #
    async def connect(self) -> None:
        self._check_auth()
        return None

    async def get_spot(self, underlying: str) -> float:
        self._check_auth()
        self._spot += self._rng.uniform(-5, 5)
        return round(self._spot, 2)

    async def get_recent_candles(
        self, underlying: str, minutes: int, count: int
    ) -> list[Candle]:
        self._check_auth()
        candles: list[Candle] = []
        now = now_ist().replace(second=0, microsecond=0)
        price = self._spot
        for i in range(count, 0, -1):
            o = price
            price += self._rng.uniform(-10, 10)
            h = max(o, price) + self._rng.uniform(0, 5)
            low = min(o, price) - self._rng.uniform(0, 5)
            candles.append(
                Candle(now - timedelta(minutes=minutes * i), o, h, low, price, 1000)
            )
        return candles

    async def resolve_atm_contract(
        self, underlying, spot, direction, strike_interval, lot_size
    ) -> OptionContract:
        self._check_auth()
        strike = self.atm_strike(spot, strike_interval)
        opt = self.option_type_for(direction)
        symbol = f"{underlying}{strike}{opt.value}"
        self._premium.setdefault(symbol, 120.0)
        return OptionContract(symbol, underlying, strike, opt, "WEEKLY", lot_size)

    async def get_ltp(self, contract: OptionContract) -> float:
        self._check_auth()
        cur = self._premium.get(contract.symbol, 120.0)
        cur = max(0.5, cur + self._rng.uniform(-3, 3))
        self._premium[contract.symbol] = round(cur, 2)
        return self._premium[contract.symbol]

    async def get_option_candles(self, contract: OptionContract, minutes: int,
                                 count: int) -> list[Candle]:
        """Synthetic single completed candle carrying a plausible non-zero
        volume, for tests/dev -- override `self._option_volume` per test to
        exercise specific volume-weighting scenarios."""
        self._check_auth()
        price = await self.get_ltp(contract)
        volume = self._option_volume
        return [Candle(now_ist(), price, price, price, price, volume)]

    async def place_order(self, request: OrderRequest) -> OrderResult:
        self._check_auth()
        # Idempotent: a known client id returns the same result, never re-nets.
        if request.client_order_id in self._orders:
            return self._orders[request.client_order_id]

        filled = request.quantity
        status = OrderStatus.FILLED
        if self._partial_fill_qty is not None:
            filled = min(request.quantity, self._partial_fill_qty)
            status = (OrderStatus.FILLED if filled >= request.quantity
                      else OrderStatus.PARTIAL)

        price = request.limit_price or self._premium.get(
            request.contract.symbol, 120.0
        )
        result = OrderResult(
            client_order_id=request.client_order_id,
            status=status,
            broker_order_id=f"PAPER-{len(self._orders) + 1}",
            filled_quantity=filled,
            average_price=price,
            message="paper_fill" if status is OrderStatus.FILLED else "paper_partial",
        )
        # Record the order BEFORE any simulated fault, so a subsequent lookup
        # (get_order) can find it — this is exactly what proves idempotency.
        self._orders[request.client_order_id] = result
        self._apply_fill(request, filled, price)
        self._place_calls += 1

        if self._raise_on_place_after_record:
            # Simulate: exchange accepted the order, but our response was lost.
            self._raise_on_place_after_record = False
            raise RuntimeError("simulated_network_error_after_accept")
        return result

    def _apply_fill(self, request: OrderRequest, filled: int, price: float) -> None:
        """Net the filled quantity into the position book (BUY closes SELL),
        realizing PnL on whatever portion of this fill closes or reduces an
        existing position (Part 2: a plain ledger, never a MTM/valuation
        engine -- realized PnL only, computed here because it depends on
        this broker's own fill/netting mechanics, nothing else)."""
        symbol = request.contract.symbol
        prev = self._positions.get(symbol)
        prev_net = 0
        prev_avg = None
        prev_entry_ts = None
        if prev:
            prev_net = prev["qty"] if prev["side"] == Side.BUY.value else -prev["qty"]
            prev_avg = prev["avg_price"]
            prev_entry_ts = prev.get("entry_timestamp")
        delta = filled if request.side is Side.BUY else -filled
        net = prev_net + delta

        # Realize PnL for whatever portion of this fill reduces/closes the
        # PRIOR position (i.e. this fill's side is opposite prev_net's sign).
        if prev_net != 0 and (prev_net > 0) != (delta > 0):
            closing_qty = min(abs(delta), abs(prev_net))
            # Long position (prev_net>0) closed by a SELL: profit = (fill - entry) * qty.
            # Short position (prev_net<0) closed by a BUY: profit = (entry - fill) * qty.
            sign = 1 if prev_net > 0 else -1
            realized = sign * (price - prev_avg) * closing_qty
            self._realized_pnl[symbol] = self._realized_pnl.get(symbol, 0.0) + realized

        if net == 0:
            self._positions.pop(symbol, None)
        else:
            # A fresh open from flat (prev is None) gets a new entry
            # timestamp; a same-direction add or a partial reduction that
            # leaves the position open PRESERVES the original entry
            # timestamp -- this ledger never claims a position was
            # "re-entered" just because its size changed.
            # Preserve the original entry timestamp whenever the position
            # stays open in the SAME direction it was already in (whether
            # added-to or partially reduced); a fresh open from flat, or a
            # flip to the opposite direction in one fill, starts a new one.
            if prev is not None and prev_net != 0 and (net > 0) == (prev_net > 0):
                entry_ts = prev_entry_ts
            else:
                entry_ts = now_ist().isoformat()
            self._positions[symbol] = {
                "symbol": symbol,
                "side": Side.BUY.value if net > 0 else Side.SELL.value,
                "qty": abs(net),
                "avg_price": price,
                "entry_timestamp": entry_ts,
            }

    async def get_order(self, client_order_id: str) -> OrderResult:
        self._check_auth()
        return self._orders.get(
            client_order_id,
            OrderResult(client_order_id, OrderStatus.UNKNOWN, message="not_found"),
        )

    async def cancel_order(self, client_order_id: str) -> OrderResult:
        self._check_auth()
        return OrderResult(client_order_id, OrderStatus.CANCELLED)

    async def get_open_positions(self) -> list[dict]:
        self._check_auth()
        return list(self._positions.values())

    def get_realized_pnl(self, symbol: Optional[str] = None) -> float:
        """Real, already-realized PnL only -- never an estimate, never
        MTM. Per-symbol if `symbol` is given, else the ledger total."""
        if symbol is not None:
            return self._realized_pnl.get(symbol, 0.0)
        return sum(self._realized_pnl.values())

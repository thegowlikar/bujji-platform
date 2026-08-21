"""D-7: tell the paper broker what the market actually looks like.

WHY. PaperBroker already models a real spread: `set_quote` makes a BUY lift
the ask and a SELL hit the bid, and without it every fill falls back to the
ONE reference price the caller passed -- the leg's own premium. In
production nothing ever called it. So a short strangle was entered and
exited at the identical mid, the spread cost nothing, and paper P&L was
optimistic by the full bid-ask on every leg of every trade. Feeding it the
real observed top-of-book is the difference between a simulation of trading
and a simulation of a frictionless market.

THREE DELIBERATE LIMITS, stated rather than papered over:

1. SYMBOL KEYING -- SOLVED AT THE SOURCE (2026-08-21). This module used to
   REBUILD the quote key with the same formula `_leg_to_core_contract` used,
   because orders carried that internal vocabulary while the chain spoke the
   broker's. Two builders, one contract, and a test pinning them together --
   which is a drift alarm, not an absence of drift.

   Orders now carry `chain_row.instrument_symbol` verbatim
   (option_symbol_resolver), so this module writes that same field, also
   verbatim. ONE ORIGIN, reached two ways: the resolver looks it up per
   selected leg, this sweeps the whole chain. Neither constructs, so they
   cannot disagree.

   ELIGIBILITY IS WIDER HERE THAN AT THE RESOLVER, deliberately (operator
   decision (a)). A quote key never leaves the simulation -- it only has to
   match what an order carries against the same PaperBroker -- so
   SOURCE_AUTHORITATIVE rows (NSE bhavcopy `FinInstrmNm`) DO key quotes, and
   a replay keeps paying a real spread. The resolver still refuses that same
   symbol for a real margin call, because nothing has shown FYERS accepts
   it. ABSENT and SYNTHETIC rows are skipped on both sides: keying a quote
   under a sentinel is a silent no-op that still reports coverage.

   COVERAGE IS NOT A SUPERSET, and is not claimed to be. A row needs BOTH a
   bid and an ask to be applied here, while `msi_trade_construction._premium_
   for` will price a leg off settlement or close alone. So a legitimately
   selected leg can be absent from the book. That gap is real, measured
   (`quotes_applied`/`rows_seen`), and surfaced by PaperBroker's
   `quote_lookup_misses` -- never papered over by relaxing this filter.

2. DEPTH IS CONSUMED ONLY WHERE IT IS REAL. `set_depth` wants the real
   top-of-book QUANTITY. The observation schema HAS `bid_quantity`/
   `ask_quantity`, and this module uses them when a row carries them --
   taking the smaller of the two, since PaperBroker holds one number per
   symbol and the conservative side is the honest one. What it will NOT do
   is substitute the chain's cumulative traded `volume`, which is a
   different quantity entirely and would be fabricated depth wearing a real
   number's clothes. As of 2026-08-19 the live chain provider does not
   populate either quantity (verified by reading it, not assumed), so depth
   is absent in practice today and `depth_source` reports exactly that --
   the day the source starts carrying quantities, fills become
   size-sensitive with no further change here.

3. MARGIN PER LOT IS NOT SET. PaperBroker's internal margin check is a
   secondary simulation; `capital_check.assess_capital` (Gate B) is the
   canonical margin VETO authority and already prices the real whole-book
   SPAN requirement. A per-lot figure derived from a whole-book number would
   be an invention wherever the book is not exactly one lot. Equity and
   available margin ARE set, because those are measured facts the capital
   snapshot provider already holds.

Nothing here derives a price, widens a spread, or fills a gap. A row without
a real bid or a real ask contributes nothing and is counted as skipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

from bujji.production_runtime.option_symbol_resolver import quote_sync_eligible


@dataclass(frozen=True)
class QuoteSyncReport:
    """What was actually pushed, and what was not. Every count is a real
    tally -- there is no 'assumed applied'."""

    quotes_applied: int = 0
    rows_seen: int = 0
    skipped_no_bid: int = 0
    skipped_no_ask: int = 0
    skipped_unusable_key: int = 0
    skipped_not_authoritative: int = 0
    depth_applied: int = 0
    depth_source: str = "observation.bid_quantity/ask_quantity (min); absent when the source omits them"
    capital_applied: bool = False
    symbols: Tuple[str, ...] = field(default=())

    def as_dict(self) -> Dict[str, Any]:
        return {
            "quotes_applied": self.quotes_applied, "rows_seen": self.rows_seen,
            "skipped_no_bid": self.skipped_no_bid, "skipped_no_ask": self.skipped_no_ask,
            "skipped_unusable_key": self.skipped_unusable_key,
            "skipped_not_authoritative": self.skipped_not_authoritative,
            "depth_applied": self.depth_applied, "depth_source": self.depth_source,
            "capital_applied": self.capital_applied,
        }


def _positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def sync_quotes_from_chain(broker: Any, chain: Sequence[Any]) -> QuoteSyncReport:
    """Push every REAL observed bid/ask in `chain` into `broker`.

    A row missing either side is skipped and counted, never half-applied:
    a quote with one side present would let one direction cross a real
    price while the other silently fell back to the reference price, which
    is worse than no quote at all because it looks like coverage.
    """
    if not hasattr(broker, "set_quote"):
        return QuoteSyncReport(rows_seen=len(chain))

    applied = depth = 0
    no_bid = no_ask = bad_key = not_authoritative = 0
    symbols = []
    for row in chain:
        if not quote_sync_eligible(row):
            # ABSENT/SYNTHETIC/undeclared. Its symbol is not a name any
            # order will ever look up, so a quote under it is coverage that
            # does not exist. Counted, never silently dropped.
            not_authoritative += 1
            continue
        bid = _positive(getattr(row, "bid", None))
        ask = _positive(getattr(row, "ask", None))
        if bid is None and ask is None:
            no_bid += 1
            no_ask += 1
            continue
        if bid is None:
            no_bid += 1
            continue
        if ask is None:
            no_ask += 1
            continue
        # The row's OWN symbol, verbatim -- the exact string the resolver
        # will hand the order for this same contract. Nothing is built here.
        symbol = getattr(row, "instrument_symbol", None)
        if not symbol or not str(symbol).strip():
            bad_key += 1
            continue
        broker.set_quote(symbol, bid, ask)
        symbols.append(symbol)
        applied += 1

        # Real top-of-book quantity only. Both sides must be present: one
        # side alone describes only one direction of the book, and this
        # broker holds a single depth number per symbol.
        bid_qty = _positive(getattr(row, "bid_quantity", None))
        ask_qty = _positive(getattr(row, "ask_quantity", None))
        if bid_qty is not None and ask_qty is not None and hasattr(broker, "set_depth"):
            broker.set_depth(symbol, int(min(bid_qty, ask_qty)))
            depth += 1

    return QuoteSyncReport(
        quotes_applied=applied, rows_seen=len(chain), skipped_no_bid=no_bid,
        skipped_no_ask=no_ask, skipped_unusable_key=bad_key,
        skipped_not_authoritative=not_authoritative, depth_applied=depth,
        symbols=tuple(symbols),
    )


def sync_capital(broker: Any, capital_snapshot: Any) -> bool:
    """Tell the broker the capital that actually backs this session.

    Only measured fields are pushed. `margin_per_lot` is deliberately left
    alone -- see this module's docstring: Gate B owns real margin, and a
    per-lot figure inferred from a whole-book number would be an invention.
    """
    if capital_snapshot is None or not hasattr(broker, "set_capital"):
        return False
    equity = getattr(capital_snapshot, "total_capital", None)
    available = getattr(capital_snapshot, "available_capital", None)
    if equity is None and available is None:
        return False
    broker.set_capital(account_equity=equity, available_margin=available)
    return True

"""The ELIGIBLE SELECTION BAND -- every contract the selector may choose from.

WHY A SEPARATE SCOPE FROM THE CAPTURE UNIVERSE. The session subscribes a wide
universe (tiers of +/-1500 / +/-1000 / +/-500 index points across three expiry
roles) for audit, replay, context and discovery. That set is deliberately wider
than trading alone justifies -- it is drawn on OPEN INTEREST, and
`capture_universe.builder`'s own measurement records six of eighteen expiries
trading zero contracts all day. Requiring a fresh tick from every one of those
would refuse to trade every day, and a naturally inactive far-out contract
proves nothing about the feed.

What the entry decision actually rests on is narrower: the contracts the
selector RANKS. `msi_trade_construction.engine._build_strike_evidence` keeps
every chain row at the chosen expiry, and `_candidates_for_type` ranks those
whose Black-Scholes delta solved. A stale price anywhere in that set corrupts
the CHOICE even when the stale contract is not the one chosen -- which is why
this is the band and not merely the legs.

CAPTURE WIDE, REQUIRE NARROW.

THE EXPIRY IS NOT RE-DERIVED HERE. `select_expiry` is imported from the
construction engine and called with the same arguments the engine uses, so the
band cannot drift from the set the selector will actually range over. A gate
that computes its own answer to a question the code already answers elsewhere
is a gate that will eventually disagree with it.

BROKER-REAL SYMBOLS ONLY. Coverage is proven by matching a symbol against the
websocket feed's per-symbol tick ages, so the band's symbols must be the
BROKER's own strings. `LiveChainProvider` declares
SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE and refuses to fabricate a symbol when
FYERS omits one. Other providers do not: `store_chain_provider` declares
ABSENT, and a bhavcopy symbol is SOURCE_AUTHORITATIVE -- real at NSE and not a
FYERS subscription string. Matching either against tick ages would silently
report every symbol as never-requested and block forever, so anything that is
not broker-authoritative is refused LOUDLY here instead.

PURE. No I/O, no clock, no broker, no feed. Handed a chain, it returns the set.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

BAND_OK = "OK"
BAND_NO_EXPIRY = "NO_EXPIRY_IN_DTE_WINDOW"
BAND_NO_ROWS = "NO_ROWS_AT_CHOSEN_EXPIRY"
BAND_NOT_BROKER_REAL = "SYMBOLS_NOT_BROKER_REAL"

# Only this provenance yields a string the tick feed can be asked about.
_USABLE_PROVENANCE = "BROKER_AUTHORITATIVE"


@dataclass(frozen=True)
class SelectionBand:
    state: str
    expiry: Optional[str]
    symbols: Tuple[str, ...]
    rows_considered: int
    detail: str

    @property
    def usable(self) -> bool:
        return self.state == BAND_OK and bool(self.symbols)

    def as_dict(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "expiry": self.expiry,
            "symbols": len(self.symbols),
            "rows_considered": self.rows_considered,
            "detail": self.detail,
            # Bounded on purpose: the band is ~82 contracts today, but
            # `strike_count` is configurable and this payload is written to
            # every session artifact.
            "symbols_sample": list(self.symbols[:10]),
        }


def selection_band(chain: Sequence, as_of_date: str, *,
                   min_dte: Optional[int] = None,
                   max_dte: Optional[int] = None) -> SelectionBand:
    """Every contract the selector may range over, from the chain it will use.

    Never raises: a band that cannot be determined is returned as an
    un-usable state with a reason, because the caller must BLOCK on that
    rather than receive an exception it might catch into a pass.
    """
    # Local import: the engine owns the expiry decision and this module must
    # not become a second opinion on it. Deferred so importing this module
    # never drags the construction engine into unrelated callers.
    from bujji.msi_trade_construction import config as _mtc_config
    from bujji.msi_trade_construction.engine import select_expiry

    if min_dte is None:
        min_dte = _mtc_config.DEFAULT_MIN_DTE
    if max_dte is None:
        max_dte = _mtc_config.DEFAULT_MAX_DTE

    rows = tuple(chain or ())
    # `select_expiry` reads `row.expiry` directly and raises on a row that has
    # none. This module promises never to raise -- a caller that could catch an
    # exception is a caller that could catch it into a pass -- so a malformed
    # chain becomes an un-usable band with a reason instead. Found by its own
    # test, which fed the function an object with no attributes at all.
    try:
        decision = select_expiry(rows, as_of_date, min_dte=min_dte, max_dte=max_dte)
    except Exception as exc:  # noqa: BLE001 -- see above
        return SelectionBand(
            state=BAND_NO_EXPIRY, expiry=None, symbols=(), rows_considered=len(rows),
            detail=(f"the chain could not be read for its expiries "
                    f"({type(exc).__name__}: {exc}) -- the eligible band is UNKNOWN"),
        )
    chosen = getattr(decision, "chosen_expiry", None)
    if chosen is None:
        return SelectionBand(
            state=BAND_NO_EXPIRY, expiry=None, symbols=(), rows_considered=len(rows),
            detail=("no expiry survived the configured DTE window, so the selector "
                    "has nothing to range over: " + "; ".join(
                        getattr(decision, "reasoning", ()) or ("no reason given",))),
        )

    # The SAME row filter `_build_strike_evidence` applies. Anything it would
    # skip cannot be selected, so it is not part of the band.
    in_band = [r for r in rows
               if getattr(r, "expiry", None) == chosen
               and getattr(r, "strike", None) is not None
               and getattr(r, "option_type", None) in ("CE", "PE")]
    if not in_band:
        return SelectionBand(
            state=BAND_NO_ROWS, expiry=str(chosen), symbols=(), rows_considered=len(rows),
            detail=f"the chain carries no CE/PE rows at the chosen expiry {chosen}",
        )

    symbols, not_real = [], []
    for row in in_band:
        provenance = getattr(row, "symbol_provenance", None)
        symbol = getattr(row, "instrument_symbol", None)
        if provenance != _USABLE_PROVENANCE or not symbol:
            not_real.append(f"{getattr(row, 'option_type', '?')}"
                            f"{getattr(row, 'strike', '?')}:{provenance}")
            continue
        symbols.append(str(symbol))

    if not_real:
        # LOUD, not silent. Matching a non-broker symbol against tick ages
        # reports "never requested" for every one of them, which is
        # indistinguishable from an unsubscribed universe and would block the
        # session with a misleading reason.
        return SelectionBand(
            state=BAND_NOT_BROKER_REAL, expiry=str(chosen), symbols=(),
            rows_considered=len(rows),
            detail=(f"{len(not_real)} of {len(in_band)} band rows do not carry a "
                    f"broker-authoritative symbol (e.g. {', '.join(not_real[:5])}) -- "
                    f"coverage cannot be proven against the feed for this chain source"),
        )

    ordered = tuple(dict.fromkeys(symbols))
    return SelectionBand(
        state=BAND_OK, expiry=str(chosen), symbols=ordered, rows_considered=len(rows),
        detail=(f"{len(ordered)} contracts at expiry {chosen} are eligible for "
                f"selection and must all be fresh before a strike is chosen"),
    )

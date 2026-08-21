"""The one place a broker option symbol is obtained. It is never built.

WHY THIS MODULE EXISTS. Two vocabularies described the same contract. The
chain spoke the broker's ("NSE:NIFTY2681824100CE"); everything downstream
rebuilt its own from the strategy leg's expiry/strike/type
("NIFTY2026-08-2524500CE"). Measured live on 2026-08-20:

    'NIFTY2026-08-2524500CE'  -> verified=False  total_margin=None
    'NSE:NIFTY26AUG22700PE'   -> verified=True   total_margin=98915.87

The API was never broken. Every entry that session was blocked by a string
format, and the same split silently made every paper fill frictionless,
because the quote book was keyed by one vocabulary and orders looked up the
other.

The cure is not a translator. A translator is a second string-builder, which
is a second thing to drift. The cure is that the symbol has exactly ONE
origin -- `chain_row.instrument_symbol` -- and everything that needs it
LOOKS IT UP. This module does the looking up, and it does nothing else: it
cannot construct, prefix, strip, reformat, round, coerce, or translate. If
it cannot prove which row a leg means, it refuses.

PROVENANCE, NOT SHAPE. A well-formed fake is still a fake, so nothing here
inspects the string. Eligibility is decided by `symbol_provenance`, recorded
at construction time (commit 39b1ca9).

WHAT IS REFUSED, AND WHY EXACTLY THIS LINE. ABSENT and SYNTHETIC name
nothing real -- an ABSENT row carries the `UNRESOLVED|...` sentinel because
the model cannot hold an empty instrument, and SYNTHETIC is a string someone
built. Neither can be traded and neither can key a quote, so both are
refused everywhere, permanently. A row that declares no provenance at all is
refused with them: authority is never granted by omission.

SOURCE_AUTHORITATIVE IS PERMITTED HERE, and that is a deliberate boundary,
not a concession. NSE's `FinInstrmNm` is a real observed identity; what is
unproven is whether the FYERS execution venue accepts it. That question only
matters when a symbol actually LEAVES for a real venue, and exactly one
production path does: Gate B's SPAN call under a `fyers_*` margin provider.
That path is closed at the source -- `_guard_provider_vocabulary` in the
runner refuses to START a session pairing a non-broker chain
(replay_chain / observation_store) with a real margin provider, which is
where the exposure actually lived: `providers.market_data` and
`providers.margin` were selected independently, and
config/options_os_shadow.yaml shipped `replay_chain` + `fyers_certified`.

Refusing SOURCE_AUTHORITATIVE here as well would be redundant on that path
and destructive everywhere else: thirty test files, and every backtest, build
their chains from a real NSE bhavcopy, so a bhavcopy chain that cannot
construct an order is a replay engine that cannot replay. The alternative --
re-stamping those real NSE rows as BROKER_AUTHORITATIVE -- would assert that
FYERS returned strings FYERS never returned, which is the precise
fabrication this whole line of work exists to remove. Operator decision,
2026-08-21.

THE KEY IS (expiry, strike, option_type), AND IT IS LOSSLESS. Proven
against the codebase rather than assumed:
  * `msi_trade_construction._build_strike_evidence` stores
    `_StrikeEvidence(strike=row.strike, ...)` -- the SAME float object --
    and `_leg()` passes `evidence.strike` straight to `leg.strike`. So
    `leg.strike is row.strike`. Every `int(strike)` in that module is
    inside an f-string for a log line.
  * `leg.expiry` traces to `sorted({row.expiry for row in chain})`, byte
    identical; that module already compares expiries with `!=`.
  * `option_type` is filtered to exactly ("CE", "PE") at ingest.
All normalisation happens at the PROVIDER, upstream of the chain. The chain
row IS the normalised form, so this module converts nothing -- and a `str`
strike is a malformed input, not something to call float() on.

EXPIRY IS IN THE KEY, and that is not defensive. A CALENDAR
(msi_trade_construction.py:474-487) emits two legs at the SAME strike, both
CE, differing only by expiry. Keyed on (strike, type) they collapse to one
entry and the margin call prices two legs of one contract. Today that is
latent only because LiveChainProvider reads `expiryData[0]` -- a single
expiry per chain.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence, Tuple

from bujji.options_observation import taxonomy as opt_taxonomy

# Provenances that name a REAL contract at some venue. ABSENT and SYNTHETIC
# name nothing, so they are excluded here and everywhere -- see the module
# docstring for why SOURCE_AUTHORITATIVE is inside this line and what closes
# the real-venue path instead.
USABLE_PROVENANCES = (
    opt_taxonomy.SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE,
    opt_taxonomy.SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE,
)

MISSING_EXPIRY = "MISSING_EXPIRY"
MISSING_STRIKE = "MISSING_STRIKE"
MISSING_OPTION_TYPE = "MISSING_OPTION_TYPE"
MALFORMED_STRIKE = "MALFORMED_STRIKE"
INVALID_OPTION_TYPE = "INVALID_OPTION_TYPE"
NO_MATCH = "NO_MATCH"
EXPIRY_MISMATCH = "EXPIRY_MISMATCH"
DUPLICATE_CONTRACT = "DUPLICATE_CONTRACT"
SYMBOL_ABSENT = "SYMBOL_ABSENT"
SYMBOL_NOT_AUTHORITATIVE = "SYMBOL_NOT_AUTHORITATIVE"


class OptionSymbolUnresolvable(LookupError):
    """No broker symbol could be PROVEN for this leg.

    LookupError on purpose: Gate B already catches Exception around the
    margin build and VETOes, so this fails closed there without a new
    branch. `reason` is a stable string for tests and log greps; the
    message carries the diagnosis a human needs at 09:20.
    """

    def __init__(self, reason: str, message: str,
                 expiry: Any = None, strike: Any = None, option_type: Any = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.expiry = expiry
        self.strike = strike
        self.option_type = option_type


def _numeric_strike(strike: Any) -> float:
    """The strike as a float, or a refusal. NEVER a conversion.

    `float("24050")` succeeding is exactly the silent coercion this whole
    change exists to remove: it would let a differently-typed strike match a
    row it was never proven to be. bool is excluded because `isinstance(True,
    int)` is True and `True == 1.0` -- a boolean must never index a strike.
    """
    if strike is None:
        raise OptionSymbolUnresolvable(
            MISSING_STRIKE, "no strike on the leg -- cannot identify a contract")
    if isinstance(strike, bool) or not isinstance(strike, (int, float)):
        raise OptionSymbolUnresolvable(
            MALFORMED_STRIKE,
            f"strike {strike!r} is {type(strike).__name__}, not a number -- refusing to "
            f"coerce it: a coerced strike can match a row it was never proven to be",
            strike=strike)
    value = float(strike)
    if math.isnan(value) or math.isinf(value):
        raise OptionSymbolUnresolvable(
            MALFORMED_STRIKE, f"strike {strike!r} is not a finite number", strike=strike)
    return value


def _validated_key(expiry: Any, strike: Any, option_type: Any) -> Tuple[str, float, str]:
    if expiry is None or expiry == "":
        raise OptionSymbolUnresolvable(
            MISSING_EXPIRY, "no expiry on the leg -- a strike alone is not a contract")
    if option_type is None or option_type == "":
        raise OptionSymbolUnresolvable(
            MISSING_OPTION_TYPE, "no option type on the leg -- CE and PE are different contracts")
    if option_type not in opt_taxonomy.ALL_OPTION_TYPES:
        raise OptionSymbolUnresolvable(
            INVALID_OPTION_TYPE,
            f"option type {option_type!r} is not one of {opt_taxonomy.ALL_OPTION_TYPES}",
            option_type=option_type)
    return (expiry, _numeric_strike(strike), option_type)


class SymbolIndex:
    """One pass over one chain, then O(1) lookups against it.

    Built once per entry cycle and shared by the margin gate and order
    construction, so those two CANNOT resolve differently: same object, same
    index, same key, same row, same string.

    Nothing is filtered away silently. Rows that cannot be keyed, or whose
    provenance bars them, are counted in `rejected` -- a chain whose rows
    were all quietly dropped must never look like an empty chain.
    """

    __slots__ = ("_by_key", "_duplicates", "_by_strike_type", "rejected")

    def __init__(self, chain: Sequence[Any]) -> None:
        self._by_key: Dict[Tuple[str, float, str], Any] = {}
        self._duplicates: Dict[Tuple[str, float, str], int] = {}
        self._by_strike_type: Dict[Tuple[float, str], set] = {}
        self.rejected: Dict[str, int] = {}
        for row in chain or ():
            self._add(row)

    def _reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    def _add(self, row: Any) -> None:
        expiry = getattr(row, "expiry", None)
        option_type = getattr(row, "option_type", None)
        try:
            key = _validated_key(expiry, getattr(row, "strike", None), option_type)
        except OptionSymbolUnresolvable as exc:
            self._reject(exc.reason)
            return
        # Recorded BEFORE the provenance/symbol checks: a wrong-expiry
        # diagnosis must still be possible for a row that exists but is
        # ineligible, otherwise the operator is told "no match" when the
        # contract is right there under another expiry.
        self._by_strike_type.setdefault((key[1], key[2]), set()).add(key[0])
        if key in self._by_key or key in self._duplicates:
            # Two rows for one contract means the CHAIN is wrong. Keep both
            # out: resolving to either -- even when they agree -- would hide
            # a provider defect behind a plausible answer.
            self._by_key.pop(key, None)
            self._duplicates[key] = self._duplicates.get(key, 1) + 1
            self._reject(DUPLICATE_CONTRACT)
            return
        self._by_key[key] = row

    def resolve(self, expiry: Any, strike: Any, option_type: Any) -> str:
        """The broker's own symbol for this contract, verbatim, or a refusal."""
        key = _validated_key(expiry, strike, option_type)
        if key in self._duplicates:
            row_count = self._duplicates[key]
            raise OptionSymbolUnresolvable(
                DUPLICATE_CONTRACT,
                f"{row_count} chain rows claim {option_type} {key[1]:g} expiry {expiry} -- "
                f"refusing to choose between them (a chain with two rows for one contract "
                f"is malformed; picking either would hide that)",
                expiry=expiry, strike=strike, option_type=option_type)
        row = self._by_key.get(key)
        if row is None:
            other = sorted(self._by_strike_type.get((key[1], key[2]), set()))
            if other:
                raise OptionSymbolUnresolvable(
                    EXPIRY_MISMATCH,
                    f"no {option_type} {key[1]:g} at expiry {expiry!r}; the chain carries "
                    f"that strike at {other} -- the leg and the chain disagree about WHICH "
                    f"expiry is being traded",
                    expiry=expiry, strike=strike, option_type=option_type)
            raise OptionSymbolUnresolvable(
                NO_MATCH,
                f"no chain row for {option_type} {key[1]:g} expiry {expiry!r} "
                f"({len(self._by_key)} contracts indexed, rejected={self.rejected or 'none'})",
                expiry=expiry, strike=strike, option_type=option_type)

        provenance = getattr(row, "symbol_provenance", None)
        if provenance not in USABLE_PROVENANCES:
            # Includes provenance=None. Authority is never granted by
            # omission -- a row that does not say where its symbol came from
            # has not earned the benefit of the doubt.
            raise OptionSymbolUnresolvable(
                SYMBOL_NOT_AUTHORITATIVE,
                f"{option_type} {key[1]:g} expiry {expiry} carries symbol_provenance="
                f"{provenance!r}, which names no real contract at any venue "
                f"(ABSENT = the capture never recorded a symbol; SYNTHETIC = one was "
                f"manufactured). Usable: {USABLE_PROVENANCES}.",
                expiry=expiry, strike=strike, option_type=option_type)

        symbol = getattr(row, "instrument_symbol", None)
        if not symbol or not str(symbol).strip():
            raise OptionSymbolUnresolvable(
                SYMBOL_ABSENT,
                f"{option_type} {key[1]:g} expiry {expiry} declares {provenance} "
                f"but carries no symbol",
                expiry=expiry, strike=strike, option_type=option_type)
        # Verbatim. No prefix added, none stripped, no case change, no
        # reformatting -- whatever the venue said, character for character.
        return symbol

    def resolve_leg(self, leg: Any) -> str:
        return self.resolve(getattr(leg, "expiry", None), getattr(leg, "strike", None),
                            getattr(leg, "option_type", None))

    def __len__(self) -> int:
        return len(self._by_key)


def build_symbol_index(chain: Sequence[Any]) -> SymbolIndex:
    """Index `chain` once. Share the result across every consumer of that
    same chain -- that sharing is what makes margin and order symbols
    provably equal rather than coincidentally equal."""
    return SymbolIndex(chain)


def quote_sync_eligible(row: Any) -> bool:
    """May this row's symbol key the PaperBroker quote book?

    THE SAME SET `resolve()` accepts, and deliberately ONE predicate rather
    than two that happen to agree -- two would be two things to drift, which
    is the entire defect class this module exists to end. An order and its
    quote must be keyed by the same string, so anything orderable must be
    quotable and vice versa.

    A row keyed under a sentinel would be a silent no-op that still reports
    coverage, which is why ABSENT and SYNTHETIC are excluded here too.
    """
    return getattr(row, "symbol_provenance", None) in USABLE_PROVENANCES

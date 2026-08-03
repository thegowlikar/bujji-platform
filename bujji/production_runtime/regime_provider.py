"""Regime Provider -- Bujji Options OS, Phase-1 Runner Bridge.

PURPOSE: the ONE boundary between "where the regime classification
comes from" and TradingSessionGovernor.select_and_lock_strategy(),
which only ever consumes a (trend_regime, volatility_regime) string
pair. This is what makes a future MIC/regime-classifier swap a
provider-only change, never a change to strategy_selector.py or
session_governor.py.

NO INTELLIGENCE HERE, DELIBERATELY: this module does not classify
anything from market data itself -- Phase-1 ships exactly one concrete
provider, `HumanSuppliedRegimeProvider`, which holds whatever values
an operator explicitly supplied (via config or CLI) and returns them
verbatim. A real classifier (MIC) is explicitly Category C future
work, not attempted here.

FAIL CLOSED, NEVER GUESS: if a regime value is missing, this module
raises rather than defaulting to "NEUTRAL"/"NORMAL" or any other
invented value. `strategy_selector.select_strategy()` already treats
`None` as "fail closed to NO_TRADE" -- but that fallback exists for a
genuinely unclassifiable market, not as a substitute for an operator
never having supplied an answer. This provider makes the difference
observable: a missing INPUT is an error (raised here, before the
Governor is even asked), a genuinely UNKNOWN market condition is a
valid, allowed None value the Governor already knows how to handle.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Tuple


class MissingRegimeInputError(Exception):
    """Raised when a regime provider has no value to return -- never
    silently substituted with a guessed regime."""


class RegimeProvider(ABC):
    """Downstream code (the runner, TradingSessionGovernor.select_and_
    lock_strategy) depends on this abstraction only, never on a
    concrete source."""

    @abstractmethod
    def get_trend_regime(self) -> Optional[str]:
        """One of market_regime_adapter's own TREND_* constants, or
        raises MissingRegimeInputError -- never a value this provider
        invented itself."""

    @abstractmethod
    def get_volatility_regime(self) -> Optional[str]:
        """One of market_regime_adapter's own VOL_* constants, or
        raises MissingRegimeInputError -- never a value this provider
        invented itself."""

    def get_regime(self) -> Tuple[Optional[str], Optional[str]]:
        """Convenience: both values together, same fail-closed contract
        as the two methods above (raises before returning a partial
        pair)."""
        return self.get_trend_regime(), self.get_volatility_regime()


class HumanSuppliedRegimeProvider(RegimeProvider):
    """Phase-1's only concrete RegimeProvider. Holds exactly the values
    an operator explicitly supplied at construction time (from
    options_os_shadow.yaml or a CLI override) -- never derives, never
    infers, never defaults. Passing None for either value is legal at
    construction (the operator may genuinely not have an answer yet),
    but calling get_trend_regime()/get_volatility_regime() on a None
    value raises immediately -- the missing input surfaces as an error
    at the point of use, not as a silently-accepted "NEUTRAL"/"NORMAL"
    guess three layers downstream."""

    def __init__(self, trend_regime: Optional[str] = None, volatility_regime: Optional[str] = None) -> None:
        self._trend_regime = trend_regime
        self._volatility_regime = volatility_regime

    def get_trend_regime(self) -> Optional[str]:
        if self._trend_regime is None:
            raise MissingRegimeInputError(
                "trend_regime was never supplied -- refusing to guess. Set it explicitly in "
                "options_os_shadow.yaml (regime.trend_regime) or via --trend-regime before "
                "starting the ENTRY_WINDOW stage."
            )
        return self._trend_regime

    def get_volatility_regime(self) -> Optional[str]:
        if self._volatility_regime is None:
            raise MissingRegimeInputError(
                "volatility_regime was never supplied -- refusing to guess. Set it explicitly in "
                "options_os_shadow.yaml (regime.volatility_regime) or via --volatility-regime "
                "before starting the ENTRY_WINDOW stage."
            )
        return self._volatility_regime

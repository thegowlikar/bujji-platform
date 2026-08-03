"""Shadow Session Configuration -- Shadow Runtime, Phase-5.1.

The minimal operator-facing configuration model for a shadow
observation session. Contains ONLY observation-related fields --
which contracts to watch, how often to poll, the market window, and
where to write artifacts. Nothing about strategy, signal, entry, exit,
trade, position, order, quantity, size, risk, capital, margin, P&L,
approval, execution, or broker action belongs here, or ever will --
this is a domain model for "what to observe," never "what to do."

NO PARSING INFRASTRUCTURE: this module defines the domain model only.
Whether a future operator layer loads this from YAML, JSON, a CLI, or
a UI is deliberately undecided here -- out of this phase's scope.

REUSES EXISTING MODELS: `contracts` holds real `bujji.core.models.
OptionContract` instances (with their own real `OptionType`) --
nothing here reimplements or wraps that shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from bujji.core.models import OptionContract


class InvalidShadowSessionConfigError(Exception):
    """Raised on any structurally invalid configuration -- never
    silently coerced or defaulted."""


@dataclass(frozen=True)
class ShadowSessionConfig:
    session_id: str
    contracts: Tuple[OptionContract, ...]
    poll_interval_seconds: int
    market_start: str    # e.g. "09:15" or a full ISO timestamp -- must be lexicographically
    market_end: str      # comparable to market_start (zero-padded, consistent format); this
                          # module does no time parsing, only a structural ordering check
    artifact_path: str

    def __post_init__(self) -> None:
        if not self.session_id:
            raise InvalidShadowSessionConfigError("session_id must not be empty")
        if not self.contracts:
            raise InvalidShadowSessionConfigError("contracts must not be empty -- nothing to observe")
        if self.poll_interval_seconds <= 0:
            raise InvalidShadowSessionConfigError(
                f"poll_interval_seconds must be positive, got {self.poll_interval_seconds!r}"
            )
        if not self.market_start or not self.market_end:
            raise InvalidShadowSessionConfigError("market_start and market_end must both be supplied")
        if self.market_start >= self.market_end:
            raise InvalidShadowSessionConfigError(
                f"market_start ({self.market_start!r}) must be before market_end ({self.market_end!r})"
            )
        if not self.artifact_path:
            raise InvalidShadowSessionConfigError("artifact_path must not be empty")

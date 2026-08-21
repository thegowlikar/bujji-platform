"""Execution Reality Profiles -- Phase 20.2.

Bundles the already-existing, unmodified Gate F.2 config dataclasses
(SlippageConfig, LatencyConfig, RejectionConfig, ChargesConfig) into
three named, opt-in profiles -- NORMAL, STRESS, EXTREME -- for the
Phase 20.2 backtest driver and for any PaperBroker instantiation that
explicitly wants non-default execution realism.

Nothing here changes PaperBroker's own default construction
(`PaperBroker()`, no args) -- these profiles are consumed only by code
that explicitly imports and passes them.

See docs/PHASE_20_2_EXECUTION_REALITY_REPORT.md.
"""
from .profiles import ExecutionProfile, EXTREME, NORMAL, STRESS, get_profile

__all__ = ["ExecutionProfile", "NORMAL", "STRESS", "EXTREME", "get_profile"]

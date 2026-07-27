"""Capital Management Engine — exceptions.

Every failure mode here is a signal to REFUSE the trade, never a signal to
guess or fall back to a default quantity. The engine's `approve_trade()`
catches all of these internally and turns them into a BLOCKED
SizingDecision with an explicit reason — nothing upstream (Orchestrator,
strategy code) ever needs to catch these directly, but they exist as
distinct types so tests can assert exactly which failure mode fired.
"""
from __future__ import annotations


class CapitalUnverifiedError(RuntimeError):
    """Raised when the broker's funds/margin response cannot be trusted —
    missing, malformed, or a required field absent. Never caught silently
    into a guessed value; always surfaces as CAPITAL STATUS: UNVERIFIED."""


class MarginCalculatorUnavailableError(RuntimeError):
    """Raised when no broker-verified margin-per-straddle figure can be
    obtained — e.g. the broker adapter has no certified margin-calculator
    implementation (see broker_adapter.py's FyersMarginAdapter, which raises
    this deliberately: the official fyers-apiv3 SDK, as installed and
    introspected in this codebase, exposes no margin-calculator endpoint —
    LIVE CERTIFICATION REQUIRED before this can be closed)."""


class CapitalRejectedError(RuntimeError):
    """Raised by the Orchestrator's entry path (not the engine itself --
    approve_trade() never raises) when the Capital Management Engine
    returns a non-approved SizingDecision. A dedicated type so the entry
    error-handling path can roll back to READY with a clear, distinct
    reason instead of folding into the generic contract-resolution
    catch-all."""


class BrokerCapitalQueryError(RuntimeError):
    """Raised when the underlying broker call itself fails (timeout,
    disconnect, auth failure surfaced as a generic error at this layer,
    malformed HTTP response). Distinct from CapitalUnverifiedError (which
    means "the broker answered, but we can't trust the answer") — this
    means "the broker didn't answer at all." Both lead to the same outcome:
    BLOCKED, never a guess.
    """

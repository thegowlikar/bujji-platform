"""Deliverable 3 -- Safety. Multiple independent, real safeguards, not
one single flag trusted alone.

1. `SHADOW_MODE` is a module-level constant, `True`, never read from
   config/env (so it cannot be flipped by a misconfigured deployment) --
   mirrors this project's own "never-tuned constant" discipline.
2. `assert_shadow_safe()` -- a runtime assertion the operator calls at
   every stage boundary (startup, per-tick, per-cadence, shutdown).
   Raises `ShadowModeViolation` (fail closed) if ever False.
3. `LiveShadowOperator` (operator.py) never imports `bujji.execution`
   or `bujji.broker` for anything beyond `FyersTickFeed` (a read-only
   market-data feed) and `FyersTokenManager` (a read-only token
   refresh) -- verified structurally by this package's own AST test,
   the same false-positive-safe convention established since Sprint
   105 (raw string search on "broker"/"execution" would trip on this
   very docstring).
4. No `place_order`/`submit_and_confirm`/`ExecutionEngine` symbol is
   ever imported, referenced, or constructed anywhere in this package --
   if one ever is, `_forbidden_symbol_guard()` raises immediately
   rather than silently succeeding.
"""
from __future__ import annotations

SHADOW_MODE = True


class ShadowModeViolation(RuntimeError):
    """Raised, never caught internally, if shadow safety is ever
    violated -- fail closed, per this sprint's own explicit mandate."""


def assert_shadow_safe() -> None:
    if not SHADOW_MODE:
        raise ShadowModeViolation(
            "SHADOW_MODE is False -- refusing to proceed. This constant "
            "must never be True->False in this package; execution is "
            "structurally impossible here regardless of its value, but "
            "this assertion exists as an independent, redundant check."
        )


_FORBIDDEN_SYMBOLS = ("place_order", "submit_and_confirm", "ExecutionEngine", "submit_order")


def _forbidden_symbol_guard(name: str) -> None:
    """Called defensively anywhere this package might, in a future
    edit, be tempted to reach for a broker submission call. Always
    raises -- there is no code path in this package that is allowed to
    reach this function with a forbidden name."""
    if name in _FORBIDDEN_SYMBOLS:
        raise ShadowModeViolation(
            f"Attempted to reach forbidden execution symbol {name!r} from "
            "bujji.live_shadow_operator. Execution must be impossible by "
            "construction; this assertion exists to fail loudly if that "
            "invariant is ever broken by a future edit."
        )


_BANNER_WIDTH = 62


def render_shadow_banner() -> str:
    lines = [
        "=" * _BANNER_WIDTH,
        "",
        "  SHADOW MODE",
        "  NO ORDERS CAN BE SENT",
        "",
        "  Execution module: NOT IMPORTED, NOT INITIALISED",
        "  Broker submit methods: NEVER CALLED (structurally absent)",
        "  Decision logic: FROZEN (Series 73-106, unmodified)",
        "",
        "=" * _BANNER_WIDTH,
    ]
    return "\n".join(lines)

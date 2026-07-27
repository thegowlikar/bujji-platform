"""Execution Planning Engine config — Series 98.

Deliverable 3 sequencing philosophy, declarative, keyed by Series 90's
own real `strategy_family` (never a re-derived construction concept):
the leg that DEFINES or LIMITS the position's risk is staged first;
legs that ADD exposure or REDUCE net cost are staged only after the
risk-defining stage is confirmed filled. This single rule reproduces
BOTH of the spec's own worked examples exactly:
  - Debit Spread: "Buy Leg -> Verify Fill -> Sell Leg" (the long leg
    defines/caps risk immediately; the short leg only reduces cost).
  - Iron Condor: "Short Call + Short Put -> Verify -> Long Wings ->
    Verify Complete Position" (the short legs collect the credit that
    funds the protective wings, staged second here since the wings
    only CAP an already-accepted risk, they don't newly define it).

Each entry is an ordered tuple of ROLE FILTERS; legs are grouped into
stages by matching (side | option_type) exactly as Series 90 already
stores them on each `StrikeLeg` -- no new leg classification is
introduced.
"""
from __future__ import annotations

from . import taxonomy as t

STAGE_RULES = {
    "LONG_DIRECTIONAL": (("ANY",),),
    "SHORT_DIRECTIONAL": (("ANY",),),
    "NEUTRAL_PREMIUM_SELLING": (("CE",), ("PE",)),
    "NEUTRAL_PREMIUM_BUYING": (("CE",), ("PE",)),
    "VOLATILITY_EXPANSION": (("CE",), ("PE",)),
    "VOLATILITY_COMPRESSION": (("CE",), ("PE",)),
    "IRON_CONDOR": (("SELL",), ("BUY",)),
    "IRON_FLY": (("SELL",), ("BUY",)),
    "BUTTERFLY": (("BUY",), ("SELL",)),
    "RATIO": (("BUY",), ("SELL",)),
    "COVERED": (("ANY",),),
    "SYNTHETIC": (("BUY",), ("SELL",)),
    "CALENDAR": (("BUY",), ("SELL",)),
}

# --- Deliverable 4: failure -> deterministic PLANNED response (never
# executed). Each response is modeled directly on the REAL, confirmed
# behavior of `bujji.execution.engine.ExecutionEngine` where a
# real precedent exists (disclosed per-item below); genuinely NEW,
# conservative defaults are used only where no such precedent exists
# in this codebase (also disclosed). --------------------------------------
FAILURE_RESPONSES = {
    t.FAILURE_PARTIAL_FILL: (
        "accept the partial fill actually achieved (never raise on a non-zero fill); "
        "cancel remaining dependent stages; re-evaluate the position at its actual "
        "filled size before any further action",
        "REUSED PRECEDENT: matches ExecutionEngine.submit_and_confirm's own real "
        "discipline exactly -- only a ZERO fill is ever treated as a failure",
    ),
    t.FAILURE_REJECTED_ORDER: (
        "abort all remaining stages in this plan; do not retry with the same or "
        "different parameters; escalate to re-evaluation before any resubmission",
        "REUSED PRECEDENT: matches ExecutionEngine._place_idempotent's own real "
        "discipline of verifying rather than blindly re-placing on an ambiguous outcome",
    ),
    t.FAILURE_TIMEOUT: (
        "cancel the unfilled remainder of the timed-out order (never let a late fill "
        "silently increase exposure); treat the position as partially established at "
        "its actual filled size; re-evaluate before continuing to the next stage",
        "REUSED PRECEDENT: matches ExecutionEngine._await_fill's own real "
        "timeout-then-cancel-remainder behavior exactly",
    ),
    t.FAILURE_EXCHANGE_HALT: (
        "abort the entire plan; do not assume any not-yet-verified leg is safe; "
        "escalate to manual review before any resumption",
        "NEW, conservative default -- no exchange-halt handling exists anywhere in "
        "this codebase today; disclosed as new, not reused",
    ),
    t.FAILURE_BROKER_DISCONNECT: (
        "on reconnect, reconcile real broker-reported positions before resuming or "
        "assuming any prior stage's last-known state is still accurate",
        "REUSED PRECEDENT: matches the CONCEPT of ExecutionEngine.reconcile() "
        "(fetch live broker positions to detect drift) -- not its code, which is "
        "live-only and cannot run in this deterministic package",
    ),
    t.FAILURE_STALE_QUOTES: (
        "abort the stage that would have used the stale quote; do not place an order "
        "against a quote that cannot be freshness-verified",
        "NEW -- disclosed, motivated directly by Series 90/91's own real finding that "
        "bid/ask is 100% absent from historical Bhavcopy data",
    ),
    t.FAILURE_PRICE_DRIFT: (
        "re-validate the expected credit/debit against a configured tolerance before "
        "continuing to the next stage; abort the remaining stages if drift exceeds it",
        "NEW -- a disclosed, structural tolerance, never fit to replay P&L",
    ),
    t.FAILURE_CANCELLED_ORDER: (
        "treat identically to a rejected order: abort remaining stages, escalate to "
        "re-evaluation, never silently resubmit",
        "REUSED PRECEDENT: same discipline as FAILURE_REJECTED_ORDER above",
    ),
}

# --- Structural, disclosed tolerance for FAILURE_PRICE_DRIFT (never fit to
# replay P&L) --------------------------------------------------------------
PRICE_DRIFT_TOLERANCE_PCT = 0.10  # 10% of the originally expected credit/debit.

# --- Estimated latency, a qualitative label keyed on stage count only
# (no real network latency data exists anywhere in this replay arc). ------
LATENCY_BY_STAGE_COUNT = {1: t.LATENCY_LOW, 2: t.LATENCY_MODERATE}
LATENCY_DEFAULT_FOR_MORE_STAGES = t.LATENCY_HIGH

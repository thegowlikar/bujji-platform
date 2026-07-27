"""Capital Management Engine — broker adapter.

Translates a Broker's raw, normalized funds/margin dicts into the engine's
typed models, and is the SINGLE place that decides whether a broker
response can be trusted. A broker implementation returns a plain dict (or
None); this module is the only code that turns "missing key" into "treat
this as unverified" — the sizing algorithm in engine.py never has to know
about broker response shapes at all.

Contract every Broker implementation must honor for `get_funds()`:
    Returns a dict with any subset of these keys (a missing key means that
    particular figure could not be verified — never omit a key AND supply
    a guessed value under it):
        account_equity, available_funds, available_margin, cash_balance,
        collateral, used_margin, available_exposure, peak_margin
    Returns None if funds could not be fetched at all (broker error/timeout
    is the caller's job to raise before this; None here specifically means
    "the call succeeded but returned nothing usable").

Contract for `get_order_margin(legs)`:
    Returns {"margin_per_lot": float, "verified": bool, "source": str} or
    None if no margin figure — broker-verified or otherwise — is available.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .exceptions import CapitalUnverifiedError
from .models import CapitalSnapshot, MarginRequirement


def build_snapshot(raw: Optional[dict[str, Any]], now: datetime) -> CapitalSnapshot:
    """Build a CapitalSnapshot from a broker's raw get_funds() dict.

    Never raises on a missing/partial dict — a partial snapshot is exactly
    what CapitalSnapshot.is_complete exists to detect; the engine decides
    what to do with an incomplete snapshot (always: refuse to trade).
    `raw is None` (funds unobtainable at all) produces an all-None snapshot,
    which is_complete correctly reports as incomplete.
    """
    raw = raw or {}

    def _num(key: str) -> Optional[float]:
        v = raw.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            # A present-but-garbage value is treated identically to an
            # absent one — never let a malformed broker field silently
            # become 0.0 or crash the caller.
            return None

    return CapitalSnapshot(
        account_equity=_num("account_equity"),
        available_funds=_num("available_funds"),
        available_margin=_num("available_margin"),
        cash_balance=_num("cash_balance"),
        collateral=_num("collateral"),
        used_margin=_num("used_margin"),
        available_exposure=_num("available_exposure"),
        peak_margin=_num("peak_margin"),
        as_of=now,
    )


def build_margin_requirement(
    raw: Optional[dict[str, Any]], now: datetime,
) -> MarginRequirement:
    """Build a MarginRequirement from a broker's raw get_order_margin() dict.

    `raw is None` (no margin figure obtainable at all, verified or not)
    produces margin_per_lot=None, verified=False — the engine treats this as
    an automatic BLOCKED decision, never a guessed default.
    """
    if raw is None:
        return MarginRequirement(
            margin_per_lot=None, verified=False, source="unavailable", as_of=now,
        )
    margin = raw.get("margin_per_lot")
    try:
        margin_val = float(margin) if margin is not None else None
    except (TypeError, ValueError):
        margin_val = None
    return MarginRequirement(
        margin_per_lot=margin_val,
        verified=bool(raw.get("verified", False)) and margin_val is not None,
        source=str(raw.get("source", "unknown")),
        as_of=now,
    )


def require_complete(snapshot: CapitalSnapshot) -> None:
    """Raise CapitalUnverifiedError if the snapshot lacks what the sizing
    algorithm needs. Callers (engine.py) always catch this — it exists as
    a distinct, testable failure mode, not a crash."""
    if not snapshot.is_complete:
        missing = []
        if snapshot.available_margin is None:
            missing.append("available_margin")
        if snapshot.account_equity is None:
            missing.append("account_equity")
        raise CapitalUnverifiedError(
            f"broker funds response missing required field(s): {', '.join(missing)}"
        )

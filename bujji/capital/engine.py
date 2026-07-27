"""MODULE — Capital Management Engine (CME).

Platform subsystem, strategy-independent. Every present and future strategy
that wants to size a trade calls `CapitalManagementEngine.approve_trade()` —
no strategy module calculates a quantity itself.

Design principle (matches every other capital-protection path in this
codebase): FAIL SAFE, NEVER GUESS. If the broker's funds or margin response
cannot be verified, the engine returns a BLOCKED decision with
approved_lots=0 — it never falls back to a configured default quantity,
never estimates, never silently proceeds.

    Strategy -> CapitalManagementEngine -> Broker -> Decision
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from ..broker.base import Broker
from ..core.clock import now_ist
from ..core.logging_setup import log_event
from ..core.models import OptionContract
from .broker_adapter import build_snapshot, require_complete
from .exceptions import BrokerCapitalQueryError, CapitalUnverifiedError
from .models import CapitalStatus, MarginRequirement, SizingDecision
from .providers import BrokerMarginProvider, MarginProvider


def compute_lot_sizing(
    available_margin: float, margin_per_lot: float, safety_buffer: float, configured_max_lots: int,
) -> tuple[float, int, int]:
    """Pure sizing arithmetic, EXTRACTED (Series 91, additive, zero behavior
    change to `approve_trade`, which now calls this instead of repeating the
    same three lines inline) so `bujji.msi_portfolio_construction` can reuse
    this EXACT formula for deterministic replay sizing rather than
    re-deriving it independently -- mirrors the `compute_expected_move`
    extraction precedent from Series 88 exactly.

    Returns (usable_margin, maximum_safe_lots, approved_lots). Caller is
    responsible for ensuring `margin_per_lot > 0` (this function assumes a
    validated, positive figure, exactly as `approve_trade` already
    guarantees before calling it)."""
    usable_margin = available_margin * safety_buffer
    maximum_safe_lots = int(math.floor(usable_margin / margin_per_lot))
    approved_lots = min(configured_max_lots, max(0, maximum_safe_lots))
    return usable_margin, maximum_safe_lots, approved_lots


class CapitalManagementEngine:
    """Determines, before any order is sent, whether and how big a trade
    the account can safely support."""

    def __init__(self, broker: Broker, logger: logging.Logger,
                 safety_buffer: float = 0.90, configured_max_lots: int = 1,
                 warning_utilization: float = 0.85,
                 margin_provider: Optional[MarginProvider] = None,
                 require_verified_margin: bool = False) -> None:
        if not (0.0 < safety_buffer <= 1.0):
            raise ValueError(f"safety_buffer must be in (0, 1.0], got {safety_buffer}")
        self._broker = broker  # Still used directly for get_funds() only --
                               # see docs/CAPITAL_MANAGEMENT_ENGINE.md for
                               # why funds and margin are treated differently.
        self._log = logger
        self._safety_buffer = safety_buffer
        self._configured_max_lots = configured_max_lots
        self._warning_utilization = warning_utilization
        # Margin Provider abstraction (Phase 3, CME v2): this engine NEVER
        # calls broker.get_order_margin() itself anymore -- it only ever
        # talks to this provider, which may or may not be broker-backed.
        # Defaults to an uncertified BrokerMarginProvider so every existing
        # caller keeps working unchanged; callers that care about the
        # capital_policy distinction (STRICT/ESTIMATED/SIMULATION/CERTIFIED)
        # should construct via bujji.capital.policy.build_margin_provider()
        # and pass the result here explicitly.
        self._margin_provider = margin_provider or BrokerMarginProvider(broker)
        # STRICT policy's "unknown margin -> no trade" requirement: when
        # True, an uncertified (verified=False) but numerically-present
        # margin figure is treated exactly like a missing one -- BLOCKED,
        # never silently accepted. False (the default) preserves the
        # pre-Phase-3 behavior of using whatever numeric figure the
        # provider returns, verified or not -- correct for ESTIMATED/
        # SIMULATION policies, which exist precisely to accept an
        # unverified figure deliberately.
        self._require_verified_margin = require_verified_margin

    async def approve_trade(
        self, ce_contract: OptionContract, pe_contract: OptionContract,
    ) -> SizingDecision:
        """The single entry point every strategy uses instead of
        `risk.lots * lot_size`. Never raises — every failure mode is
        represented in the returned SizingDecision's status/reason."""
        now = now_ist()
        lot_size = ce_contract.lot_size

        try:
            raw_funds = await self._safe_call(self._broker.get_funds)
        except BrokerCapitalQueryError as exc:
            return self._blocked(now, lot_size, reason=f"funds_query_failed: {exc}")

        snapshot = build_snapshot(raw_funds, now)
        try:
            require_complete(snapshot)
        except CapitalUnverifiedError as exc:
            return self._blocked(now, lot_size, snapshot=snapshot,
                                 reason=f"capital_unverified: {exc}")

        try:
            margin = await self._safe_provider_call(ce_contract, pe_contract)
        except BrokerCapitalQueryError as exc:
            return self._blocked(now, lot_size, snapshot=snapshot,
                                 reason=f"margin_query_failed: {exc}")

        if margin.margin_per_lot is None or margin.margin_per_lot <= 0:
            return self._blocked(
                now, lot_size, snapshot=snapshot, margin=margin,
                reason=(
                    "margin_unverified: no margin figure available for "
                    f"this exact CE+PE straddle (source={margin.source}) — "
                    "see docs/CAPITAL_MANAGEMENT_ENGINE.md: this is LIVE "
                    "CERTIFICATION REQUIRED for real broker adapters "
                    "without a certified margin-calculator endpoint."
                ),
            )
        if self._require_verified_margin and not margin.verified:
            return self._blocked(
                now, lot_size, snapshot=snapshot, margin=margin,
                reason=(
                    f"margin_not_certified: capital_policy=STRICT requires "
                    f"a broker-CERTIFIED margin figure; this one "
                    f"(source={margin.source}) is UNCERTIFIED — refusing "
                    "to trade rather than accept an unverified number. "
                    "Switch to capital_policy=CERTIFIED (after live "
                    "verification) or ESTIMATED/SIMULATION if this level "
                    "of caution isn't intended."
                ),
            )

        usable_margin, maximum_safe_lots, approved_lots = compute_lot_sizing(
            snapshot.available_margin, margin.margin_per_lot, self._safety_buffer, self._configured_max_lots,
        )

        if approved_lots <= 0:
            decision = self._build_decision(
                now, lot_size, snapshot, margin, usable_margin,
                maximum_safe_lots, approved_lots,
                status=CapitalStatus.BLOCKED,
                reason=(
                    f"insufficient_margin: usable_margin={usable_margin:.2f} "
                    f"< margin_required_per_lot={margin.margin_per_lot:.2f} "
                    "— maximum_safe_lots is 0, refusing to trade today"
                ),
            )
            log_event(self._log, "capital_health", **decision.to_log())
            self._log.warning(decision.render())
            return decision

        margin_used = approved_lots * margin.margin_per_lot
        utilization = margin_used / snapshot.available_margin if snapshot.available_margin else None
        status = (
            CapitalStatus.WARNING
            if utilization is not None and utilization >= self._warning_utilization
            else CapitalStatus.SAFE
        )
        reason = (
            f"approved {approved_lots} lot(s): min(configured_max_lots="
            f"{self._configured_max_lots}, maximum_safe_lots={maximum_safe_lots})"
        )
        decision = self._build_decision(
            now, lot_size, snapshot, margin, usable_margin,
            maximum_safe_lots, approved_lots, status=status, reason=reason,
        )
        log_event(self._log, "capital_health", **decision.to_log())
        self._log.info(decision.render())
        return decision

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _safe_call(self, fn, *args):
        try:
            return await fn(*args)
        except Exception as exc:  # noqa: BLE001 - any broker-side failure.
            raise BrokerCapitalQueryError(str(exc)) from exc

    async def _safe_provider_call(self, ce_contract, pe_contract) -> MarginRequirement:
        try:
            return await self._margin_provider.get_margin_per_lot(ce_contract, pe_contract)
        except BrokerCapitalQueryError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider-side failure.
            raise BrokerCapitalQueryError(str(exc)) from exc

    def _build_decision(self, now, lot_size, snapshot, margin, usable_margin,
                        maximum_safe_lots, approved_lots, status, reason) -> SizingDecision:
        remaining_margin = None
        capital_utilization = None
        if snapshot.available_margin is not None and margin.margin_per_lot:
            margin_used = approved_lots * margin.margin_per_lot
            remaining_margin = snapshot.available_margin - margin_used
            capital_utilization = (
                margin_used / snapshot.available_margin
                if snapshot.available_margin else None
            )
        return SizingDecision(
            status=status,
            approved_lots=approved_lots,
            quantity=approved_lots * lot_size,
            configured_max_lots=self._configured_max_lots,
            maximum_safe_lots=maximum_safe_lots,
            lot_size=lot_size,
            safety_buffer=self._safety_buffer,
            usable_margin=usable_margin,
            margin_required_per_lot=margin.margin_per_lot,
            capital_utilization=capital_utilization,
            remaining_margin=remaining_margin,
            reason=reason,
            snapshot=snapshot,
            margin=margin,
            timestamp=now,
        )

    def _blocked(self, now, lot_size, snapshot=None, margin=None, *, reason: str) -> SizingDecision:
        from .models import CapitalSnapshot
        snapshot = snapshot or CapitalSnapshot(as_of=now)
        margin = margin or MarginRequirement(margin_per_lot=None, verified=False,
                                             source="unavailable", as_of=now)
        decision = SizingDecision(
            status=CapitalStatus.UNVERIFIED,
            approved_lots=0,
            quantity=0,
            configured_max_lots=self._configured_max_lots,
            maximum_safe_lots=0,
            lot_size=lot_size,
            safety_buffer=self._safety_buffer,
            usable_margin=None,
            margin_required_per_lot=margin.margin_per_lot,
            capital_utilization=None,
            remaining_margin=None,
            reason=reason,
            snapshot=snapshot,
            margin=margin,
            timestamp=now,
        )
        log_event(self._log, "capital_health", **decision.to_log())
        self._log.warning(decision.render())
        return decision

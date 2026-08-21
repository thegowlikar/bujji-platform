"""Live Risk Context Provider -- BUJJI Options OS v3, Gate E.3.

PURPOSE: eliminate the remaining MANUAL inputs E.2's GovernorContextBuilder
still required (margin_snapshot/margin_explanation, capital_snapshot,
market_regime) by actually CALLING the live producers that already
exist for them, with caching and structured failure reporting. This
is a provider, not a governor: it makes zero risk/margin/sizing
decisions of its own, and computes nothing E.2/Gate C/Gate D don't
already compute.

================================================================
PART 1 -- MANDATORY INSPECTION FINDINGS, table + two hard STOPs
================================================================

| Data                  | Existing producer(s)                                  | Computed? | Reusable? | Missing? |
|------------------------|--------------------------------------------------------|-----------|-----------|----------|
| Position groups        | Gate A Position Group Journal (read_all_group_ids +    | Yes       | Yes       | No       |
|                         | read_events + position_group_fold.fold)                |           |           |          |
| Position journals       | bujji/journal/position_group_journal.py                 | N/A       | Yes       | No       |
| Margin snapshot          | Gate C: SimulatedMarginProvider /                        | Yes, IF   | Yes (Gate | No, but  |
|                          | FyersMarginProvider.get_portfolio_margin_with_           | leg data  | C only)   | needs    |
|                          | explanation(legs, clock) -- SEE STOP #2 BELOW            | supplied  |           | legs     |
| Portfolio risk snapshot  | Gate D.2 aggregate_portfolio_risk()                       | Yes       | Yes       | No       |
| Risk aggregation          | Gate D.2 (same function)                                    | Yes       | Yes       | No       |
| Strategy proposal          | msi_trade_construction.construct_trade()                    | Yes       | Yes (E.1  | No       |
|                            |                                                              |           | adapter)  |          |
| Adaptive memory             | Gate D.5 AdaptiveRiskMemory.lookup()                          | Yes       | Yes       | No       |
| Calibration store             | Gate C.5 MarginCalibrationStore                                | Yes       | Yes       | No       |
| Capital snapshot                | SEE STOP #1 BELOW                                                | Partial   | No (conflict) | Yes  |
| Market regime                     | market_regime_adapter.py exists but nothing feeds it live       | No        | N/A       | Yes      |

--- STOP #1: TWO INCOMPATIBLE "capital snapshot" PRODUCERS EXIST -------
`bujji.capital.models.CapitalSnapshot` (account_equity, available_funds,
available_margin, cash_balance, collateral, used_margin,
available_exposure, peak_margin, as_of) is a completely SEPARATE,
older-generation model from `bujji.trading_brain.risk_governor.
capital_safety_governor.CapitalSafetySnapshot` (total_capital,
available_capital, used_margin, open_risk, reserved_risk, daily_pnl,
daily_loss_limit, peak_capital, max_allowed_drawdown, consecutive_
losses). They share exactly ONE field name (`used_margin`) and even
THAT may not mean the same thing (one is "used_margin reported by the
broker right now," the other is Gate D.1's own account-level tracked
figure). Per this phase's own explicit instruction ("If multiple
producers already exist for the same field, STOP. Do not merge them.
Document the conflict."), this module does NOT attempt to map one onto
the other -- doing so would require inventing a field-by-field
reconciliation formula that does not exist anywhere in this codebase
today. CapitalSafetySnapshot (the shape GovernorPipelineContext
actually requires) therefore remains a REQUIRED, EXPLICIT, caller-
injected provider callable (`capital_snapshot_provider`) -- this
module calls it, never derives it from `bujji.capital`.

--- STOP #2: TWO INCOMPATIBLE "margin provider" FAMILIES EXIST ---------
`bujji.capital.providers.BrokerMarginProvider`/`CertifiedBrokerMarginProvider`
(async `get_margin_per_lot(ce_contract, pe_contract) -> MarginRequirement`,
ONE straddle pair at a time, and it DOES call `Broker.get_order_margin()`
-- a real, live broker call) is structurally incompatible with Gate C's
`SimulatedMarginProvider`/`FyersMarginProvider` (`get_portfolio_margin_
with_explanation(legs: List[MarginLegRequest], clock) -> (MarginSnapshot,
MarginExplanation)`, the WHOLE BOOK at once). Different call shape,
different per-call granularity, different output type entirely --
merging them would mean writing new reconciliation code, explicitly
forbidden. This module wires to Gate C's shape ONLY (the shape
GovernorPipelineContext/E.2 actually consume), via a caller-injected
`margin_provider` object -- `bujji.capital.providers` is documented
here as the un-chosen, structurally incompatible alternative, never
touched or imported.

Margin computation ALSO still requires per-leg contract/side/reference-
price data (STOP already established in E.2: Gate A's journal carries
none of this by design) -- so `build_context()` still requires the
caller to supply `contracts_by_client_order_id`/`sides_by_client_
order_id`/`reference_prices_by_client_order_id`, exactly mirroring
msi_entry_bridge.py's own established pattern for the same reason.

================================================================
PART 4 -- DEPENDENCY GRAPH (the exact call chain, no shortcuts)
================================================================

  PositionGroupJournal.read_all_group_ids() / read_events()
    -> position_group_fold.fold()                                  [Gate A]
    -> whole_book_margin_provider.project_whole_book_to_margin_legs() [Gate C, pure projection]
    -> margin_provider.get_portfolio_margin_with_explanation()        [Gate C, injected]
    -> capital_snapshot_provider()                                     [caller-injected, STOP #1]
    -> market_regime_provider()                                         [caller-injected, optional]
    -> AdaptiveRiskMemory (passed straight through)                      [Gate D.5]
    -> GovernorContextBuilder(journal).build(...)                          [Gate E.2 -- delegates
                                                                              ALL assembly/validation]
    -> GovernorPipelineContext                                               [ready for D.6]

Every arrow above is a call to an already-existing function/method.
This module adds zero new formulas at any step.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Union

from bujji.msi_trade_construction.models import TradeConstructionAssessment
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.position_group_fold import fold

from .capital_safety_governor import CapitalSafetySnapshot, CapitalSafetyThresholds, ProposedTradeEffect
from .portfolio_risk_aggregator import PortfolioRiskThresholds
from .risk_budget_governor import RiskPolicy
from .position_lifecycle_intelligence import PositionHealthThresholds
from .adaptive_risk_memory import AdaptiveRiskMemory
from .margin_calibration_runner import MarginCalibrationStore
from .whole_book_margin_provider import IllegalMarginProjectionInputError, project_whole_book_to_margin_legs
from .risk_governor_pipeline import GovernorPipelineContext
from .governor_context_builder import ContextBuildFailure, GovernorContextBuilder

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ContextUnavailable:
    """Part 6: never fabricate a replacement -- name exactly which
    producer, which object, why, and when."""
    producer: str
    object_name: str
    reason: str
    timestamp: datetime


@dataclass(frozen=True)
class FieldProvenance:
    field: str
    producer: str
    status: str          # "computed" | "cached"
    as_of: datetime


@dataclass(frozen=True)
class _CachedContext:
    context: GovernorPipelineContext
    built_at: datetime
    provenance: tuple


class LiveRiskContextProvider:
    """Part 2: read -> collect -> call existing producers -> return a
    populated object. No decision making anywhere in this class."""

    def __init__(
        self,
        journal: PositionGroupJournal,
        margin_provider: Any,                                            # Gate C shape: get_portfolio_margin_with_explanation(legs, clock)
        capital_snapshot_provider: Callable[[], CapitalSafetySnapshot],    # required, injected -- see STOP #1
        memory: AdaptiveRiskMemory,
        market_regime_provider: Optional[Callable[[], Optional[str]]] = None,
        calibration_store: Optional[MarginCalibrationStore] = None,
        cache_ttl_seconds: Optional[float] = None,
    ) -> None:
        self._journal = journal
        self._margin_provider = margin_provider
        self._capital_snapshot_provider = capital_snapshot_provider
        self._memory = memory
        self._market_regime_provider = market_regime_provider
        self._calibration_store = calibration_store
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: Optional[_CachedContext] = None

    def invalidate(self) -> None:
        """Part 5: explicit invalidation -- the ONLY other way the
        cache clears besides a fresh build overwriting it."""
        self._cache = None

    def _cache_is_fresh(self, now: datetime) -> bool:
        if self._cache is None or self._cache_ttl_seconds is None:
            return False
        age_seconds = (now - self._cache.built_at).total_seconds()
        return 0 <= age_seconds <= self._cache_ttl_seconds

    def explain_context(self) -> str:
        """Part 7: where every field came from, from the LAST
        build_context() call (cached or fresh)."""
        if self._cache is None:
            return "No context has been built yet."
        lines = []
        for entry in self._cache.provenance:
            lines.append(f"{entry.field}\n  <- {entry.producer}\n  {entry.status} at {entry.as_of.isoformat()}")
        return "\n\n".join(lines)

    def build_context(
        self,
        proposal: TradeConstructionAssessment,
        desired_quantity: int,
        requested_risk: float,
        proposed_trade_effect: ProposedTradeEffect,
        contracts_by_client_order_id: Dict[str, Any],
        sides_by_client_order_id: Dict[str, str],
        reference_prices_by_client_order_id: Dict[str, Optional[float]],
        instrument_type: str,
        product_type: str,
        risk_by_position_group_id: Optional[Dict[str, float]],
        capital_safety_thresholds: Optional[CapitalSafetyThresholds],
        portfolio_risk_thresholds: Optional[PortfolioRiskThresholds],
        risk_policy: RiskPolicy,
        position_health_thresholds: Optional[PositionHealthThresholds],
        clock: Clock,
        calibration_sample_id: Optional[str] = None,
        use_cache: bool = True,
    ) -> Union[GovernorPipelineContext, ContextUnavailable]:
        now = clock()
        if use_cache and self._cache_is_fresh(now):
            return self._cache.context

        result, provenance = self._build_fresh(
            proposal, desired_quantity, requested_risk, proposed_trade_effect,
            contracts_by_client_order_id, sides_by_client_order_id, reference_prices_by_client_order_id,
            instrument_type, product_type, risk_by_position_group_id, capital_safety_thresholds,
            portfolio_risk_thresholds, risk_policy, position_health_thresholds, clock, calibration_sample_id, now,
        )
        if isinstance(result, GovernorPipelineContext):
            self._cache = _CachedContext(context=result, built_at=now, provenance=provenance)
        return result

    def _build_fresh(
        self, proposal, desired_quantity, requested_risk, proposed_trade_effect,
        contracts_by_client_order_id, sides_by_client_order_id, reference_prices_by_client_order_id,
        instrument_type, product_type, risk_by_position_group_id, capital_safety_thresholds,
        portfolio_risk_thresholds, risk_policy, position_health_thresholds, clock, calibration_sample_id, now,
    ):
        provenance: List[FieldProvenance] = []

        # -- Gate A: position groups (same existing functions E.2 uses) --
        group_ids = self._journal.read_all_group_ids()
        position_groups = []
        for group_id in group_ids:
            events = self._journal.read_events(group_id)
            state = fold(events) if events else None
            if state is not None:
                position_groups.append(state)
        provenance.append(FieldProvenance("position_groups", "PositionGroupJournal", "computed", now))

        # -- Gate C: margin (pure projection + injected provider) --------
        try:
            legs = project_whole_book_to_margin_legs(
                position_groups, contracts_by_client_order_id, sides_by_client_order_id,
                reference_prices_by_client_order_id, instrument_type, product_type,
            )
            margin_snapshot, margin_explanation = self._margin_provider.get_portfolio_margin_with_explanation(
                legs, clock,
            )
        except IllegalMarginProjectionInputError as exc:
            return ContextUnavailable("project_whole_book_to_margin_legs", "margin_snapshot", str(exc), now), tuple(provenance)
        except Exception as exc:  # noqa: BLE001 -- any margin provider failure, never guessed around
            return ContextUnavailable(type(self._margin_provider).__name__, "margin_snapshot", str(exc), now), tuple(provenance)
        provenance.append(FieldProvenance("margin_snapshot", type(self._margin_provider).__name__, "computed", now))
        provenance.append(FieldProvenance("margin_explanation", type(self._margin_provider).__name__, "computed", now))

        # -- STOP #1: capital snapshot, injected, never derived here -----
        try:
            capital_snapshot = self._capital_snapshot_provider()
        except Exception as exc:  # noqa: BLE001
            return ContextUnavailable("capital_snapshot_provider", "capital_snapshot", str(exc), now), tuple(provenance)
        provenance.append(FieldProvenance("capital_snapshot", "capital_snapshot_provider", "computed", now))

        # -- Market regime, optional, never fabricated when absent -------
        market_regime = None
        if self._market_regime_provider is not None:
            try:
                market_regime = self._market_regime_provider()
            except Exception as exc:  # noqa: BLE001
                return ContextUnavailable("market_regime_provider", "market_regime", str(exc), now), tuple(provenance)
            provenance.append(FieldProvenance("market_regime", "market_regime_provider", "computed", now))

        provenance.append(FieldProvenance("memory_entries", "AdaptiveRiskMemory.lookup", "computed", now))

        # -- Delegate ALL assembly + validation to E.2 --------------------
        builder = GovernorContextBuilder(self._journal)
        build_result = builder.build(
            proposal, desired_quantity, requested_risk, capital_snapshot, proposed_trade_effect,
            capital_safety_thresholds, margin_snapshot, margin_explanation, risk_by_position_group_id,
            portfolio_risk_thresholds, risk_policy, position_health_thresholds, market_regime, self._memory,
            clock, self._calibration_store, calibration_sample_id,
        )
        if isinstance(build_result, ContextBuildFailure):
            return ContextUnavailable("GovernorContextBuilder", build_result.object_name, build_result.reason, now), tuple(provenance)

        provenance.append(FieldProvenance("GovernorPipelineContext", "GovernorContextBuilder.build", "computed", now))
        return build_result, tuple(provenance)

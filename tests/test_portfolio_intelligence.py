"""Phase 15M -- Portfolio Intelligence tests. Each test proves one
meaningful invariant over a REAL `PositionLifecycle` dict produced by
the real `apply_event` reducer (Phase 15G), never a hand-built
PortfolioSnapshot."""
from __future__ import annotations

from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_management_assessed_payload,
    build_position_closed_payload, build_position_opened_payload, build_structured_exit,
    build_thesis_evaluated_payload,
)
from bujji.position_management.engine import assess_position_management
from bujji.portfolio_intelligence.engine import build_portfolio_snapshot, detect_conflicts
from bujji.portfolio_intelligence.models import (
    CONFLICT_CORRELATED_EXPOSURE, CONFLICT_DIRECTIONAL, CONFLICT_EXPIRY_CONCENTRATION,
    CONFLICT_NO_CONFLICT, CONFLICT_UNDERLYING_CONCENTRATION, STATUS_KNOWN, STATUS_UNKNOWN,
    THESIS_HEALTH_ALL_INTACT, THESIS_HEALTH_DETERIORATING, THESIS_HEALTH_NO_OPEN_POSITIONS,
)


class Leg:
    def __init__(self, role="PRIMARY", option_type="CE", strike=24450.0, side="BUY", ratio=1, delta=0.5,
                 expiry="2026-08-13", entry_mid=100.0):
        self.role, self.option_type, self.strike, self.expiry = role, option_type, strike, expiry
        self.side, self.ratio, self.entry_mid, self.delta = side, ratio, entry_mid, delta
        self.entry_bid, self.entry_ask = entry_mid - 2, entry_mid + 2


class Candidate:
    def __init__(self, candidate_id, direction, legs, underlying_symbol="NIFTY", family="LONG_DIRECTIONAL",
                 lot_size=75):
        self.candidate_id, self.source_cycle_id, self.strategy_family = candidate_id, "t0", family
        self.timestamp, self.market_regime, self.direction, self.thesis = "t0", "RANGING", direction, "X"
        self.selection_confidence, self.underlying_price, self.legs = "HIGH", 24450.0, legs
        self.lot_size = lot_size
        self.underlying_symbol = underlying_symbol


class ThesisEval:
    def __init__(self, status):
        self.thesis_status = status
        self.evidence_confidence = "HIGH"
        self.candidate_id = "c1"

    def to_dict(self):
        return {"thesis_status": self.thesis_status, "evidence_confidence": self.evidence_confidence}


def _open(states, cid, direction, legs, session_id="S1", ts="t0", **kwargs):
    candidate = Candidate(cid, direction, legs, **kwargs)
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload(session_id, candidate, entry, ts)
    states, result = apply_event(states, session_id, "POSITION_OPENED", session_id, payload)
    return states, payload["position_id"]


def _evaluate_thesis(states, pid, status, session_id="S1", cycle_id="c1"):
    payload = build_thesis_evaluated_payload(pid, cycle_id, ThesisEval(status))
    return apply_event(states, session_id, "THESIS_EVALUATED", session_id, payload)


def _close(states, pid, legs, lot_size, exit_price, session_id="S1"):
    exit_prices = {leg.leg_id: {"exit_price": exit_price} for leg in legs}
    structured_exit = build_structured_exit(legs, lot_size, "t5", "manual", exit_prices)
    payload = build_position_closed_payload(pid, "t5", "manual", structured_exit)
    return apply_event(states, session_id, "POSITION_CLOSED", session_id, payload)


# 1. Single position
def test_single_position():
    states, pid = _open({}, "C1", "BULLISH", (Leg(),))
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.position_count == 1
    assert snap.open_position_ids == (pid,)
    assert snap.net_delta.status == STATUS_KNOWN
    assert snap.net_delta.net == 75 * 0.5  # BUY, delta 0.5, qty 1, lot 75.


# 2. Multiple positions
def test_multiple_positions():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(option_type="CE"),))
    states, pid2 = _open(states, "C2", "BULLISH", (Leg(option_type="PE", side="SELL"),))
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.position_count == 2
    assert set(snap.open_position_ids) == {pid1, pid2}


# 3. Multi-leg position (Iron Condor)
def test_multi_leg_position():
    legs = (
        Leg("LONG_CE", "CE", 24600.0, "BUY"), Leg("SHORT_CE", "CE", 24500.0, "SELL"),
        Leg("SHORT_PE", "PE", 24300.0, "SELL"), Leg("LONG_PE", "PE", 24200.0, "BUY"),
    )
    states, pid = _open({}, "C1", "RANGING", legs)
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.position_count == 1
    assert snap.premium_exposure.status == STATUS_KNOWN


# 4. Same-symbol concurrent positions
def test_same_symbol_concurrent_positions():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(),), underlying_symbol="NIFTY")
    states, pid2 = _open(states, "C2", "BULLISH", (Leg(),), underlying_symbol="NIFTY")
    snap = build_portfolio_snapshot(states, "S1")
    conc = {c.key: c for c in snap.concentration_by_underlying}
    assert conc["NIFTY"].position_count == 2


# 5. Long/short directional conflict
def test_directional_conflict_detected():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(),), underlying_symbol="NIFTY")
    states, pid2 = _open(states, "C2", "BEARISH", (Leg(side="SELL"),), underlying_symbol="NIFTY")
    snap = build_portfolio_snapshot(states, "S1")
    findings = {c.finding for c in snap.conflicts}
    assert CONFLICT_DIRECTIONAL in findings


# No conflict case
def test_no_conflict_when_positions_agree():
    states, pid = _open({}, "C1", "BULLISH", (Leg(),), underlying_symbol="NIFTY")
    snap = build_portfolio_snapshot(states, "S1")
    findings = {c.finding for c in snap.conflicts}
    assert CONFLICT_NO_CONFLICT in findings
    assert CONFLICT_DIRECTIONAL not in findings


# 6. Mixed strategy families
def test_mixed_strategy_families():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(),), family="LONG_DIRECTIONAL")
    states, pid2 = _open(states, "C2", "RANGING", (Leg(option_type="PE"),), family="IRON_CONDOR")
    snap = build_portfolio_snapshot(states, "S1")
    families = {c.key for c in snap.concentration_by_strategy_family}
    assert families == {"LONG_DIRECTIONAL", "IRON_CONDOR"}


# 7. Realized P&L aggregation
def test_realized_pnl_aggregation_across_closed_positions():
    legs1 = (Leg(),)
    states, pid1 = _open({}, "C1", "BULLISH", legs1)
    leg1 = states[pid1].legs[0]
    states, _ = _close(states, pid1, (leg1,), 75, 150.0)  # +50/unit profit.

    legs2 = (Leg(option_type="PE"),)
    states, pid2 = _open(states, "C2", "BULLISH", legs2)
    leg2 = states[pid2].legs[0]
    states, _ = _close(states, pid2, (leg2,), 75, 80.0)  # -20/unit loss.

    snap = build_portfolio_snapshot(states, "S1")
    assert snap.pnl.status == STATUS_KNOWN
    assert snap.pnl.realized_gross_pnl == (50.0 * 75) + (-20.0 * 75)
    assert snap.pnl.per_position[pid1] == 50.0 * 75
    assert snap.pnl.per_position[pid2] == -20.0 * 75


# 8. UNKNOWN propagation
def test_unknown_propagates_when_no_structured_exit():
    legs = (Leg(),)
    states, pid = _open({}, "C1", "BULLISH", legs)
    leg = states[pid].legs[0]
    # Legacy-style close with NO structured_exit at all.
    payload = build_position_closed_payload(pid, "t5", "manual", None)
    states, _ = apply_event(states, "S1", "POSITION_CLOSED", "S1", payload)
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.pnl.status == STATUS_UNKNOWN
    assert snap.pnl.realized_gross_pnl is None


def test_unrealized_pnl_always_unknown_in_pure_snapshot():
    states, pid = _open({}, "C1", "BULLISH", (Leg(),))
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.pnl.unrealized_pnl is None
    assert snap.pnl.unrealized_pnl_status == STATUS_UNKNOWN


# 9. Greek aggregation (gamma/theta/vega) -- honestly UNKNOWN without entry_greeks.
def test_greek_aggregation_unknown_without_entry_greeks():
    states, pid = _open({}, "C1", "BULLISH", (Leg(),))
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.net_gamma.status == STATUS_UNKNOWN
    assert snap.net_theta.status == STATUS_UNKNOWN
    assert snap.net_vega.status == STATUS_UNKNOWN


def test_greek_aggregation_known_when_strike_matches_entry_snapshot():
    from bujji.position_lifecycle.engine import build_entry_snapshot_for_position
    candidate = Candidate("C1", "BULLISH", (Leg(strike=24450.0),))
    cycle_record = {"greeks": {
        "strike": 24450.0, "ce": {"delta": 0.5, "gamma": 0.02, "theta_per_day": -1.5, "vega_per_pct": 3.0},
        "pe": {"delta": -0.5, "gamma": 0.02, "theta_per_day": -1.2, "vega_per_pct": 2.8},
    }}
    entry = build_entry_snapshot_for_position(candidate, cycle_record)
    payload = build_position_opened_payload("S1", candidate, entry, "t0")
    states, _ = apply_event({}, "S1", "POSITION_OPENED", "S1", payload)
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.net_gamma.status == STATUS_KNOWN
    assert snap.net_gamma.net == 0.02 * 1 * 75


def test_greek_aggregation_unknown_when_leg_strike_differs_from_snapshot():
    """The leg's own strike (24500) does NOT match the entry snapshot's
    ATM strike (24450) -- gamma/theta/vega must stay UNKNOWN, never
    borrowed from the wrong strike."""
    from bujji.position_lifecycle.engine import build_entry_snapshot_for_position
    candidate = Candidate("C1", "BULLISH", (Leg(strike=24500.0),))
    cycle_record = {"greeks": {"strike": 24450.0, "ce": {"delta": 0.5, "gamma": 0.02, "theta_per_day": -1.5, "vega_per_pct": 3.0},
                                "pe": {"delta": -0.5, "gamma": 0.02, "theta_per_day": -1.2, "vega_per_pct": 2.8}}}
    entry = build_entry_snapshot_for_position(candidate, cycle_record)
    payload = build_position_opened_payload("S1", candidate, entry, "t0")
    states, _ = apply_event({}, "S1", "POSITION_OPENED", "S1", payload)
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.net_gamma.status == STATUS_UNKNOWN


# 10. Concentration detection
def test_expiry_concentration_detected():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(expiry="2026-08-13"),))
    states, pid2 = _open(states, "C2", "BULLISH", (Leg(expiry="2026-08-13"),))
    snap = build_portfolio_snapshot(states, "S1")
    findings = {c.finding for c in snap.conflicts}
    assert CONFLICT_EXPIRY_CONCENTRATION in findings


def test_correlated_short_premium_exposure_detected():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(side="SELL"),))
    states, pid2 = _open(states, "C2", "BULLISH", (Leg(option_type="PE", side="SELL"),))
    snap = build_portfolio_snapshot(states, "S1")
    findings = {c.finding for c in snap.conflicts}
    assert CONFLICT_CORRELATED_EXPOSURE in findings


# 11. Thesis aggregation
def test_thesis_aggregation_all_intact():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(),))
    states, _ = _evaluate_thesis(states, pid1, "THESIS_INTACT")
    states, pid2 = _open(states, "C2", "BULLISH", (Leg(option_type="PE"),))
    states, _ = _evaluate_thesis(states, pid2, "THESIS_INTACT")
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.thesis_health == THESIS_HEALTH_ALL_INTACT
    assert snap.thesis_health_detail == {"intact": 2, "weakening": 0, "invalidated": 0, "unknown": 0}


def test_thesis_aggregation_deteriorating():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(),))
    states, _ = _evaluate_thesis(states, pid1, "THESIS_INTACT")
    states, pid2 = _open(states, "C2", "BULLISH", (Leg(option_type="PE"),))
    states, _ = _evaluate_thesis(states, pid2, "THESIS_INVALIDATED")
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.thesis_health == THESIS_HEALTH_DETERIORATING


def test_thesis_health_no_open_positions():
    snap = build_portfolio_snapshot({}, "S1")
    assert snap.thesis_health == THESIS_HEALTH_NO_OPEN_POSITIONS


# 12. Management aggregation
def test_management_recommendation_summary():
    states, pid = _open({}, "C1", "BULLISH", (Leg(),))
    states, _ = _evaluate_thesis(states, pid, "THESIS_INTACT")
    assessment = assess_position_management(pid, "c1", ThesisEval("THESIS_INTACT"), None, None)
    payload = build_management_assessed_payload(pid, "c1", assessment)
    states, _ = apply_event(states, "S1", "MANAGEMENT_ASSESSED", "S1", payload)
    snap = build_portfolio_snapshot(states, "S1")
    assert sum(snap.management_recommendation_summary.values()) == 1
    assert "HOLD" in snap.management_recommendation_summary or len(snap.management_recommendation_summary) == 1


# 13. Duplicate events -- portfolio snapshot is a pure function of already-deduped lifecycle state,
# so re-hydrating the same events twice must give byte-identical results.
def test_duplicate_events_do_not_change_snapshot(tmp_path):
    from bujji.position_lifecycle.recovery import hydrate_position_lifecycles
    from bujji.state_persistence.models import PersistedEvent
    from bujji.state_persistence.store import EventStore
    path = str(tmp_path / "dup.jsonl")
    store = EventStore(path)
    candidate = Candidate("C1", "BULLISH", (Leg(),))
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload("S1", candidate, entry, "t0")
    pid = payload["position_id"]
    event = PersistedEvent(event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id="S1", cycle_id="t0",
                            timestamp="t0", schema_version="1.0.0", provenance="test", payload=payload)
    store.append(event)
    store.append(event)  # exact duplicate.
    states, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.position_count == 1  # not double-counted.


# 14. Session isolation
def test_session_isolation():
    states_a, pid_a = _open({}, "C1", "BULLISH", (Leg(),), session_id="SESSION-A")
    states_b, pid_b = _open({}, "C1", "BULLISH", (Leg(),), session_id="SESSION-B")
    snap_a = build_portfolio_snapshot(states_a, "SESSION-A")
    snap_b = build_portfolio_snapshot(states_b, "SESSION-B")
    assert snap_a.position_count == 1
    assert snap_b.position_count == 1
    assert snap_a.open_position_ids != snap_b.open_position_ids  # different position_ids -- session embedded in identity.


# Capital deployed
def test_capital_deployed_only_counts_open_positions():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(entry_mid=100.0),))
    leg1 = states[pid1].legs[0]
    states, _ = _close(states, pid1, (leg1,), 75, 150.0)
    states, pid2 = _open(states, "C2", "BULLISH", (Leg(option_type="PE", entry_mid=50.0),))
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.capital_deployed == 50.0 * 1 * 75  # only the still-open position's premium notional.


# Closed positions tracked separately
def test_closed_positions_tracked_separately():
    states, pid1 = _open({}, "C1", "BULLISH", (Leg(),))
    leg1 = states[pid1].legs[0]
    states, _ = _close(states, pid1, (leg1,), 75, 150.0)
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.closed_position_ids == (pid1,)
    assert snap.open_position_ids == ()

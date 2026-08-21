"""Premium Behaviour Recovery -- Phase 15E.

Same event-derived-reconstruction finding as Phase 15D's
ObservationMemory recovery: `market_snapshots.jsonl` (already
persisted, zero new format) already contains everything needed to
reconstruct PremiumBehaviourState -- the ATM CE/PE mid premiums and
spot, per real cycle, in order. Reuses
`bujji.market_state_builder.recovery.read_market_snapshots_with_diagnostics`
DIRECTLY (the exact same diagnostics reader Phase 15D built and
validated against the real 174-cycle session) rather than inventing a
second persistence/diagnostics mechanism for what is the same
underlying file.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.market_state_builder.recovery import read_market_snapshots_with_diagnostics
from bujji.state_persistence.models import RECOVERY_COMPLETE, RECOVERY_FAILED, RECOVERY_PARTIAL, RecoveryReport

from .models import DEFAULT_LOOKBACK, PremiumBehaviourState, PremiumObservation


def _atm_mid_premiums_and_spot(snapshot) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    spot = snapshot.spot.ltp if snapshot.spot else None
    if snapshot.option_chain is None:
        return None, None, spot
    atm = snapshot.option_chain.atm_strike
    ce = snapshot.option_chain.leg(atm, "CE")
    pe = snapshot.option_chain.leg(atm, "PE")
    ce_mid = (ce.bid + ce.ask) / 2.0 if ce and ce.bid is not None and ce.ask is not None else None
    pe_mid = (pe.bid + pe.ask) / 2.0 if pe and pe.bid is not None and pe.ask is not None else None
    return ce_mid, pe_mid, spot


def hydrate_premium_behaviour(
    market_snapshot_path: Optional[str], lookback: int = DEFAULT_LOOKBACK,
) -> Tuple[PremiumBehaviourState, RecoveryReport]:
    """Replays every real, valid, in-order MarketSnapshot from
    `market_snapshot_path` through PremiumBehaviourState.advance() --
    the exact same pure state-transition function the live recorder
    already calls each cycle. Never reads live market data."""
    state = PremiumBehaviourState(lookback=lookback)
    total_lines = 0
    malformed = 0
    schema_mismatch = 0
    duplicate_or_out_of_order = 0
    replayed = 0
    errors = []
    last_ts: Optional[str] = None

    for snapshot, issue in read_market_snapshots_with_diagnostics(market_snapshot_path):
        total_lines += 1
        if issue == "malformed":
            malformed += 1
            continue
        if issue == "schema_mismatch":
            schema_mismatch += 1
            continue
        if issue == "duplicate_or_out_of_order":
            duplicate_or_out_of_order += 1
            continue
        try:
            ce_mid, pe_mid, spot = _atm_mid_premiums_and_spot(snapshot)
            state = state.advance(PremiumObservation(snapshot.timestamp, ce_mid, pe_mid, spot))
            replayed += 1
            last_ts = snapshot.timestamp
        except Exception as exc:  # noqa: BLE001 -- a corrupt-but-parseable snapshot must degrade to FAILED, never crash the caller.
            errors.append(f"{type(exc).__name__}: {exc}")

    had_issues = bool(errors or malformed or schema_mismatch or duplicate_or_out_of_order)
    if total_lines == 0:
        status = RECOVERY_COMPLETE
    elif errors and replayed == 0:
        status = RECOVERY_FAILED
    elif had_issues and replayed == 0:
        status = RECOVERY_FAILED
    elif had_issues:
        status = RECOVERY_PARTIAL
    else:
        status = RECOVERY_COMPLETE

    report = RecoveryReport(
        status=status, events_discovered=total_lines, events_replayed=replayed,
        events_skipped_duplicate=duplicate_or_out_of_order, events_skipped_malformed=malformed,
        events_skipped_schema_mismatch=schema_mismatch, last_recovered_cycle_id=last_ts,
        errors=tuple(errors),
        unresolved_notes=() if not had_issues else (
            f"{malformed} malformed, {schema_mismatch} schema-mismatched, "
            f"{duplicate_or_out_of_order} duplicate/out-of-order line(s) skipped",
        ),
    )
    return state, report

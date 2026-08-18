"""Formal Replay Engine -- Phase 15H. Orchestrates ALREADY-EXISTING
production reducers (never a parallel/forked reimplementation) over
ALREADY-PERSISTED real artifacts. No broker, no execution, no capital/
risk mutation, strictly read-only over its source files.

Domains RECONSTRUCTED (independently rebuilt from `market_snapshots.jsonl`
alone, via the real production function each one already uses live):
- ObservationMemory / PSI / MSSI (`market_state_builder.market_state.MarketStateBuilder`)
- MDI (`market_state.direction_bridge.build_market_direction`)
- Regime Memory trajectory (`market_regime_memory`, advanced over the
  REFERENCED regime label -- see below)
- Greeks (`market_perception.greeks_adapter.build_greeks_assessment`)
- Premium Behaviour (`premium_behaviour.engine.evaluate`)

Domains REFERENCED (read verbatim from the already-persisted
`intelligence_cycle.jsonl`, NEVER recomputed -- forensic finding, Step
4: `MarketSnapshot` never persists the spot candles a cycle used, so
Volatility Structure and everything downstream of it -- consensus,
opportunity, eligibility, selection, TradeIntent -- cannot be
independently reconstructed from today's persisted artifacts. This is
a disclosed, real architecture gap, not silently worked around):
- volatility_structure, consensus, opportunity, strategy_eligibility,
  strategy_selection, trade_intent
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from bujji.market_perception.greeks_adapter import build_greeks_assessment
from bujji.market_regime_memory.engine import evaluate as evaluate_regime_memory
from bujji.market_regime_memory.models import RegimeMemoryState
from bujji.market_state.direction_bridge import build_market_direction
from bujji.market_state_builder.market_state import MarketStateBuilder
from bujji.market_state_builder.recovery import market_snapshot_from_dict, read_market_snapshots_with_diagnostics
from bujji.premium_behaviour.engine import evaluate as evaluate_premium_behaviour
from bujji.premium_behaviour.models import PremiumBehaviourState, PremiumObservation
from bujji.state_persistence.models import RECOVERY_COMPLETE, RECOVERY_FAILED, RECOVERY_PARTIAL, RecoveryReport

from .models import REFERENCED, RECONSTRUCTED, ReplayCheckpoint, ReplayCycleResult, ReplaySession

_REFERENCED_FIELDS = (
    "volatility_structure", "consensus", "opportunity", "strategy_eligibility",
    "strategy_selection", "trade_intent",
)


def _atm_mid_premiums_and_spot(snapshot):
    spot = snapshot.spot.ltp if snapshot.spot else None
    if snapshot.option_chain is None:
        return None, None, spot
    atm = snapshot.option_chain.atm_strike
    ce = snapshot.option_chain.leg(atm, "CE")
    pe = snapshot.option_chain.leg(atm, "PE")
    ce_mid = (ce.bid + ce.ask) / 2.0 if ce and ce.bid is not None and ce.ask is not None else None
    pe_mid = (pe.bid + pe.ask) / 2.0 if pe and pe.bid is not None and pe.ask is not None else None
    return ce_mid, pe_mid, spot


def _normalize(x):
    """Tuples -> lists (JSON round-trip normalization), so a
    fingerprint/comparison never differs on representation alone."""
    if isinstance(x, dict):
        return {k: _normalize(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_normalize(v) for v in x]
    return x


def fingerprint_state(payload: Dict[str, Any]) -> str:
    """Deterministic SHA-256 over the JSON-normalized payload -- same
    real content always produces the same fingerprint, regardless of
    tuple/list/object-identity artifacts."""
    normalized = _normalize(payload)
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()


class ReplayEngine:
    """One session's replay driver. Strictly read-only over
    `market_snapshot_path`/`intelligence_cycle_path` -- never writes to
    either, never imports a broker."""

    def __init__(self, session_id: str, market_snapshot_path: str, intelligence_cycle_path: Optional[str] = None) -> None:
        self._session_id = session_id
        self._market_snapshot_path = market_snapshot_path
        self._intelligence_cycle_path = intelligence_cycle_path

    def _load_referenced_cycles(self) -> Dict[str, dict]:
        """timestamp -> real persisted intelligence_cycle record, for
        REFERENCED-field lookup and cross-check (Step 7). Empty dict
        (never an error) if no intelligence_cycle_path was supplied."""
        if not self._intelligence_cycle_path:
            return {}
        out = {}
        try:
            with open(self._intelligence_cycle_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    out[record.get("timestamp", "")] = record
        except FileNotFoundError:
            return {}
        return out

    def run(self, max_cycles: Optional[int] = None, resume_from: Optional[ReplayCheckpoint] = None) -> ReplaySession:
        """Full replay from cycle 0, OR a resume: iterates ALL real
        persisted snapshots (needed to correctly rebuild state up to
        the checkpoint -- this IS the same mechanism
        hydrate_observation_memory/hydrate_premium_behaviour already
        use), but only COLLECTS per-cycle results from
        `resume_from.cycle_index` onward. Genuine restart-from-a-
        truncated-file equivalence is separately, additionally proven
        by tests calling the underlying hydrate_* functions directly."""
        skip_before = resume_from.cycle_index if resume_from else 0
        referenced_cycles = self._load_referenced_cycles()

        builder = MarketStateBuilder()
        regime_state = RegimeMemoryState()
        premium_state = PremiumBehaviourState()

        cycles = []
        total_lines = malformed = schema_mismatch = duplicate_or_out_of_order = 0
        replayed = 0
        errors = []
        last_ts = None

        for i, (snapshot, issue) in enumerate(read_market_snapshots_with_diagnostics(self._market_snapshot_path)):
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
            if max_cycles is not None and i >= max_cycles:
                break
            try:
                assessment = builder.process(snapshot)
                mdi = build_market_direction(assessment, snapshot.timestamp)
                greeks = build_greeks_assessment(snapshot, __import__("datetime").datetime.fromisoformat(snapshot.timestamp))

                referenced_record = referenced_cycles.get(snapshot.timestamp)
                referenced_regime = (referenced_record or {}).get("market_state", {}) or {}
                regime_label = referenced_regime.get("regime") if isinstance(referenced_regime, dict) else None
                regime_state = regime_state.advance(regime_label)
                regime_memory = evaluate_regime_memory(regime_state)

                ce_mid, pe_mid, spot = _atm_mid_premiums_and_spot(snapshot)
                premium_state = premium_state.advance(PremiumObservation(snapshot.timestamp, ce_mid, pe_mid, spot))
                premium_reading = evaluate_premium_behaviour(premium_state)

                reconstructed = {
                    "price_structure": _normalize(_to_plain(assessment.price_structure)),
                    "market_structure": _normalize(_to_plain(assessment.market_structure)),
                    "market_direction": _normalize(_to_plain(mdi)),
                    "greeks": greeks.to_dict() if greeks else None,
                    "regime_memory": regime_memory.to_dict(),
                    "premium_behaviour": premium_reading.to_dict(),
                }
                referenced = {k: (referenced_record or {}).get(k) for k in _REFERENCED_FIELDS}

                replayed += 1
                last_ts = snapshot.timestamp
                if i >= skip_before:
                    cycles.append(ReplayCycleResult(cycle_index=i, timestamp=snapshot.timestamp,
                                                      reconstructed=reconstructed, referenced=referenced))
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
            errors=tuple(errors), unresolved_notes=(),
        )

        final_fingerprint = fingerprint_state({
            "observation_memory": _normalize(_to_plain(builder.memory)),
            "regime_memory": _normalize(_to_plain(regime_state)),
            "premium_behaviour_state": _normalize(_to_plain(premium_state)),
        })

        return ReplaySession(
            session_id=self._session_id, market_snapshot_path=self._market_snapshot_path,
            intelligence_cycle_path=self._intelligence_cycle_path, cycles=tuple(cycles),
            final_fingerprint=final_fingerprint,
            recovery_reports={"market_snapshot_replay": report.to_dict()},
        )

    def checkpoint(self, cycle_index: int) -> ReplayCheckpoint:
        return ReplayCheckpoint(cycle_index=cycle_index, session_id=self._session_id)


def _to_plain(obj):
    if obj is None:
        return None
    import dataclasses
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return obj

"""Tests — Engineering Series 70 Phase 2: Context Stability Observatory
(recording-only). Verifies the new PublishedState fields, the new
QualificationReport session dict keys, backward compatibility when the
new values are absent, and that the new context/stability detail fields
sit in CANONICAL_FIELD_ORDER before "context_stability" itself.

Follows the same mocked-subprocess pattern already used by
tests/test_historical_mic_publication_replay.py -- never spawns the
real MIC v2 subprocess in the unit tests below (a live end-to-end check
against the real MIC v2 checkout was done manually, outside pytest, per
docs/CONTEXT_STABILITY_OBSERVABILITY.md).
"""
import json
from datetime import datetime
from unittest.mock import patch

from bujji.mic_replay.publication_replay import (
    PublishedState,
    replay_published_state_for_session,
)
from bujji.mic_replay.observation_adapter import build_observation_payload
from bujji.observatory.comparison import CANONICAL_FIELD_ORDER, compare_sessions
from bujji.qualification.recorder import QualificationRecord, RuntimeOutcome
from bujji.qualification.report import _session_entry
from bujji.replay.validator import HistoricalSessionRecord


def _record(session_id="NIFTY-2026-07-22", trading_date="2026-07-22", spot=23996.25):
    return HistoricalSessionRecord(
        session_id=session_id,
        trading_date=trading_date,
        timestamp=f"{trading_date}T15:30:00",
        spot=spot,
        spot_as_of=f"{trading_date}T15:30:00",
        option_chain_entries=((24000.0, "CE", "2026-07-28", "NIFTY26JUL24000CE"),),
        option_chain_expiries=("2026-07-28",),
        option_chain_as_of=f"{trading_date}T15:30:00",
    )


def _fake_bridge_result(**overrides):
    base = {
        "market_context": "TRENDING_UP", "context_id": "CTX-1",
        "market_opinion": "BULLISH", "opinion_id": "OPN-1",
        "context_stability": "STABLE", "stability_id": "STB-1",
        "calibration": "CALIBRATED", "calibration_id": "CAL-1",
        "governance": "APPROVED", "governance_id": "GOV-1",
        "lifecycle": "ACTIVE", "lifecycle_id": "LFC-1",
        "contract": "COMPLETE", "contract_record_id": "CTR-1",
        "candle_count": 1,
        # Series 70 Phase 2 fields.
        "stability_reasoning_summary": "zero_transitions",
        "stability_transition_count": 0,
        "stability_persistence_length": 5,
        "stability_dimension_agreement": 1.0,
        "stability_confidence_variance": 0.0,
        "stability_context_lifetime": 5,
        "context_volatility": "STABLE",
        "context_liquidity": "UNKNOWN",
        "context_regime": "TREND",
        "context_conviction": "HIGH",
        "context_stability_dimension": "STABLE",
        "transition_events": [{"timestamp": "2026-07-22T15:20:00", "memory_type": "REGIME_TRANSITION"}],  # JSON list on the wire
    }
    base.update(overrides)
    return base


class _FakeCompletedProcess:
    def __init__(self, stdout_obj, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = json.dumps(stdout_obj)
        self.stderr = stderr


# ---------------------------------------------------------------------------
# PublishedState: new fields populated from the bridge's JSON output.
# ---------------------------------------------------------------------------


def test_published_state_carries_new_context_stability_fields():
    with patch(
        "bujji.mic_replay.publication_replay.subprocess.run",
        return_value=_FakeCompletedProcess(_fake_bridge_result()),
    ):
        state = replay_published_state_for_session([build_observation_payload(_record())])

    assert state.stability_reasoning_summary == "zero_transitions"
    assert state.stability_transition_count == 0
    assert state.stability_persistence_length == 5
    assert state.stability_dimension_agreement == 1.0
    assert state.stability_confidence_variance == 0.0
    assert state.stability_context_lifetime == 5
    assert state.context_volatility == "STABLE"
    assert state.context_liquidity == "UNKNOWN"
    assert state.context_regime == "TREND"
    assert state.context_conviction == "HIGH"
    assert state.context_stability_dimension == "STABLE"
    assert state.transition_events == ({"timestamp": "2026-07-22T15:20:00", "memory_type": "REGIME_TRANSITION"},)


def test_published_state_new_fields_default_none_when_bridge_omits_them():
    """Backward compatibility: a bridge result JSON that predates Series
    70 Phase 2 (no new keys at all) must never crash -- every new field
    defaults to None/() via .get(...), never fabricated.
    """
    fake = _fake_bridge_result()
    for k in (
        "stability_reasoning_summary", "stability_transition_count", "stability_persistence_length",
        "stability_dimension_agreement", "stability_confidence_variance", "stability_context_lifetime",
        "context_volatility", "context_liquidity", "context_regime", "context_conviction",
        "context_stability_dimension", "transition_events",
    ):
        del fake[k]

    with patch("bujji.mic_replay.publication_replay.subprocess.run", return_value=_FakeCompletedProcess(fake)):
        state = replay_published_state_for_session([build_observation_payload(_record())])

    assert state.stability_reasoning_summary is None
    assert state.stability_transition_count is None
    assert state.context_stability_dimension is None
    assert state.transition_events == ()
    # Pre-existing fields are unaffected.
    assert state.market_context == "TRENDING_UP"


def test_published_state_field_defaults_are_none_or_empty_tuple():
    """A PublishedState built without any Phase 2 kwargs at all (the
    dataclass-level default, exercised directly rather than through the
    bridge) must not require them -- confirms every new field is
    genuinely optional at the dataclass level, not just at the .get()
    call site.
    """
    state = PublishedState(
        market_context="TRENDING_UP", market_opinion="BULLISH", context_stability="STABLE",
        calibration="CALIBRATED", governance="APPROVED", lifecycle="ACTIVE", contract="COMPLETE",
        publication_ids={}, candle_count=1,
    )
    assert state.stability_reasoning_summary is None
    assert state.stability_transition_count is None
    assert state.stability_persistence_length is None
    assert state.stability_dimension_agreement is None
    assert state.stability_confidence_variance is None
    assert state.stability_context_lifetime is None
    assert state.context_volatility is None
    assert state.context_liquidity is None
    assert state.context_regime is None
    assert state.context_conviction is None
    assert state.context_stability_dimension is None
    assert state.transition_events == ()


# ---------------------------------------------------------------------------
# QualificationRecord.published_state + report._session_entry()
# ---------------------------------------------------------------------------


def _record_with_published_state(published_state):
    return QualificationRecord(
        replay_identifier="S1",
        timestamp="2026-07-22T15:30:00",
        strategy_decision=None,
        risk_decision=None,
        capital_decision=None,
        execution_plan=None,
        health_snapshot=type("H", (), {"overall": "HEALTHY"})(),
        circuit_decision=type("C", (), {"state": "CLOSED"})(),
        rate_limit_decision=type("R", (), {"state": "OK"})(),
        runtime_outcome=RuntimeOutcome(status="COMPLETED", reason="ok", shadow_result=None),
        qualification_fingerprint="FP-1",
        published_state=published_state,
    )


def test_session_entry_surfaces_new_keys_from_published_state():
    state = PublishedState(
        market_context="TRENDING_UP", market_opinion="BULLISH", context_stability="STABLE",
        calibration="CALIBRATED", governance="APPROVED", lifecycle="ACTIVE", contract="COMPLETE",
        publication_ids={}, candle_count=1,
        stability_reasoning_summary="zero_transitions", stability_transition_count=0,
        stability_persistence_length=5, stability_dimension_agreement=1.0,
        stability_confidence_variance=0.0, stability_context_lifetime=5,
        context_volatility="STABLE", context_liquidity="UNKNOWN", context_regime="TREND",
        context_conviction="HIGH", context_stability_dimension="STABLE",
        transition_events=({"timestamp": "2026-07-22T15:20:00", "memory_type": "REGIME_TRANSITION"},),
    )
    record = _record_with_published_state(state)
    entry = _session_entry(record)

    assert entry["stability_reasoning_summary"] == "zero_transitions"
    assert entry["stability_transition_count"] == 0
    assert entry["stability_persistence_length"] == 5
    assert entry["stability_dimension_agreement"] == 1.0
    assert entry["stability_confidence_variance"] == 0.0
    assert entry["stability_context_lifetime"] == 5
    assert entry["context_volatility"] == "STABLE"
    assert entry["context_liquidity"] == "UNKNOWN"
    assert entry["context_regime"] == "TREND"
    assert entry["context_conviction"] == "HIGH"
    assert entry["context_stability_dimension"] == "STABLE"
    assert entry["transition_events"] == ({"timestamp": "2026-07-22T15:20:00", "memory_type": "REGIME_TRANSITION"},)


def test_session_entry_new_keys_none_when_published_state_absent():
    """Backward compatibility: a QualificationRecord built before Series
    70 Phase 2 (published_state defaults to None) must produce every new
    key as None/[] -- never crash, never fabricate.
    """
    record = _record_with_published_state(None)
    entry = _session_entry(record)

    assert entry["stability_reasoning_summary"] is None
    assert entry["stability_transition_count"] is None
    assert entry["context_stability_dimension"] is None
    assert entry["transition_events"] is None
    # Pre-existing keys are unaffected.
    assert entry["id"] == "S1"
    assert entry["outcome"] == "COMPLETED"


# ---------------------------------------------------------------------------
# CANONICAL_FIELD_ORDER placement + divergence chain.
# ---------------------------------------------------------------------------


def test_new_scalar_context_fields_precede_context_stability_in_canonical_order():
    idx_stability = CANONICAL_FIELD_ORDER.index("context_stability")
    for field_name in (
        "context_volatility", "context_liquidity", "context_regime",
        "context_conviction", "context_stability_dimension",
    ):
        assert CANONICAL_FIELD_ORDER.index(field_name) < idx_stability


def test_stability_scalar_detail_fields_precede_context_stability_in_canonical_order():
    idx_stability = CANONICAL_FIELD_ORDER.index("context_stability")
    for field_name in (
        "stability_transition_count", "stability_persistence_length",
        "stability_dimension_agreement", "stability_confidence_variance",
        "stability_context_lifetime",
    ):
        assert CANONICAL_FIELD_ORDER.index(field_name) < idx_stability


def test_free_text_and_list_fields_excluded_from_canonical_order():
    assert "stability_reasoning_summary" not in CANONICAL_FIELD_ORDER
    assert "transition_events" not in CANONICAL_FIELD_ORDER


def _session(session_id, **overrides):
    base = {
        "id": session_id,
        "outcome": "COMPLETED",
        "market_context": "TRENDING_UP",
        "context_volatility": "STABLE",
        "context_liquidity": "UNKNOWN",
        "context_regime": "TREND",
        "context_conviction": "HIGH",
        "context_stability_dimension": "STABLE",
        "market_opinion": "BULLISH",
        "context_stability": "STABLE",
    }
    base.update(overrides)
    return base


def test_context_stability_dimension_diverges_before_context_stability_itself():
    """A divergence-chain example: two sessions where only the per-cycle
    MarketContext dimension differs (context_stability_dimension) while
    the whole-replay context_stability classification happens to be the
    same on both sides -- first_divergence must land on the earlier
    (upstream) field, per CANONICAL_FIELD_ORDER, not on context_stability.
    """
    session_a = _session("S1", context_stability_dimension="STABLE")
    session_b = _session("S1", context_stability_dimension="CHANGING")

    diff = compare_sessions(session_a, session_b)

    assert diff.first_divergence == "context_stability_dimension"
    assert "context_stability" not in diff.changed_fields

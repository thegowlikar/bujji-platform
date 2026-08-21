"""Tests for MFE/MAE -- Phase 5, Nervous System Integration. Confirms
the honest-None-when-no-history contract, correct max/min extraction
when history IS supplied, and that both PositionOutcomeAttribution and
OutcomeMemoryRecord carry the fields through without breaking anything
that came before (see test_outcome_attribution.py/test_outcome_memory.py
for the full existing suites, unmodified and still green)."""
from __future__ import annotations

from bujji.outcome_attribution.engine import compute_mfe_mae


class TestComputeMfeMae:
    def test_empty_history_is_honest_none_not_zero(self):
        assert compute_mfe_mae(()) == (None, None)

    def test_none_entries_are_filtered_not_treated_as_zero(self):
        assert compute_mfe_mae((None, None)) == (None, None)

    def test_real_history_returns_max_and_min(self):
        mfe, mae = compute_mfe_mae((100.0, -50.0, 200.0, -300.0, 50.0))
        assert mfe == 200.0
        assert mae == -300.0

    def test_single_value_history(self):
        assert compute_mfe_mae((42.0,)) == (42.0, 42.0)

    def test_mixed_none_and_real_values_filters_none(self):
        mfe, mae = compute_mfe_mae((None, 10.0, None, -5.0))
        assert mfe == 10.0
        assert mae == -5.0

    def test_default_call_with_no_argument_is_none(self):
        assert compute_mfe_mae() == (None, None)

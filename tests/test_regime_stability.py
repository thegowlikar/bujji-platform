"""The stability gate: unanimity across sampling rates, or no answer.

WHY IT EXISTS. An adversarial replay of 478 real captured snapshots found
the derived regime moved with the SPACING alone -- 30/45/60/90/120s gave
TRENDING, 180s gave CORRECTING. A configurable spacing would therefore let
a number in a YAML file decide the day's regime while looking like a market
judgement. NO_TRADE from thin evidence is honest; a regime picked by a knob
is not.

These tests pin the properties that stop the gate degrading into a rubber
stamp -- above all, that a stride which fails to answer makes the verdict
UNSTABLE rather than being quietly dropped from the vote.
"""
from __future__ import annotations

import pytest

from bujji.regime_stability import (
    INSUFFICIENT, UNSTABLE, StabilityVerdict, assess_stability, subsample,
)


def const(regime):
    return lambda window: regime


class TestSubsample:
    def test_takes_every_nth_anchored_at_the_first(self):
        obs = list(range(10))
        assert subsample(obs, 1) == tuple(range(10))
        assert subsample(obs, 2) == (0, 2, 4, 6, 8)
        assert subsample(obs, 4) == (0, 4, 8)

    def test_all_strides_share_a_start_instant(self):
        """A difference between strides must be a difference of sampling
        RATE, never of when the window opened."""
        obs = list(range(20))
        assert subsample(obs, 1)[0] == subsample(obs, 2)[0] == subsample(obs, 4)[0]

    def test_a_zero_or_negative_stride_is_refused(self):
        for bad in (0, -1):
            with pytest.raises(ValueError):
                subsample(list(range(10)), bad)


class TestUnanimity:
    def test_agreement_returns_the_regime(self):
        v = assess_stability(list(range(20)), const("RANGE_PERSISTENCE"))
        assert v.is_stable is True
        assert v.regime == "RANGE_PERSISTENCE"
        assert set(v.by_stride.values()) == {"RANGE_PERSISTENCE"}

    def test_disagreement_returns_no_regime(self):
        """THE CORE CASE. Same tape, different sampling, different answer."""
        answers = {1: "TRENDING", 2: "TRENDING", 4: "CORRECTING"}
        v = assess_stability(list(range(20)), lambda w: answers[20 // len(w)] if 20 // len(w) in answers else "X")
        assert v.is_stable is False
        assert v.regime is None
        assert UNSTABLE in v.reason

    def test_the_reason_names_what_disagreed(self):
        seen = {}
        def derive(window):
            r = "TRENDING" if len(window) > 6 else "CORRECTING"
            seen[len(window)] = r
            return r
        v = assess_stability(list(range(20)), derive)
        assert "stride" in v.reason
        assert "TRENDING" in v.reason and "CORRECTING" in v.reason
        assert set(v.disagreement) == {"TRENDING", "CORRECTING"}

    def test_there_is_no_majority_rule(self):
        """Two-against-one must NOT win. A majority rule would reintroduce
        the arbitrariness the gate exists to remove."""
        def derive(window):
            return "CORRECTING" if len(window) == 5 else "TRENDING"
        v = assess_stability(list(range(20)), derive)
        assert v.regime is None, "a 2-1 split was resolved instead of refused"


class TestAStrideThatCannotAnswerMakesItUnstable:
    def test_a_none_regime_from_one_stride_blocks_the_verdict(self):
        """Dropping the strides that failed to answer and agreeing among
        the rest is exactly how a stability check becomes a rubber stamp."""
        def derive(window):
            return None if len(window) <= 5 else "RANGE_PERSISTENCE"
        v = assess_stability(list(range(20)), derive)
        assert v.is_stable is False
        assert v.regime is None
        assert UNSTABLE in v.reason
        assert "formed no regime at all" in v.reason

    def test_all_none_is_also_unstable_not_agreement(self):
        """Three strides agreeing on 'no opinion' is not a regime."""
        v = assess_stability(list(range(20)), const(None))
        assert v.regime is None and v.is_stable is False


class TestThinEvidence:
    def test_too_few_observations_after_subsampling_refuses(self):
        v = assess_stability(list(range(6)), const("RANGE_PERSISTENCE"))
        assert v.is_stable is False
        assert INSUFFICIENT in v.reason
        assert v.observations_used[4] < 4

    def test_the_reason_says_how_to_fix_it(self):
        v = assess_stability(list(range(6)), const("X"))
        assert "Poll longer" in v.reason

    def test_an_empty_series_refuses_rather_than_defaulting(self):
        v = assess_stability([], const("X"))
        assert v.regime is None and v.is_stable is False

    def test_exactly_enough_is_enough(self):
        """16 observations gives stride 4 exactly 4 -- the measured
        threshold for a real MDI opinion."""
        v = assess_stability(list(range(16)), const("RANGE_PERSISTENCE"))
        assert v.observations_used[4] == 4
        assert v.is_stable is True


class TestVerdictIsAuditable:
    def test_every_stride_is_recorded_even_when_stable(self):
        v = assess_stability(list(range(20)), const("X"))
        assert set(v.by_stride) == {1, 2, 4}
        assert set(v.observations_used) == {1, 2, 4}

    def test_observation_counts_are_recorded(self):
        v = assess_stability(list(range(20)), const("X"))
        assert v.observations_used[1] == 20
        assert v.observations_used[2] == 10
        assert v.observations_used[4] == 5

    def test_a_stable_verdict_still_states_what_it_survived(self):
        v = assess_stability(list(range(20)), const("RANGE_PERSISTENCE"))
        assert "stable" in v.reason and "RANGE_PERSISTENCE" in v.reason

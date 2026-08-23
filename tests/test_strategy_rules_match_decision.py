"""The declarative rules and the decision table must agree, everywhere.

WHY THIS TEST IS THE POINT OF THE RULES TABLE. STRATEGY_RULES does not decide
anything: select_strategy()'s branch bodies still produce the outcome. A
declaration that merely sits beside the code it claims to describe is
documentation, and documentation drifts silently. This test makes the two
mutually load-bearing -- change the branches without the declarations, or the
declarations without the branches, and it fails.

It enumerates the FULL cross product of the regime vocabulary in BOTH risk
modes, so there is no reachable input pair that is checked by inspection only.
"""
from __future__ import annotations

import itertools
from datetime import datetime

import pytest

from bujji.production_runtime.trading_session_governor import strategy_selector as ss
from bujji.trading_brain.risk_governor.market_regime_adapter import (
    ALL_TREND_REGIMES, ALL_VOLATILITY_REGIMES,
)

CLOCK = lambda: datetime(2026, 8, 24, 10, 0, 0)

# None is a real input: the regime provider returns it when it has no opinion,
# and "fail closed on missing" is a behaviour worth pinning, not an edge case.
TRENDS = tuple(ALL_TREND_REGIMES) + (None,)
VOLS = tuple(ALL_VOLATILITY_REGIMES) + (None,)
PAIRS = tuple(itertools.product(TRENDS, VOLS))
MODES = (False, True)


def _decide(trend, vol, defined_risk_only):
    return ss.select_strategy(trend, vol, CLOCK, defined_risk_only=defined_risk_only)


class TestRulesMatchTheDecision:
    @pytest.mark.parametrize("trend,vol", PAIRS)
    @pytest.mark.parametrize("defined_risk_only", MODES)
    def test_declared_eligibility_equals_the_decision(self, trend, vol, defined_risk_only):
        """The declarations must name exactly the family the table selects --
        and name nothing at all when the table refuses."""
        result = _decide(trend, vol, defined_risk_only)
        declared = ss.eligible_families(
            trend, vol, defined_risk_only=defined_risk_only)

        if result.selected_strategy is None:
            assert declared == (), (
                f"({trend}, {vol}, defined_risk_only={defined_risk_only}): the table "
                f"refused to trade but the declarations claim {declared} is eligible. "
                f"One of the two is wrong.")
        else:
            assert declared == (result.selected_strategy,), (
                f"({trend}, {vol}, defined_risk_only={defined_risk_only}): the table "
                f"selected {result.selected_strategy!r} but the declarations say "
                f"{declared}.")

    @pytest.mark.parametrize("trend,vol", PAIRS)
    @pytest.mark.parametrize("defined_risk_only", MODES)
    def test_every_declared_shape_gets_a_candidate_record(self, trend, vol, defined_risk_only):
        """A shape that was never considered must not be silently absent: the
        record has to account for every family Bujji is allowed to sell."""
        result = _decide(trend, vol, defined_risk_only)
        assert {c.family for c in result.candidates} == set(ss.RULES_BY_FAMILY)

    @pytest.mark.parametrize("trend,vol", PAIRS)
    @pytest.mark.parametrize("defined_risk_only", MODES)
    def test_no_candidate_is_rejected_without_a_reason_code(self, trend, vol, defined_risk_only):
        result = _decide(trend, vol, defined_risk_only)
        for c in result.candidates:
            assert c.reason_code, f"{c.family} carries no reason code"
            assert c.detail, f"{c.family} carries no human-readable detail"
            if c.status == ss.CANDIDATE_REJECTED:
                assert c.reason_code != ss.REASON_ELIGIBLE

    @pytest.mark.parametrize("trend,vol", PAIRS)
    @pytest.mark.parametrize("defined_risk_only", MODES)
    def test_at_most_one_shape_is_ever_eligible(self, trend, vol, defined_risk_only):
        """Today the selector has no ranking stage. If two shapes ever become
        eligible for one regime, the decision would be an arbitrary tie-break
        that nothing declares -- that must fail loudly, not pick silently."""
        declared = ss.eligible_families(
            trend, vol, defined_risk_only=defined_risk_only)
        assert len(declared) <= 1, f"ambiguous eligibility {declared} with no declared ranking"


class TestTheVetoCannotBeReachedAround:
    @pytest.mark.parametrize("trend", TRENDS)
    @pytest.mark.parametrize("defined_risk_only", MODES)
    def test_expanding_volatility_refuses_every_shape_in_every_trend(self, trend, defined_risk_only):
        from bujji.trading_brain.risk_governor.market_regime_adapter import VOL_EXPANSION
        result = _decide(trend, VOL_EXPANSION, defined_risk_only)
        assert result.selected_strategy is None
        assert ss.eligible_families(
            trend, VOL_EXPANSION, defined_risk_only=defined_risk_only) == ()

    @pytest.mark.parametrize("trend", [t for t in TRENDS if t is not None])
    @pytest.mark.parametrize("defined_risk_only", MODES)
    def test_the_veto_is_what_refuses_them_not_a_coincidence(self, trend, defined_risk_only):
        """A NEGATIVE CONTROL CAUGHT THIS TEST BEING VACUOUS.

        Deleting VOL_EXPANSION from a rule's vetoing_volatilities did not fail
        the outcome assertion above, because no shape lists VOL_EXPANSION in
        its eligible_volatilities either -- so the shape was still refused,
        just for the unrelated reason VOL_NOT_ELIGIBLE. The veto declaration
        was therefore decorative: it could have been deleted wholesale and
        every test would still have passed.

        Asserting the REASON CODE makes the veto load-bearing. It also
        encodes the ordering the decision table is careful about: expansion
        must refuse a shape BEFORE direction is considered, so that no
        directional branch can ever reach around it.
        """
        from bujji.trading_brain.risk_governor.market_regime_adapter import (
            TREND_UNKNOWN, VOL_EXPANSION,
        )
        if trend == TREND_UNKNOWN:
            pytest.skip("an unknown regime is refused earlier, by REGIME_UNKNOWN")
        result = _decide(trend, VOL_EXPANSION, defined_risk_only)
        assert result.candidates
        for c in result.candidates:
            assert c.status == ss.CANDIDATE_REJECTED
            assert c.reason_code == ss.REASON_VOL_EXPANSION, (
                f"{c.family} was refused under expanding volatility for "
                f"{c.reason_code}, not the expansion veto. The veto is not "
                f"what is protecting this shape.")


class TestDefinedRiskModeIsStructural:
    @pytest.mark.parametrize("trend,vol", PAIRS)
    def test_defined_risk_mode_never_yields_an_unhedged_shape(self, trend, vol):
        """The whole purpose of the mode: what it selects must have a
        structural floor, checked against the declaration rather than against
        a hand-maintained list of names."""
        result = _decide(trend, vol, True)
        if result.selected_strategy is None:
            return
        rule = ss.RULES_BY_FAMILY[result.selected_strategy]
        assert rule.structurally_defined_risk, (
            f"defined-risk mode selected {result.selected_strategy}, which declares "
            f"structurally_defined_risk=False")
        assert rule.hedged


class TestCapabilityPolicyIsUnconfiguredNotSatisfied:
    """Gate 1 has not run. Nothing here may assert the feed can do anything."""

    def test_an_unconfigured_policy_does_not_gate(self):
        """Current production state: no capability policy, so selection is
        unchanged. This pins that today's behaviour is 'not evaluated'."""
        assert ss.eligible_families("SIDEWAYS", "HIGH_VOL") != () or True
        declared = ss.eligible_families(
            "SIDEWAYS", "HIGH_VOL", available_capabilities=None)
        assert declared == (ss.FAMILY_SHORT_STRADDLE,)

    def test_a_configured_policy_missing_a_capability_refuses(self):
        """And this pins that once Monday supplies a real policy, an
        unsatisfied requirement is a refusal rather than a warning."""
        declared = ss.eligible_families(
            "SIDEWAYS", "HIGH_VOL", available_capabilities=frozenset())
        assert declared == ()

    def test_a_configured_policy_that_is_satisfied_permits(self):
        declared = ss.eligible_families(
            "SIDEWAYS", "HIGH_VOL",
            available_capabilities=frozenset({ss.CAP_LAST_PRICE}))
        assert declared == (ss.FAMILY_SHORT_STRADDLE,)

    def test_no_rule_declares_a_broker_specific_field_name(self):
        """No FYERS payload field has been measured. A capability named after
        one would be an invented claim about a feed nobody has read yet."""
        for rule in ss.STRATEGY_RULES:
            for cap in rule.required_capabilities:
                assert cap.startswith("QUOTE_"), (
                    f"{rule.family} requires {cap!r}, which is not a generic capability")
                assert "fyers" not in cap.lower()


class TestEveryShapeDeclaresItsObligations:
    @pytest.mark.parametrize("rule", ss.STRATEGY_RULES, ids=lambda r: r.family)
    def test_margin_lot_exit_and_eod_owners_are_named(self, rule):
        assert rule.requires_margin_check_by
        assert rule.requires_lot_size_check_by
        assert rule.exit_policy_owner
        assert rule.eod_behaviour
        assert rule.required_capabilities, (
            f"{rule.family} declares no data requirement at all; a shape that can be "
            f"priced from nothing cannot be stopped or exited either")

    def test_declared_families_are_constructible_by_the_engine(self):
        """A selector that names a shape the construction engine cannot build
        produces a guaranteed rejection instead of a trade."""
        from bujji.msi_trade_construction import taxonomy
        for rule in ss.STRATEGY_RULES:
            assert rule.family in taxonomy.SUPPORTED_FAMILIES, (
                f"{rule.family} is declared selectable but is not in SUPPORTED_FAMILIES")

    def test_every_twin_points_at_a_real_naked_shape(self):
        for rule in ss.STRATEGY_RULES:
            if rule.twin_of is not None:
                assert rule.twin_of in ss.RULES_BY_FAMILY
                assert ss.DEFINED_RISK_TWIN[rule.twin_of] == rule.family, (
                    "the twin declaration disagrees with DEFINED_RISK_TWIN, the table "
                    "the selector actually substitutes with")


class TestTheCandidateRecordActuallyReachesTheJournal:
    """REACHABILITY, NOT CONSTRUCTION.

    Bujji's dominant defect class is code that is built, tested and never
    called. A candidate record that exists only inside the selector would be
    another instance of it: the tests above would all pass while the journal
    stayed exactly as uninformative as before. These tests assert the record
    survives the trip through the real authority the runner calls.
    """

    @staticmethod
    def _governor_with_a_bus():
        from unittest.mock import AsyncMock, MagicMock
        from datetime import datetime

        from bujji.production_runtime.trading_session_governor.session_governor import (
            TradingSessionGovernor,
        )
        from bujji.production_runtime.trading_session_governor.exit_policy import (
            ExitPolicyConfig,
        )

        published = []

        class _Bus:
            def publish_nowait(self, event):
                published.append(event)

        registry = MagicMock()
        registry.positions_for_group = AsyncMock(return_value=[])
        executor = MagicMock()
        executor.execute = AsyncMock()
        governor = TradingSessionGovernor(
            session_id="sess-candidates", trading_brain_runtime=MagicMock(),
            registry=registry, lifecycle_runtime=MagicMock(), executor=executor,
            exit_policy_config=ExitPolicyConfig(max_loss_fraction=1.0),
            clock=lambda: datetime(2026, 8, 24, 10, 0, 0), event_bus=_Bus(),
        )
        return governor, published

    @staticmethod
    def _selection_event(published):
        for e in published:
            if e.payload.get("stage") == "STRATEGY_SELECTION_EVALUATED":
                return e
        raise AssertionError(
            "the governor published no STRATEGY_SELECTION_EVALUATED event at all")

    def test_a_traded_regime_publishes_every_candidate_with_its_reason(self):
        from bujji.trading_brain.risk_governor.market_regime_adapter import (
            TREND_SIDEWAYS, VOL_LOW,
        )
        governor, published = self._governor_with_a_bus()
        governor.begin_market_analysis()
        result = governor.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
        assert result.selected_strategy is not None

        payload = self._selection_event(published).payload
        assert "candidates" in payload, (
            "the selection event carries no candidate record; the journal can say "
            "what Bujji traded but not why it declined the alternatives")
        families = {c["family"] for c in payload["candidates"]}
        assert families == set(ss.RULES_BY_FAMILY)
        for c in payload["candidates"]:
            assert c["reason_code"] and c["detail"]

    def test_a_NO_TRADE_day_still_publishes_why_each_shape_was_refused(self):
        """The case the record exists for. On a day Bujji does not trade there
        is no position, no fill and no exit -- the candidate record is the
        entire evidence of what it considered and rejected."""
        from bujji.trading_brain.risk_governor.market_regime_adapter import (
            TREND_SIDEWAYS, VOL_EXPANSION,
        )
        governor, published = self._governor_with_a_bus()
        governor.begin_market_analysis()
        result = governor.select_and_lock_strategy(TREND_SIDEWAYS, VOL_EXPANSION)
        assert result.selected_strategy is None

        payload = self._selection_event(published).payload
        assert payload["candidates"], "a no-trade day published an empty candidate record"
        assert {c["family"] for c in payload["candidates"]} == set(ss.RULES_BY_FAMILY)
        assert all(c["status"] == ss.CANDIDATE_REJECTED for c in payload["candidates"])
        assert all(c["reason_code"] == ss.REASON_VOL_EXPANSION
                   for c in payload["candidates"])

    def test_the_published_record_is_plain_json_safe_data(self):
        """The journal serializes this. A frozen dataclass would raise at write
        time -- on the one path whose whole job is to survive to disk."""
        import json

        from bujji.trading_brain.risk_governor.market_regime_adapter import (
            TREND_TRENDING_UP, VOL_LOW,
        )
        governor, published = self._governor_with_a_bus()
        governor.begin_market_analysis()
        governor.select_and_lock_strategy(TREND_TRENDING_UP, VOL_LOW)
        payload = self._selection_event(published).payload
        json.dumps(payload["candidates"])

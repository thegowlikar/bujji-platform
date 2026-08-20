"""Size and stop move together, or the stop means nothing.

AUDIT FINDING 2026-08-20. Three rupee amounts were configured as flat
per-POSITION totals beside a separately configured size:

    desired_quantity: 1
    requested_risk: 5000.0                     -> initial_risk (where the stop sits)
    proposed_trade_effect.additional_margin    -> capital check
    proposed_trade_effect.additional_max_loss  -> risk budget governor

`strategy_risk_adapter` receives desired_quantity and requested_risk side by
side and never relates them, so only the caller can. Nothing did. Raising
desired_quantity to 5 quintupled the real risk while the stop stayed at
Rs 5,000 -- it would fire on noise, and the risk-budget and capital checks
would be sized for a position one fifth the size of the one actually taken.

The figures are now read PER LOT and multiplied by the lots, in one place.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji_options_os_runner import OptionsOSRunner


class _Stub:
    """Only what _risk_budget touches."""

    _risk_budget = OptionsOSRunner._risk_budget

    def __init__(self, lots=1, risk=5000.0, margin=10000.0, max_loss=5000.0,
                 daily_limit=25000.0):
        session = {"desired_quantity": lots, "requested_risk": risk,
                   "proposed_trade_effect": {"additional_margin": margin,
                                             "additional_max_loss": max_loss}}
        self._session_cfg = session
        self._config = {"session": session,
                        "capital_snapshot": {"daily_loss_limit": daily_limit}}
        self._logger = logging.getLogger("risk-budget-test")


class TestEveryFigureScalesWithTheLots:
    def test_one_lot_is_unchanged_from_before_the_fix(self):
        """The whole point: today's production config behaves identically."""
        b = _Stub(lots=1)._risk_budget()
        assert b["requested_risk"] == 5000.0
        assert b["additional_margin"] == 10000.0
        assert b["additional_max_loss"] == 5000.0

    @pytest.mark.parametrize("lots", [1, 2, 5, 10])
    def test_all_three_figures_scale_together(self, lots):
        b = _Stub(lots=lots)._risk_budget()
        assert b["lots"] == lots
        assert b["requested_risk"] == 5000.0 * lots
        assert b["additional_margin"] == 10000.0 * lots
        assert b["additional_max_loss"] == 5000.0 * lots

    def test_the_stop_stays_proportional_to_the_position(self):
        """The defect in one assertion: five times the size, five times the
        stop -- not the same rupee stop on a five-times-larger position."""
        one = _Stub(lots=1)._risk_budget()["requested_risk"]
        five = _Stub(lots=5)._risk_budget()["requested_risk"]
        assert five == 5 * one

    def test_the_per_lot_figure_is_reported_alongside_the_total(self):
        """So a reader of the session summary can see the derivation rather
        than a bare total that could have come from anywhere."""
        b = _Stub(lots=3)._risk_budget()
        assert b["per_lot_requested_risk"] == 5000.0
        assert b["requested_risk"] == 15000.0


class TestDegenerateSizes:
    def test_zero_or_negative_lots_floor_at_one(self):
        """A position was taken, so the size cannot be zero. Flooring beats
        producing a zero risk budget, which would disable the stop entirely."""
        for lots in (0, -3):
            assert _Stub(lots=lots)._risk_budget()["lots"] == 1

    def test_missing_config_falls_back_to_the_documented_defaults(self):
        stub = _Stub()
        stub._session_cfg = {}
        b = stub._risk_budget()
        assert b["lots"] == 1 and b["requested_risk"] == 5000.0


class TestTheIncoherenceWarning:
    def test_a_stop_wider_than_the_daily_limit_is_flagged(self, caplog):
        """The daily limit would halt the session before the position's own
        stop could fire -- reachable only by raising the size, which is
        exactly the case this scaling exists for."""
        with caplog.at_level(logging.WARNING):
            _Stub(lots=10, daily_limit=25000.0)._risk_budget()   # 50,000 > 25,000
        assert any("exceeds the daily loss limit" in r.message for r in caplog.records)

    def test_a_coherent_budget_warns_about_nothing(self, caplog):
        with caplog.at_level(logging.WARNING):
            _Stub(lots=1, daily_limit=25000.0)._risk_budget()
        assert not any("daily loss limit" in r.message for r in caplog.records)

    def test_no_daily_limit_configured_is_not_an_error(self):
        stub = _Stub(lots=10)
        stub._config = {"capital_snapshot": {}}
        assert stub._risk_budget()["requested_risk"] == 50000.0


class TestTheRunnerUsesOneSource:
    def test_entry_and_management_read_the_same_helper(self):
        """The coupling only holds if BOTH the entry sizing and the exit
        policy's initial_risk come from the same computation. Two call sites
        reading the raw config independently is how they drifted."""
        source = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        # Exactly two call sites: the entry sizing and the exit policy's
        # initial_risk. Both must read the helper, never the raw config.
        assert source.count("self._risk_budget()") == 2
        # The raw flat reads that caused the defect must be gone.
        assert 'session_cfg.get("requested_risk"' not in source
        assert 'self._session_cfg.get("requested_risk"' not in source

    def test_the_production_config_documents_the_per_lot_meaning(self):
        text = (REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text()
        assert "PER LOT" in text
        assert "per lot -> initial_risk" in text

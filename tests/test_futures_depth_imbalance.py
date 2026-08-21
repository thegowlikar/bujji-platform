"""Per-cycle order-book depth: the only genuine PRESSURE signal Bujji has.

OPERATOR DIRECTIVE 2026-08-20. Not spot price (price/structure lenses), not
option open interest (positioning lens), not basis (futures lens) -- resting
quantity on each side of the futures book.

FIELD NAMES ARE VERIFIED, NOT GUESSED. get_depth()'s own docstring forbids
assuming a bids/asks shape without confirming against a live response:
"Fabricating that shape from an unverified guess would be exactly the kind of
invented field this codebase's Layer 0 discipline forbids." `totalbuyqty` and
`totalsellqty` are recorded in the certified discovery artifact
data_certification/fyers_depth_discovery_20260813.json, with a real captured
example of 266760 / 318435. Only those two aggregates are consumed; the
5-level ladders are deliberately not parsed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.futures_observation.engine import compute_depth_imbalance
from bujji.market_perception.models import FutureSnapshot


class TestTheFieldNamesAreCertified:
    def test_the_discovery_artifact_records_both_aggregates(self):
        """If this artifact ever stops naming these fields, the adapter is
        reading something nobody verified."""
        artifact = json.loads(
            (REPO_ROOT / "data_certification"
             / "fyers_depth_discovery_20260813.json").read_text())
        text = json.dumps(artifact)
        assert "totalbuyqty" in text and "totalsellqty" in text

    def test_the_adapter_reads_only_the_verified_fields(self):
        source = (REPO_ROOT / "bujji" / "market_perception" / "futures_adapter.py").read_text()
        assert '"totalbuyqty"' in source and '"totalsellqty"' in source
        # The unverified ladders must not be parsed.
        assert '"bids"' not in source and '"ask"' not in source


class TestTheImbalanceIsArithmeticOnly:
    def test_the_real_captured_example(self):
        """266760 bid vs 318435 ask, straight from the certified artifact."""
        assert compute_depth_imbalance(266760, 318435) == pytest.approx(-0.0883, abs=1e-4)

    @pytest.mark.parametrize("buy,sell,expected", [
        (500000, 100000, +2 / 3),
        (100000, 500000, -2 / 3),
        (200000, 200000, 0.0),
        (1, 0, +1.0),
        (0, 1, -1.0),
    ])
    def test_it_is_bounded_and_signed(self, buy, sell, expected):
        assert compute_depth_imbalance(buy, sell) == pytest.approx(expected)

    def test_bids_heavier_is_positive(self):
        assert compute_depth_imbalance(300000, 200000) > 0


class TestAbsenceIsNotBalance:
    @pytest.mark.parametrize("buy,sell", [(None, None), (266760, None), (None, 318435)])
    def test_a_missing_side_yields_none_not_zero(self, buy, sell):
        """A zero imbalance is a MEASUREMENT of a balanced book. Absence is
        not -- collapsing them would let a failed poll read as a balanced
        market, which is a directional claim nobody observed."""
        assert compute_depth_imbalance(buy, sell) is None

    def test_an_empty_book_yields_none(self):
        assert compute_depth_imbalance(0, 0) is None

    def test_a_measured_balance_is_zero_not_none(self):
        """The other half of the same distinction."""
        assert compute_depth_imbalance(200000, 200000) == 0.0


class TestTheSnapshotCarriesIt:
    def test_the_fields_default_to_absent(self):
        fields = FutureSnapshot.__dataclass_fields__
        assert fields["total_buy_qty"].default is None
        assert fields["total_sell_qty"].default is None

    def test_a_snapshot_without_depth_still_constructs(self):
        """Depth is additive: it must never cost the futures read."""
        snap = FutureSnapshot(symbol="NSE:NIFTY26AUGFUT", ltp=24416.2, volume=103415,
                              open_interest=None, basis=45.0, premium_discount=45.0)
        assert compute_depth_imbalance(snap.total_buy_qty, snap.total_sell_qty) is None


class TestTheFetchNeverCostsTheFuturesRead:
    def test_a_failing_depth_call_leaves_the_snapshot_intact(self):
        source = (REPO_ROOT / "bujji" / "market_perception" / "futures_adapter.py").read_text()
        # The depth call is wrapped and its failure sets both to None rather
        # than propagating -- the futures quote already succeeded by then.
        assert "except Exception:" in source
        assert "total_buy_qty = total_sell_qty = None" in source

    def test_the_depth_call_is_made_once_per_cycle(self):
        """One extra call against the host-wide FYERS budget -- negligible at
        ~8.3/s, but it should be one, not one per leg."""
        source = (REPO_ROOT / "bujji" / "market_perception" / "futures_adapter.py").read_text()
        assert source.count("get_depth(") == 1

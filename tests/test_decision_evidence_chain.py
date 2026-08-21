"""The evidence a decision cites is actually written down.

THE DEFECT (2026-08-20, first live continuous session). Decisions cited 346
distinct supporting observation ids; not one resolved anywhere on disk. The
campaign report simultaneously printed "Explanation completeness: 100%",
because that metric counts POPULATED FIELDS, not whether what they point at
exists. Every trade taken under that regime would have been unauditable and
therefore unlearnable.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.market_perception.models import (
    HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot, OptionLeg,
    FutureSnapshot, SpotSnapshot, VixSnapshot,
)
from bujji.market_state_builder.market_state import MarketStateBuilder
from bujji.shadow_observatory import evidence_store as E

_CFG = OptionChainConfig(**{p.name: 2 for p in inspect.signature(OptionChainConfig).parameters.values()
                            if p.default is inspect.Parameter.empty})


def _leg(strike, kind, px):
    return OptionLeg(symbol=f"NSE:NIFTY26AUG{int(strike)}{kind}", strike=strike,
                     option_type=kind, ltp=px, bid=px - 0.5, ask=px + 0.5, spread=1.0,
                     volume=1200.0, open_interest=814450.0,
                     iv=None, delta=None, gamma=None, theta=None, vega=None)


def _snap(ts, spot, *, chain=True, futures=True, shift=0.0):
    atm = round(spot / 50) * 50
    legs = tuple(_leg(atm + d, k, max(1.0, 120 - abs(d) / 10 + shift))
                 for d in (-100, -50, 0, 50, 100) for k in ("CE", "PE"))
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=812.4,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NSE:NIFTY50-INDEX", ltp=spot),
        vix=VixSnapshot(value=11.4, prev_close=11.2),
        futures=(FutureSnapshot(symbol="NSE:NIFTY26AUGFUT", ltp=spot + 45, volume=103415,
                                open_interest=None, basis=45.0, premium_discount=0.18)
                 if futures else None),
        option_chain=(OptionChainSnapshot(underlying="NIFTY", expiry="2026-08-25",
                                          atm_strike=atm, config=_CFG, legs=legs)
                      if chain else None))


def _drive(path, prices):
    builder = MarketStateBuilder()
    assessment = None
    for i, px in enumerate(prices):
        s = _snap(f"2026-08-20T09:{21 + i:02d}:42.911078+05:30", px, shift=i * 3)
        assessment = builder.process(s)
        E.append_evidence(path, E.evidence_records_for_snapshot(s))
    return assessment


class TestTheTrailIsWhole:
    def test_every_id_the_real_lenses_cite_resolves(self, tmp_path):
        """THE test. 0/346 was the defect; this asserts the inverse."""
        path = str(tmp_path / "decision_evidence.jsonl")
        a = _drive(path, [24416.2, 24421.5, 24430.1, 24428.8, 24440.3, 24455.9, 24460.2])
        index = E.load_evidence_index(path)
        cited = total = 0
        for name in ("price_structure", "market_structure", "participant_positioning"):
            lens = getattr(a, name, None)
            for oid in (getattr(lens, "supporting_observation_ids", ()) or ()):
                total += 1
                cited += 1 if oid in index else 0
        assert total > 0, "the lenses cited nothing -- this test proves nothing"
        assert cited == total, f"{total - cited} of {total} cited ids do not resolve"

    def test_option_legs_reach_the_store(self, tmp_path):
        """participant_positioning cited 42 option-leg ids on 2026-08-20."""
        path = str(tmp_path / "e.jsonl")
        _drive(path, [24416.2, 24421.5, 24430.1])
        roles = {r["role"] for r in E.load_evidence_index(path).values()}
        assert {"spot", "vix", "futures", "option_leg"} <= roles

    def test_a_resolved_record_carries_the_actual_observed_value(self, tmp_path):
        """Resolving must yield the fact, not merely confirm an id existed."""
        path = str(tmp_path / "e.jsonl")
        s = _snap("2026-08-20T09:21:42.911078+05:30", 24416.2)
        E.append_evidence(path, E.evidence_records_for_snapshot(s))
        spot = next(r for r in E.load_evidence_index(path).values() if r["role"] == "spot")
        got = E.resolve_evidence(path, spot["observation_id"])
        assert got["observation"]["value"]["payload"] == 24416.2
        assert got["observation"]["identity"]["timestamp"] == s.timestamp


class TestRebuildDeterminism:
    def test_rebuilding_from_the_same_snapshot_gives_the_same_ids(self):
        """The property the whole design rests on. If build_observation ever
        starts reading a clock or a random source, this fails first."""
        s = _snap("2026-08-20T09:21:42.911078+05:30", 24416.2)
        a = {r["observation_id"] for r in E.evidence_records_for_snapshot(s)}
        b = {r["observation_id"] for r in E.evidence_records_for_snapshot(s)}
        assert a == b and a


class TestHonestAccounting:
    def test_a_broken_trail_is_reported_not_hidden(self, tmp_path):
        path = str(tmp_path / "empty.jsonl")
        record = {"price_structure": {"supporting_observation_ids": ["OBS-aaa", "OBS-bbb"]}}
        i = E.evidence_integrity(path, record)
        assert i["cited_count"] == 2 and i["resolved_count"] == 0
        assert i["resolution_rate"] == 0.0
        assert i["all_cited_ids_resolve"] is False

    def test_citing_nothing_is_not_scored_perfect(self, tmp_path):
        """A cycle that cited nothing has demonstrated nothing. Reporting it
        whole is the exact 'Explanation completeness: 100%' failure."""
        i = E.evidence_integrity(str(tmp_path / "e.jsonl"), {"anything": 1})
        assert i["cited_count"] == 0
        assert i["resolution_rate"] is None
        assert i["all_cited_ids_resolve"] is None

    def test_the_real_2026_08_20_record_measures_as_broken(self):
        """Run against the genuine artifact. If this ever passes as whole,
        the measurement has stopped measuring."""
        session = REPO_ROOT / "shadow_sessions" / "OPTIONS_OS_2026-08-20_db10ae52"
        thesis = session / "market_thesis.jsonl"
        if not thesis.exists():
            pytest.skip("historical session artifact not present")
        record = json.loads(thesis.read_text().splitlines()[0])["cycle_record"]
        i = E.evidence_integrity(str(session / "decision_evidence.jsonl"), record)
        assert i["cited_count"] > 0
        assert i["all_cited_ids_resolve"] is False


class TestIdExtraction:
    def test_it_finds_ids_at_any_depth(self):
        """Cited ids live under at least nine different key paths. A fixed
        key list would silently stop covering a new one."""
        rec = {"a": {"supporting_observation_ids": ["OBS-1"]},
               "b": [{"explanation": {"which_observations_support_it": ["OBS-2"]}}],
               "c": {"lenses": [{"supporting_evidence_ids": ["OBS-3"]}]}}
        assert E.cited_observation_ids(rec) == {"OBS-1", "OBS-2", "OBS-3"}

    def test_assessment_ids_are_not_observations(self):
        """EPS-/MEVT-/MSA-/MTA- name conclusions, not observations. Treating
        them as observations would manufacture permanent unresolved ids."""
        rec = {"x": ["MTA-a", "MSA-b", "EPS-c", "MEVT-d", "OBS-real"]}
        assert E.cited_observation_ids(rec) == {"OBS-real"}


class TestDurabilityAndIdempotence:
    def test_the_same_fact_is_stored_once(self, tmp_path):
        """A content hash says two observations of the same fact are one."""
        path = str(tmp_path / "e.jsonl")
        s = _snap("2026-08-20T09:21:42.911078+05:30", 24416.2)
        first = E.append_evidence(path, E.evidence_records_for_snapshot(s))
        second = E.append_evidence(path, E.evidence_records_for_snapshot(s))
        assert first > 0 and second == 0

    def test_it_is_append_only_across_cycles(self, tmp_path):
        path = str(tmp_path / "e.jsonl")
        E.append_evidence(path, E.evidence_records_for_snapshot(
            _snap("2026-08-20T09:21:42+05:30", 24416.2)))
        before = len(E.load_evidence_index(path))
        E.append_evidence(path, E.evidence_records_for_snapshot(
            _snap("2026-08-20T09:26:42+05:30", 24421.5)))
        assert len(E.load_evidence_index(path)) > before

    def test_a_corrupt_line_does_not_lose_the_session(self, tmp_path):
        """One bad line must not make an entire session unreadable."""
        path = tmp_path / "e.jsonl"
        E.append_evidence(str(path), E.evidence_records_for_snapshot(
            _snap("2026-08-20T09:21:42+05:30", 24416.2)))
        with open(path, "a") as fh:
            fh.write("{not json at all\n")
        assert len(E.load_evidence_index(str(path))) >= 1

    def test_a_missing_file_is_empty_not_an_error(self, tmp_path):
        assert E.load_evidence_index(str(tmp_path / "never.jsonl")) == {}
        assert E.resolve_evidence(str(tmp_path / "never.jsonl"), "OBS-x") is None


class TestItNeverCostsTheSession:
    @pytest.mark.parametrize("bad", [None, object(), "not-a-snapshot", 42])
    def test_a_broken_snapshot_yields_no_records_rather_than_raising(self, bad):
        assert E.evidence_records_for_snapshot(bad) == []

    def test_a_snapshot_missing_futures_and_chain_still_yields_spot(self):
        s = _snap("2026-08-20T09:21:42+05:30", 24416.2, chain=False, futures=False)
        roles = {r["role"] for r in E.evidence_records_for_snapshot(s)}
        assert "spot" in roles and "futures" not in roles

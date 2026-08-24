"""Open interest is a REST-chain fact, and it must stay one.

WHAT THIS LOCKS OUT, all measured on 2026-08-24:

  1. FyersBroker.get_option_chain returned `values.get("ce_oi", 0.0)`, so a
     strike with a CE row but no PE row was reported as a PE with ZERO open
     interest. A real 0 and an unfetched value rendered identically, and the
     minimum-OI liquidity gate compares that number to a threshold.
  2. The endpoint returns oi/prev_oi/oich per strike -- with oich == oi -
     prev_oi live-verified -- and Bujji discarded prev_oi entirely.
  3. An OI-dependent refusal named no evidence, so "why was this strike
     rejected?" could not be replayed against the record it used.
  4. The websocket carries NO open interest at all (zero occurrences of
     open_interest / "oi" / prev_oi across a 394 MB corpus), so any tick
     provenance on an OI fact is a false claim of simultaneity.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bujji.market_intelligence.engine import (  # noqa: E402
    AVAILABLE, NON_ACTIONABLE, UNAVAILABLE, build_report, to_contract_oi)
from bujji.market_intelligence.verifier import (  # noqa: E402
    AMBIGUOUS_LINKAGE, CONTRACT_IDENTITY_MISMATCH, EXPIRY_IDENTITY_MISMATCH,
    MALFORMED_VALUE, MISSING_SNAPSHOT_REFERENCE, REST_PROVENANCE,
    TICK_PROVENANCE_ON_REST_FACT, UNAVAILABLE_PRESENTED_AS_VALUE,
    UNKNOWN_PROVENANCE, decision_market_context, verify_oi_evidence)
from bujji.options_observation import taxonomy as oo_tax  # noqa: E402
from bujji.options_observation.engine import build_option_observation  # noqa: E402



def _computed_only(payload):
    """Strip EXPLANATORY fields, keep computed values.

    A field whose job is to say "this is not a signal, not bullish, not a
    recommendation" necessarily contains those words. Scanning it flags the
    denial as the offence -- which happened three separate times today, on
    three different tests. The rule, rather than a list: any key naming a
    note, reason, limit or basis is prose about the measurement; everything
    else is the measurement.
    """
    EXPLANATORY = ("note", "reason", "limit", "basis")

    def strip(v, key=""):
        if any(w in key.lower() for w in EXPLANATORY):
            return None
        if isinstance(v, dict):
            return {k: strip(x, k) for k, x in v.items()
                    if not any(w in k.lower() for w in EXPLANATORY)}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v

    return strip(payload)


# --------------------------------------------------------------- fixtures
def _obs(symbol="NSE:NIFTY26AUG24000CE", strike=24000.0, opt="CE",
         expiry="2026-08-25", oi=5000.0, prev=4000.0, oich=1000.0):
    return build_option_observation(
        underlying="NIFTY", instrument_symbol=symbol, strike=strike,
        expiry=expiry, option_type=opt, exchange="NSE", segment="FO",
        timestamp="2026-08-24T09:20:00+05:30", resolution="SNAPSHOT",
        open_=None, high=None, low=None, close=100.0, settlement=None,
        volume=1000.0, open_interest=oi, change_in_open_interest=oich,
        previous_open_interest=prev, underlying_price=24050.0,
        origin="fyers_optionchain_live",
        acquisition_timestamp="2026-08-24T09:20:00+05:30",
        normalization_timestamp="2026-08-24T09:20:00+05:30",
        symbol_provenance="BROKER_AUTHORITATIVE",
    )


def _ev(**kw):
    base = {"observation_id": "OBS-1", "instrument_symbol": "N24000CE",
            "chain_timestamp": "2026-08-24T09:20:00+05:30",
            "provenance": REST_PROVENANCE, "availability": "AVAILABLE",
            "open_interest": 5000, "previous_open_interest": 4000,
            "change_in_open_interest": 1000, "missing_fields": ()}
    base.update(kw)
    return base


# ------------------------------------------------- B: the observation fact
class TestOpenInterestIsCarriedWhole:
    def test_all_three_oi_fields_survive(self):
        o = _obs()
        assert o.open_interest == 5000.0
        assert o.previous_open_interest == 4000.0
        assert o.change_in_open_interest == 1000.0

    def test_prev_oi_is_a_first_class_field(self):
        assert hasattr(oo_tax, "FIELD_PREVIOUS_OPEN_INTEREST")
        assert (oo_tax.FIELD_PREVIOUS_OPEN_INTEREST
                in oo_tax.ALL_OPTIONS_OBSERVATION_FIELDS)

    def test_prev_oi_is_not_mandatory(self):
        """Bhavcopy has no previous-OI column. Making it mandatory would mark
        every Bhavcopy observation permanently incomplete for a field that
        source structurally cannot supply."""
        assert (oo_tax.FIELD_PREVIOUS_OPEN_INTEREST
                not in oo_tax.MANDATORY_OPTIONS_OBSERVATION_FIELDS)

    def test_absent_prev_oi_is_disclosed_not_defaulted(self):
        o = _obs(prev=None)
        assert o.previous_open_interest is None
        assert oo_tax.FIELD_PREVIOUS_OPEN_INTEREST in o.missing_fields

    def test_broker_oich_is_kept_verbatim_not_recomputed(self):
        """A competing oi - prev_oi delta would be a second answer to one
        question. The broker's own figure is authoritative."""
        o = _obs(oi=5000.0, prev=4900.0, oich=7.0)   # deliberately inconsistent
        assert o.change_in_open_interest == 7.0, "oich was recomputed"


class TestGenuineZeroIsNotMissing:
    def test_real_zero_is_available(self):
        c = to_contract_oi(_obs(oi=0.0, prev=0.0, oich=0.0))
        assert c.open_interest == 0.0
        assert c.availability == AVAILABLE

    def test_missing_is_unavailable(self):
        c = to_contract_oi(_obs(oi=None))
        assert c.open_interest is None
        assert c.availability == UNAVAILABLE

    def test_they_are_distinguishable(self):
        assert (to_contract_oi(_obs(oi=0.0)).availability
                != to_contract_oi(_obs(oi=None)).availability)


class TestNoFabricatedZeroInTheBrokerExtraction:
    def test_absent_side_is_none_not_zero(self):
        """A strike with a CE row and no PE row must not yield pe_oi = 0.0."""
        src = (REPO / "bujji" / "broker" / "fyers.py").read_text()
        block = src[src.index("async def get_option_chain("):
                    src.index("async def get_futures_quote(")]
        code = "\n".join(l for l in block.splitlines()
                         if not l.strip().startswith("#"))
        assert 'values.get("ce_oi", 0.0)' not in code
        assert 'values.get("pe_oi", 0.0)' not in code
        assert 'values.get("ce_oi")' in code and 'values.get("pe_oi")' in code

    def test_the_control_can_see_a_planted_fallback(self):
        planted = 'return [(s, v.get("ce_oi", 0.0), v.get("pe_oi", 0.0)) for s, v in x]'
        assert 'values.get("ce_oi", 0.0)' not in planted  # different spelling
        assert '.get("ce_oi", 0.0)' in planted, "detector must see the pattern"


# ------------------------------------------------ B/C: the liquidity gate
class TestUnavailableOIRefusesTheLiquidityGate:
    @staticmethod
    def _gate(*evidences):
        from bujji.msi_trade_construction.engine import _liquidity_ok
        return _liquidity_ok(evidences)

    @staticmethod
    def _se(oi, ev=None):
        from bujji.msi_trade_construction.engine import _StrikeEvidence
        return _StrikeEvidence(strike=24000.0, option_type="CE", premium=100.0,
                               open_interest=oi, iv=0.2, delta=0.5,
                               oi_evidence=ev if ev is not None else _ev())

    def test_unavailable_oi_refuses_under_its_own_name(self):
        ok, reasons = self._gate(self._se(None))
        assert ok is False
        assert any("OI_UNAVAILABLE" in r for r in reasons)
        assert not any("OI_BELOW_MINIMUM" in r for r in reasons)

    def test_below_minimum_is_a_different_refusal(self):
        ok, reasons = self._gate(self._se(1.0))
        assert ok is False
        assert any("OI_BELOW_MINIMUM" in r for r in reasons)
        assert not any("OI_UNAVAILABLE" in r for r in reasons)

    def test_valid_oi_still_passes_unchanged(self):
        """The repair must not tighten the gate for good data."""
        ok, reasons = self._gate(self._se(50_000.0))
        assert ok is True and reasons == ()

    def test_genuine_zero_is_refused_as_illiquid_not_as_missing(self):
        ok, reasons = self._gate(self._se(0.0))
        assert ok is False
        assert any("OI_BELOW_MINIMUM" in r for r in reasons)

    def test_the_refusal_names_its_evidence(self):
        ok, reasons = self._gate(self._se(None))
        assert any("observation=OBS-1" in r for r in reasons)
        assert any("chain_timestamp=" in r for r in reasons)

    def test_a_refusal_without_evidence_says_so(self):
        ok, reasons = self._gate(self._se(None, ev={}))
        assert any("NO OBSERVATION REFERENCE RECORDED" in r for r in reasons)


# ------------------------------------------------------- C: the verifier
class TestVerifierRefusals:
    def test_valid_evidence_is_replayable(self):
        assert verify_oi_evidence([_ev()]).replayable

    @pytest.mark.parametrize("mutate,code", [
        ({"observation_id": None}, MISSING_SNAPSHOT_REFERENCE),
        ({"chain_timestamp": None}, MISSING_SNAPSHOT_REFERENCE),
        ({"provenance": None}, UNKNOWN_PROVENANCE),
        ({"provenance": "SOMETHING_ELSE"}, UNKNOWN_PROVENANCE),
        ({"provenance": "LIVE_TICK"}, TICK_PROVENANCE_ON_REST_FACT),
        ({"provenance": "REST_FALLBACK"}, TICK_PROVENANCE_ON_REST_FACT),
        ({"availability": "UNAVAILABLE"}, UNAVAILABLE_PRESENTED_AS_VALUE),
        ({"availability": "MAYBE"}, MALFORMED_VALUE),
        ({"open_interest": "5000"}, MALFORMED_VALUE),
        ({"change_in_open_interest": True}, MALFORMED_VALUE),
    ])
    def test_each_defect_is_refused(self, mutate, code):
        v = verify_oi_evidence([_ev(**mutate)])
        assert not v.replayable
        assert code in [r["code"] for r in v.refusals], \
            f"expected {code}, got {[r['code'] for r in v.refusals]}"

    def test_empty_record_is_refused(self):
        v = verify_oi_evidence([{}])
        assert MISSING_SNAPSHOT_REFERENCE in [r["code"] for r in v.refusals]

    def test_ambiguous_linkage_is_refused(self):
        v = verify_oi_evidence([_ev(instrument_symbol="A"),
                                _ev(instrument_symbol="B")])
        assert AMBIGUOUS_LINKAGE in [r["code"] for r in v.refusals]

    def test_expiry_identity_mismatch_is_refused(self):
        v = verify_oi_evidence([_ev(expiry="2026-09-01")],
                               expected_expiry="2026-08-25")
        assert EXPIRY_IDENTITY_MISMATCH in [r["code"] for r in v.refusals]

    def test_contract_identity_mismatch_is_refused(self):
        v = verify_oi_evidence([_ev(expected_symbol="N24100PE")])
        assert CONTRACT_IDENTITY_MISMATCH in [r["code"] for r in v.refusals]

    def test_unavailable_with_no_value_is_fine(self):
        assert verify_oi_evidence(
            [_ev(availability="UNAVAILABLE", open_interest=None)]).replayable


# ------------------------------------------- D: descriptive, non-actionable
class TestDescriptiveReportIsNotActionable:
    def _report(self):
        return build_report(
            [_obs(symbol="CE1", strike=24000.0, opt="CE", oi=5000.0),
             _obs(symbol="PE1", strike=24000.0, opt="PE", oi=7000.0),
             _obs(symbol="CE2", strike=24100.0, opt="CE", oi=None),
             _obs(symbol="PE2", strike=24100.0, opt="PE", oi=0.0, prev=0.0, oich=0.0)],
            spot=24050.0)

    def test_missingness_is_visible(self):
        c = self._report().coverage
        assert c["contracts"] == 4 and c["oi_unavailable"] == 1
        assert c["oi_availability_pct"] == 75.0

    def test_pcr_states_its_denominator(self):
        pc = self._report().put_call
        assert pc["pcr_oi"] is not None
        assert "excluded" in pc["pcr_basis"]

    def test_concentration_and_distance_are_computed(self):
        con = self._report().concentration
        assert con["strikes_with_oi"] == 2
        assert con["oi_weighted_distance_from_spot"] is not None

    def test_no_quadrant_is_emitted_from_one_snapshot(self):
        q = self._report().quadrants
        assert q["computed"] is False and "same" in q["reason"].lower()

    def test_quadrant_vocabulary_names_no_direction(self):
        for name in self._report().quadrants["vocabulary"]:
            for banned in ("BULL", "BEAR", "LONG", "SHORT", "BUY", "SELL"):
                assert banned not in name.upper()

    def test_report_contains_no_recommendation_vocabulary(self):
        """Scan the DATA, not the disclaimer.

        The `limits` string exists precisely to say "no signal, no
        recommendation, no bullish/bearish reading", so scanning it for those
        words flags the denial as the offence. That is the third time today a
        check has matched its own documentation -- the pattern is: exclude the
        text whose job is to name the thing you are forbidding.
        """
        import json
        blob = json.dumps(_computed_only(self._report().as_dict())).upper()
        for banned in ("RECOMMEND", "SIGNAL", "BUY ", "SELL ", "ENTER ",
                       "BULLISH", "BEARISH", "TARGET PRICE"):
            assert banned not in blob, f"{banned!r} appeared in report DATA"

    def test_the_scan_would_catch_a_planted_recommendation(self):
        """NEGATIVE CONTROL for the scan above."""
        import json
        payload = self._report().as_dict()
        payload["concentration"]["top_strikes"] = ["RECOMMEND selling this strike"]
        assert "RECOMMEND" in json.dumps(_computed_only(payload)).upper()

    def test_limits_are_stated_explicitly(self):
        lim = self._report().limits
        assert lim == NON_ACTIONABLE
        for must in ("buyer or seller initiation", "participant class",
                     "opening versus closing flow", "bullish/bearish"):
            assert must in lim

    def test_oich_is_broker_reported_not_derived(self):
        st = self._report().oich_stats
        assert st["source"] == "BROKER_REPORTED_OICH"
        assert "competing" in st["note"]

    def test_no_tick_provenance_anywhere_in_the_report(self):
        dq = self._report().data_quality
        assert dq["provenance"] == "REST_CHAIN_SNAPSHOT"
        assert dq["tick_provenance_present"] is False


# ------------------------------- E: constructed, consumed by nothing
class TestIntelligenceToDecisionContractIsInert:
    def test_context_declares_itself_unconsumed(self):
        ctx = decision_market_context([_ev()])
        assert ctx["status"] == "CONSTRUCTED_NOT_CONSUMED"
        assert ctx["authorizes"].startswith("NOTHING")

    def test_context_names_facts_and_absences_separately(self):
        ctx = decision_market_context(
            [_ev(), _ev(observation_id="OBS-2", instrument_symbol="N2",
                        availability="UNAVAILABLE", open_interest=None)])
        assert len(ctx["facts_considered"]) == 1
        assert len(ctx["fields_unavailable"]) == 1
        assert ctx["sources"] == [REST_PROVENANCE]

    def test_context_carries_the_replay_verdict(self):
        bad = decision_market_context([_ev(provenance="LIVE_TICK")])
        assert bad["evidence_replayable"] is False
        assert bad["evidence_refusals"]

    def test_no_strategy_risk_or_execution_module_imports_it(self):
        """The contract may be built and recorded. It may not be consumed."""
        import subprocess
        # PRECISE: an IMPORT of this package, not the substring
        # "market_intelligence" -- which also matches the long-standing,
        # unrelated bujji.intelligence.market_intelligence_snapshot and every
        # module that merely mentions the phrase. An earlier version of this
        # check matched 25 innocent files and would have failed no matter what
        # this package did.
        out = subprocess.run(
            ["grep", "-rlnE",
             r"(from|import)\s+bujji\.market_intelligence\b",
             "--include=*.py", "bujji/", "tools/", "bujji_options_os_runner.py"],
            cwd=str(REPO), capture_output=True, text=True).stdout.split()
        offenders = [f for f in out
                     if not f.startswith("bujji/market_intelligence")]
        assert not offenders, (
            f"bujji.market_intelligence is imported by {offenders}; the "
            f"contract may be built and recorded, never consumed")


class TestMarketIntelligenceIsOffline:
    def test_absent_from_every_entrypoint_closure(self):
        """'Offline' in a docstring has meant 'reachable but uncalled' in this
        codebase before. Reachability is the only credible form of the claim."""
        import json
        import subprocess
        r = json.loads(subprocess.run(
            [sys.executable, "tools/reachability.py", "--json"],
            cwd=str(REPO), capture_output=True, text=True).stdout)
        reachable = [m for m in r["reachable_modules"]
                     if m.startswith("bujji.market_intelligence")]
        assert not reachable, f"market_intelligence is REACHABLE: {reachable}"


# ---------------------------------------------------------------- wiring
class TestTheWiringItself:
    """The unit tests above pass against a correctly-shaped evidence dict.

    They do NOT prove the engine actually builds one, or that the live chain
    path actually carries prev_oi. Three negative controls -- dropping
    prev_oi, stamping tick provenance, and removing the observation_id --
    all passed 47/47 until these tests existed. Testing the verifier is not
    testing the wiring.
    """

    def _evidence_from_real_observation(self, oi=5000.0):
        from bujji.msi_trade_construction.engine import _build_strike_evidence
        obs = _obs(oi=oi)
        ev = _build_strike_evidence([obs], expiry="2026-08-25", spot=24050.0,
                                    t_years=0.02, r=0.065)
        assert ev, "no strike evidence was built"
        return next(iter(ev.values()))

    def test_engine_records_the_observation_id(self):
        se = self._evidence_from_real_observation()
        assert se.oi_evidence.get("observation_id"), (
            "the engine built evidence with no observation_id; an OI-dependent "
            "decision could not name the record it used")

    def test_engine_records_rest_provenance_not_tick(self):
        se = self._evidence_from_real_observation()
        assert se.oi_evidence.get("provenance") == "REST_CHAIN_SNAPSHOT"

    def test_engine_records_the_chain_timestamp(self):
        se = self._evidence_from_real_observation()
        assert se.oi_evidence.get("chain_timestamp")

    def test_engine_carries_all_three_oi_values(self):
        se = self._evidence_from_real_observation()
        assert se.oi_evidence.get("open_interest") == 5000.0
        assert se.oi_evidence.get("previous_open_interest") == 4000.0
        assert se.oi_evidence.get("change_in_open_interest") == 1000.0

    def test_engine_marks_absent_oi_unavailable(self):
        se = self._evidence_from_real_observation(oi=None)
        assert se.oi_evidence.get("availability") == "UNAVAILABLE"
        assert se.oi_evidence.get("open_interest") is None

    def test_engine_evidence_passes_the_verifier(self):
        """End to end: what the engine builds must be replayable."""
        se = self._evidence_from_real_observation()
        assert verify_oi_evidence([se.oi_evidence]).replayable

    def test_live_chain_path_carries_prev_oi(self):
        """The live provider must pass prev_oi into the observation builder.

        Source-level, because constructing a LiveChainProvider needs a broker.
        The control that motivated this deleted exactly this line and nothing
        failed.
        """
        src = (REPO / "bujji" / "production_runtime"
               / "live_chain_provider.py").read_text()
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert 'previous_open_interest=row.get("prev_oi")' in code, (
            "the live chain path no longer carries prev_oi; the broker returns "
            "it and Bujji would be discarding it again")
        assert 'open_interest=row.get("oi")' in code
        assert 'change_in_open_interest=row.get("oich")' in code

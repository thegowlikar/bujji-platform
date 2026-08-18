"""Spot-from-chain: certification verdict, and the capture's gating on it.

The capture must never write a SPOT observation on an access method that has
not been certified for SPOT, and the certifier must never emit
CERTIFIED_AVAILABLE without evidence. Both are asserted structurally.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


capture = _load("capture_options_reality_session")
certify = _load("certify_fyers_optionchain_spot_access")

CERTIFIED = "CERTIFIED_AVAILABLE"
CHAIN = {"optionsChain": [{"strike_price": -1, "ltp": 24400.5},
                          {"strike_price": 24400, "ltp": 120.0}]}


# --------------------------------------------------------------- certification

def _s(chain, broker):
    return {"at": "2026-08-19T10:00:00+05:30", "chain_spot": chain,
            "broker_spot": broker, "error": None}


def test_agreeing_samples_certify():
    v = certify.assess([_s(24400.0, 24400.0), _s(24401.0, 24400.5)], 5.0)
    assert v["validation_result"] == CERTIFIED
    assert v["issues"] == []
    assert v["samples_usable"] == 2


def test_a_single_out_of_tolerance_sample_blocks_certification():
    # 24400 vs 24300 is ~41 bps, far outside 5.
    v = certify.assess([_s(24400.0, 24400.0), _s(24400.0, 24300.0)], 5.0)
    assert v["validation_result"] == "NOT_CERTIFIED"
    assert any("exceeded" in i for i in v["issues"])


def test_no_usable_samples_never_certifies():
    v = certify.assess([_s(None, 24400.0), _s(24400.0, None)], 5.0)
    assert v["validation_result"] == "NOT_CERTIFIED"
    assert v["samples_usable"] == 0


def test_empty_sample_set_never_certifies():
    v = certify.assess([], 5.0)
    assert v["validation_result"] == "NOT_CERTIFIED"


def test_incomplete_samples_are_reported_even_when_the_rest_agree():
    v = certify.assess([_s(24400.0, 24400.0), _s(None, 24400.0)], 5.0)
    assert v["validation_result"] == "NOT_CERTIFIED"
    assert any("incomplete" in i for i in v["issues"])


def test_bps_arithmetic():
    v = certify.assess([_s(24412.2, 24400.0)], 100.0)
    assert v["samples"][0]["abs_diff"] == pytest.approx(12.2)
    assert v["samples"][0]["diff_bps"] == pytest.approx(12.2 / 24400.0 * 10_000)


# ------------------------------------------------------------------- capture

class _Store:
    def __init__(self):
        self.written = []

    def write(self, obs):
        self.written.append(obs)
        return True


def test_uncertified_access_method_writes_no_spot():
    store = _Store()
    assert capture._write_spot_observation(
        store, CHAIN, "2026-08-19T10:00:00+05:30",
        cert_status="CERTIFICATION_MISSING", cert_ref=None) is False
    assert store.written == []


@pytest.mark.parametrize("status", ["NOT_CERTIFIED", "PARTIAL_CERTIFICATION", ""])
def test_only_certified_available_opens_the_spot_write(status):
    store = _Store()
    assert capture._write_spot_observation(
        store, CHAIN, "2026-08-19T10:00:00+05:30",
        cert_status=status, cert_ref=None) is False
    assert store.written == []


def test_certified_write_uses_the_shared_spot_identity_and_type():
    store = _Store()
    ok = capture._write_spot_observation(
        store, CHAIN, "2026-08-19T10:00:00+05:30",
        cert_status=CERTIFIED, cert_ref="artifact@ts")
    assert ok is True and len(store.written) == 1
    obs = store.written[0]
    # Must land on the SAME series as the historical backfill, not a parallel one.
    assert obs.instrument == "NSE:NIFTY50-INDEX"
    assert obs.instrument_type == "SPOT"
    assert obs.observation.identity.resolution == "FIVE_MINUTE"
    # The spot actually recorded is the sentinel-strike ltp, not a fabricated value.
    assert obs.observation.value.payload == {"ltp": 24400.5}


def test_missing_underlying_is_skipped_not_fabricated():
    store = _Store()
    no_sentinel = {"optionsChain": [{"strike_price": 24400, "ltp": 120.0}]}
    assert capture._write_spot_observation(
        store, no_sentinel, "2026-08-19T10:00:00+05:30",
        cert_status=CERTIFIED, cert_ref="a@b") is False
    assert store.written == []


def test_a_store_conflict_does_not_propagate_and_stop_option_capture():
    from bujji.historical_reality.store import ConflictingHistoricalObservationError

    class _Conflicting:
        def write(self, obs):
            raise ConflictingHistoricalObservationError("duplicate natural key")

    assert capture._write_spot_observation(
        _Conflicting(), CHAIN, "2026-08-19T10:00:00+05:30",
        cert_status=CERTIFIED, cert_ref="a@b") is False

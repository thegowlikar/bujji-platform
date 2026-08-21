"""Where an option's `instrument_symbol` came from is now recorded, not guessed.

THE DEFECT THIS CLOSES. `instrument_symbol` is typed `str`, validated
non-empty, and hashed into `observation_id`. A provider holding no real
symbol could not say so -- the contract forced it to supply something --
so `StoreChainProvider` built `f"{underlying}{expiry}{strike}{type}"`.
That string is shaped exactly like a real one. On 2026-08-20 a live
session handed FYERS "NIFTY2026-08-2524500CE", FYERS could not resolve
it, and Gate B vetoed every entry with MARGIN_NOT_CERTIFIED. Nothing was
broken except a symbol's origin being unknowable.

Shape cannot distinguish a good fake from the real thing, so nothing here
tests shape. It tests what the builder actually knew.

SCOPE. This is the data-contract foundation only. The canonical resolver,
Gate-B absorption and the paper_market_sync migration are later steps --
these tests deliberately assert the FACT, not yet its enforcement.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.options_observation import engine as opt_engine
from bujji.options_observation import query as opt_query
from bujji.options_observation import serialization as opt_serial
from bujji.options_observation import taxonomy as t
from bujji.production_runtime import store_chain_provider as scp

EXPIRY = "2026-08-25"


def _obs(provenance, *, symbol="NSE:NIFTY2682524500CE", strike=24500.0, close=101.0):
    return opt_engine.build_option_observation(
        underlying="NIFTY", instrument_symbol=symbol, strike=strike, expiry=EXPIRY,
        option_type="CE", exchange="NSE", segment="FO",
        timestamp="2026-08-21T09:20:00+05:30", resolution="SNAPSHOT",
        open_=None, high=None, low=None, close=close, settlement=None,
        volume=1000, open_interest=50000, change_in_open_interest=100,
        underlying_price=24113.0, origin="test",
        acquisition_timestamp="x", normalization_timestamp="x",
        symbol_provenance=provenance)


class TestTheClosedVocabulary:
    @pytest.mark.parametrize("provenance", t.ALL_SYMBOL_PROVENANCES)
    def test_every_declared_provenance_is_accepted_and_preserved(self, provenance):
        assert _obs(provenance).symbol_provenance == provenance

    def test_there_are_exactly_four_and_no_more(self):
        assert t.ALL_SYMBOL_PROVENANCES == (
            "BROKER_AUTHORITATIVE", "SOURCE_AUTHORITATIVE", "SYNTHETIC", "ABSENT")

    def test_an_undeclared_value_is_refused_not_coerced(self):
        with pytest.raises(ValueError, match="symbol_provenance"):
            _obs("PROBABLY_FINE")

    def test_omitting_provenance_is_a_hard_error_never_a_default(self):
        """The whole point. If this ever acquires a default, a synthetic
        symbol becomes broker-authoritative by silence."""
        sig = inspect.signature(opt_engine.build_option_observation)
        param = sig.parameters["symbol_provenance"]
        assert param.default is inspect.Parameter.empty, "provenance acquired a default"
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            "provenance must be keyword-only so a positional arg cannot land on it")

    def test_the_series_constructor_also_refuses_to_default(self):
        param = inspect.signature(opt_engine.new_option_series).parameters["symbol_provenance"]
        assert param.default is inspect.Parameter.empty
        assert param.kind is inspect.Parameter.KEYWORD_ONLY


class TestProvenanceIsNotIdentity:
    """The single most important property. Every historical option row in
    the observation store is addressed by `observation_id`; if provenance
    reached that hash, adding this field would silently orphan all of them."""

    def test_two_observations_differing_only_in_provenance_share_one_id(self):
        a = _obs(t.SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE)
        b = _obs(t.SYMBOL_PROVENANCE_SYNTHETIC)
        assert a.symbol_provenance != b.symbol_provenance
        assert a.observation_id == b.observation_id

    def test_provenance_reaches_no_part_of_the_wrapped_observation(self):
        obs = _obs(t.SYMBOL_PROVENANCE_ABSENT)
        assert "ABSENT" not in repr(obs.observation), (
            "provenance leaked into the MOC Observation, which IS hashed")

    def test_it_lives_on_the_wrapper_so_exclusion_is_structural(self):
        """Not a convention -- build_observation() cannot see this field,
        because it is attached only after that call has already returned."""
        src = inspect.getsource(opt_engine.build_option_observation)
        tree = ast.parse(src.lstrip())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", None) == "build_observation"]
        assert len(calls) == 1
        assert "symbol_provenance" not in ast.unparse(calls[0])

    def test_a_real_identity_field_still_does_change_the_id(self):
        """Negative control: the id is not simply insensitive to everything."""
        assert _obs(t.SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE).observation_id != \
               _obs(t.SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE, symbol="NSE:OTHER").observation_id


class TestTheSentinel:
    def test_it_is_deterministic(self):
        assert t.unresolved_symbol("NIFTY", EXPIRY, 24500, "CE") == \
               t.unresolved_symbol("NIFTY", EXPIRY, 24500, "CE")

    def test_it_names_the_contract_it_could_not_resolve(self):
        assert t.unresolved_symbol("NIFTY", EXPIRY, 24500, "CE") == \
               f"UNRESOLVED|NIFTY|{EXPIRY}|24500|CE"

    def test_distinct_contracts_never_collide(self):
        made = {t.unresolved_symbol("NIFTY", EXPIRY, s, o)
                for s in (24000, 24500) for o in ("CE", "PE")}
        assert len(made) == 4

    def test_it_cannot_be_mistaken_for_any_real_symbol_vocabulary(self):
        """Both live vocabularies are pipe-free, so a '|' is decisive --
        no future validator can accept the sentinel by accident."""
        sentinel = t.unresolved_symbol("NIFTY", EXPIRY, 24500, "CE")
        assert "|" in sentinel
        for real in ("NSE:NIFTY2682524500CE", "NIFTY26AUG24500CE"):
            assert "|" not in real
            assert sentinel != real
        assert not sentinel.startswith("NSE:")

    def test_a_sentinel_row_keeps_a_stable_id_across_rebuilds(self):
        """Content-addressing must keep working for rows with no symbol."""
        s = t.unresolved_symbol("NIFTY", EXPIRY, 24500, "CE")
        assert _obs(t.SYMBOL_PROVENANCE_ABSENT, symbol=s).observation_id == \
               _obs(t.SYMBOL_PROVENANCE_ABSENT, symbol=s).observation_id


class TestStoreChainProviderNoLongerFabricates:
    class _Store:
        def __init__(self, rows): self._rows = rows
        def range(self, *a, **k):
            return [SimpleNamespace(payload={"ltp": 24113.0})]
        def range_by_prefix(self, *a, **k): return self._rows

    @staticmethod
    def _row(identity, ltp=101.0):
        return SimpleNamespace(
            payload={"ltp": ltp},
            observation=SimpleNamespace(identity=SimpleNamespace(
                instrument=identity, timestamp="2026-08-14T09:20:00+05:30")))

    def _chain(self):
        store = self._Store([self._row("NIFTY|2026-08-14|24000|CE"),
                             self._row("NIFTY|2026-08-14|24000|PE")])
        return scp.StoreChainProvider(store, underlying="NIFTY").get_option_chain("2026-08-14")

    def test_every_row_declares_absent_not_synthetic(self):
        """ABSENT, deliberately. SYNTHETIC would mean 'we made one up and
        kept it'; the truth is the store never captured a symbol at all."""
        rows = self._chain()
        assert rows
        for row in rows:
            assert row.symbol_provenance == t.SYMBOL_PROVENANCE_ABSENT

    def test_every_row_carries_the_sentinel_not_a_plausible_symbol(self):
        for row in self._chain():
            assert row.instrument_symbol.startswith(t.UNRESOLVED_SYMBOL_PREFIX)
            assert row.instrument_symbol != (
                f"NIFTY2026-08-14{int(row.strike)}{row.option_type}"), \
                "the old fabricated form is back"

    def test_the_fabricating_expression_is_gone_from_the_source(self):
        """AST, not grep: the comment above the fix quotes the old line."""
        tree = ast.parse(Path(scp.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                src = ast.unparse(node)
                assert not ("strike" in src and "option_type" in src and "expiry" in src), \
                    f"store provider builds an option symbol again: {src}"

    def test_the_store_rows_themselves_are_untouched(self):
        """The capture keys options as 'NIFTY|date|strike|type' and never
        stored a broker symbol -- that format is unchanged, so no historical
        observation identity moves."""
        assert scp._parse_identity("NIFTY|2026-08-18|22000|CE") == \
               ("NIFTY", "2026-08-18", 22000.0, "CE")


class TestProductionProvidersDeclareHonestly:
    """Each production builder is pinned to the provenance its SOURCE
    justifies -- not to whatever keeps a test green."""

    @staticmethod
    def _provenance_at(module, func_name=None):
        tree = ast.parse(Path(module.__file__).read_text())
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and "build_option_observation" in ast.unparse(node.func):
                for kw in node.keywords:
                    if kw.arg == "symbol_provenance":
                        found.append(ast.unparse(kw.value))
        return found

    def test_live_fyers_chain_is_broker_authoritative(self):
        from bujji.production_runtime import live_chain_provider
        got = self._provenance_at(live_chain_provider)
        assert got and all("BROKER_AUTHORITATIVE" in g for g in got), got

    def test_bhavcopy_is_source_authoritative_never_broker(self):
        """NSE's FinInstrmNm is real, but nothing has shown FYERS accepts it."""
        from bujji.options_observation import runner
        got = self._provenance_at(runner)
        assert got and all("SOURCE_AUTHORITATIVE" in g for g in got), got

    def test_the_store_provider_is_absent(self):
        got = self._provenance_at(scp)
        assert got and all("ABSENT" in g for g in got), got

    def test_the_market_state_bridge_is_broker_authoritative(self):
        """leg.symbol traces to FYERS's own instrument master CSV."""
        from bujji.market_state_builder import option_observation_bridge
        got = self._provenance_at(option_observation_bridge)
        assert got and all("BROKER_AUTHORITATIVE" in g for g in got), got

    def test_the_event_translator_demands_it_from_the_producer(self):
        """It never sees the raw frame, so it must not guess -- it reads
        the payload and fails loudly when the producer omitted it."""
        from bujji.live_observation import engine as lo_engine
        got = self._provenance_at(lo_engine)
        assert got == ["payload['symbol_provenance']"], got

    def test_no_production_builder_anywhere_omits_provenance(self):
        """The sweep that would catch a NEW construction path added later."""
        missing = []
        for path in sorted(REPO_ROOT.joinpath("bujji").rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                # Exact name, not substring: build_option_observationS_from_
                # snapshot() is a CALLER of the bridge, not a builder, and a
                # substring match flagged its two call sites as defects.
                if isinstance(node, ast.Call) and \
                        ast.unparse(node.func).split(".")[-1] == "build_option_observation":
                    if not any(kw.arg == "symbol_provenance" for kw in node.keywords):
                        missing.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert not missing, f"production builders with undeclared provenance: {missing}"

    def test_that_sweep_can_actually_fail(self):
        """Positive control -- an absence claim from a search I wrote is
        worth nothing until the search is shown to find something."""
        tree = ast.parse("build_option_observation(underlying='NIFTY')")
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        assert not any(kw.arg == "symbol_provenance" for kw in call.keywords)


class TestNothingTreatsTheSentinelAsTradable:
    def test_no_production_module_special_cases_the_prefix_into_a_symbol(self):
        offenders = []
        for path in sorted(REPO_ROOT.joinpath("bujji").rglob("*.py")):
            if path.name == "taxonomy.py":
                continue
            text = path.read_text()
            if "UNRESOLVED_SYMBOL_PREFIX" in text or "UNRESOLVED|" in text:
                if "unresolved_symbol(" not in text:
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, f"modules touching the sentinel outside its own builder: {offenders}"

    def test_the_sentinel_never_claims_broker_authority(self):
        rows = TestStoreChainProviderNoLongerFabricates()._chain()
        assert not any(r.symbol_provenance == t.SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE
                       for r in rows)


class TestSeriesAndRoundTrip:
    def _series(self, provenance):
        return opt_engine.new_option_series(
            underlying="NIFTY", strike=24500.0, expiry=EXPIRY, option_type="CE",
            instrument_symbol="NSE:NIFTY2682524500CE", resolution="SNAPSHOT",
            symbol_provenance=provenance)

    def test_a_series_cannot_mix_two_symbol_origins(self):
        series = self._series(t.SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE)
        with pytest.raises(ValueError, match="two different symbol origins"):
            opt_engine.append_option_observation(
                series, _obs(t.SYMBOL_PROVENANCE_ABSENT))

    def test_rebuilt_wrappers_inherit_the_series_provenance(self):
        series = self._series(t.SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE)
        series = opt_engine.append_option_observation(
            series, _obs(t.SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE))
        assert [o.symbol_provenance for o in series.observations()] == \
               [t.SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE]
        assert opt_query.latest(series).symbol_provenance == \
               t.SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE

    def test_json_round_trip_preserves_provenance(self):
        obs = _obs(t.SYMBOL_PROVENANCE_ABSENT)
        assert opt_serial.option_observation_from_json(
            opt_serial.option_observation_to_json(obs)) == obs

    def test_an_artifact_written_before_provenance_existed_refuses_to_load(self):
        """It genuinely does not record where its symbol came from. Any of
        the four values would be a fabricated fact about a real recording."""
        legacy = opt_serial.option_observation_to_dict(
            _obs(t.SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE))
        legacy.pop("symbol_provenance")
        with pytest.raises(KeyError, match="symbol_provenance"):
            opt_serial.option_observation_from_dict(legacy)

    def test_validation_rejects_an_unknown_provenance(self):
        obs = _obs(t.SYMBOL_PROVENANCE_ABSENT)
        broken = type(obs)(observation=obs.observation, strike=obs.strike,
                           expiry=obs.expiry, option_type=obs.option_type,
                           underlying=obs.underlying, symbol_provenance="NOPE")
        result = opt_engine.validate_option_observation(broken)
        assert not result.is_valid
        assert "UNKNOWN_SYMBOL_PROVENANCE" in result.reasons

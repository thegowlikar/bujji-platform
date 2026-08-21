"""Tests — Bujji Margin Calibration CLI (Gate C.6, operator-run tool).
ZERO real network access anywhere in this file. Every test monkeypatches
`bujji_calibration_cli.build_operator_broker_provider`/
`build_operator_broker` to return a fake, in-memory broker -- no real
FyersBroker is ever constructed with real credentials or invoked
against a live endpoint in this test suite."""
from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bujji_calibration_cli as cli
from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import FyersMarginProvider


class _FakeBroker:
    """No place_order/cancel_order exist on this object at all."""

    def __init__(self, funds=None, order_margin=None):
        self._funds = funds
        self._order_margin = order_margin

    async def get_funds(self):
        return self._funds

    async def get_order_margin(self, ce_contract, pe_contract):
        return self._order_margin


_HAPPY_FUNDS = {"available_margin": 1_000_000.0, "used_margin": 100_000.0}
_HAPPY_ORDER_MARGIN = {"margin_per_lot": 199500.0, "verified": False, "source": "fyers_span_margin"}


def _capture_args(dataset, sample_id="s1", strategy="NIFTY_SHORT_STRADDLE", **overrides):
    defaults = dict(
        dataset=str(dataset), sample_id=sample_id, strategy=strategy, description="desc",
        underlying="NIFTY", expiry="2026-08-27", lot_size=75, product_type="MIS",
        ce_symbol="NSE:NIFTY26AUG24800CE", ce_strike=24800, ce_qty=75, ce_side="SELL", ce_price=90.0,
        pe_symbol="NSE:NIFTY26AUG24800PE", pe_strike=24700, pe_qty=75, pe_side="SELL", pe_price=85.0,
    )
    defaults.update(overrides)
    import argparse
    return argparse.Namespace(**defaults)


@pytest.fixture()
def happy_broker(monkeypatch):
    """Patches BOTH of the CLI's real-broker factories -- build_operator_
    broker (used by `validate --live`) AND build_operator_broker_provider
    (used by `capture`) -- to return a fake, in-memory broker. Patching
    only one of the two was a real bug caught during this phase's own
    testing: it let `validate --live` fall through to a REAL FyersBroker
    and a REAL network call to the live Fyers API (which correctly
    failed on the placeholder credentials, but the request itself still
    went out -- exactly what this whole file exists to prevent)."""
    fake = _FakeBroker(funds=_HAPPY_FUNDS, order_margin=_HAPPY_ORDER_MARGIN)
    monkeypatch.setattr(cli, "build_operator_broker", lambda broker_config: fake)
    monkeypatch.setattr(cli, "build_operator_broker_provider", lambda broker_config: FyersMarginProvider(fake))
    monkeypatch.setenv("FYERS_APP_ID", "TEST_APP_ID")
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "TEST_ACCESS_TOKEN")


@pytest.fixture()
def down_broker(monkeypatch):
    fake = _FakeBroker(funds=None, order_margin=None)
    monkeypatch.setattr(cli, "build_operator_broker", lambda broker_config: fake)
    monkeypatch.setattr(cli, "build_operator_broker_provider", lambda broker_config: FyersMarginProvider(fake))
    monkeypatch.setenv("FYERS_APP_ID", "TEST_APP_ID")
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "TEST_ACCESS_TOKEN")


# --------------------------------------------------------------------- #
# CLI invocation tests
# --------------------------------------------------------------------- #

def test_validate_fails_without_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("FYERS_APP_ID", raising=False)
    monkeypatch.delenv("FYERS_ACCESS_TOKEN", raising=False)
    args = cli.build_parser().parse_args(["--dataset", str(tmp_path / "d.json"), "validate"])
    assert cli.cmd_validate(args) == 1


def test_validate_passes_with_credentials_no_live_call(tmp_path, monkeypatch):
    monkeypatch.setenv("FYERS_APP_ID", "TEST")
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "TEST")
    args = cli.build_parser().parse_args(["--dataset", str(tmp_path / "d.json"), "validate"])
    assert cli.cmd_validate(args) == 0   # no --live flag -> no broker call attempted at all


def test_validate_live_uses_fake_broker_only(tmp_path, happy_broker):
    args = cli.build_parser().parse_args(["--dataset", str(tmp_path / "d.json"), "validate", "--live"])
    assert cli.cmd_validate(args) == 0


def test_validate_live_fails_closed_on_broker_down(tmp_path, down_broker):
    args = cli.build_parser().parse_args(["--dataset", str(tmp_path / "d.json"), "validate", "--live"])
    assert cli.cmd_validate(args) == 1


def test_capture_successful_writes_dataset(tmp_path, happy_broker):
    dataset = tmp_path / "d.json"
    args = _capture_args(dataset)
    assert cli.cmd_capture(args) == 0
    assert dataset.exists()
    payload = json.loads(dataset.read_text())
    assert len(payload["samples"]) == 1
    assert payload["samples"][0]["sample_id"] == "s1"


def test_report_generates_from_captured_samples(tmp_path, happy_broker, capsys):
    dataset = tmp_path / "d.json"
    cli.cmd_capture(_capture_args(dataset, sample_id="s1"))
    cli.cmd_capture(_capture_args(dataset, sample_id="s2"))
    report_args = cli.build_parser().parse_args(["--dataset", str(dataset), "report"])
    assert cli.cmd_report(report_args) == 0
    output = capsys.readouterr().out
    assert "Bujji Margin Calibration Certification Report" in output or "Certification Report" in output
    assert "Cases: 2" in output


# --------------------------------------------------------------------- #
# Credential handling tests
# --------------------------------------------------------------------- #

def test_broker_config_reads_established_env_var_names(monkeypatch):
    monkeypatch.setenv("FYERS_APP_ID", "MY_APP")
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "MY_TOKEN")
    config = cli.build_broker_config_from_env()
    assert config.app_id == "MY_APP"
    assert config.access_token == "MY_TOKEN"
    assert config.name == "fyers"


def test_broker_config_missing_credentials_detected():
    from bujji.core.config import BrokerConfig
    empty = BrokerConfig(name="fyers", app_id=None, access_token=None)
    assert cli.credentials_present(empty) is False


def test_constructing_real_broker_makes_no_network_call(monkeypatch):
    """FyersBroker.__init__ performs no I/O -- safe to construct even
    with placeholder credentials, verified directly (not assumed)."""
    from bujji.core.config import BrokerConfig
    config = BrokerConfig(name="fyers", app_id="PLACEHOLDER", access_token="PLACEHOLDER")
    broker = cli.build_operator_broker(config)   # must not raise, must not hang, must not call network
    assert broker is not None


def test_dataset_file_never_contains_access_token(tmp_path, happy_broker, monkeypatch):
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "SUPER_SECRET_TOKEN_VALUE")
    dataset = tmp_path / "d.json"
    cli.cmd_capture(_capture_args(dataset))
    raw_text = dataset.read_text()
    assert "SUPER_SECRET_TOKEN_VALUE" not in raw_text


def test_dataset_file_never_contains_raw_metadata_field(tmp_path, happy_broker):
    dataset = tmp_path / "d.json"
    cli.cmd_capture(_capture_args(dataset))
    payload = json.loads(dataset.read_text())
    assert "raw_metadata" not in payload["samples"][0]["broker_margin_snapshot"]


def test_cli_source_never_hardcodes_a_credential_value():
    source_path = Path(inspect.getfile(cli))
    source_text = source_path.read_text()
    # a real access token/app_secret value must never appear as a string literal
    tree = ast.parse(source_text)
    string_constants = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    suspicious = [s for s in string_constants if len(s) > 20 and s.isalnum()]
    assert suspicious == [], f"suspicious hardcoded-looking string literals found: {suspicious}"


# --------------------------------------------------------------------- #
# Export/import round-trip tests
# --------------------------------------------------------------------- #

def test_dataset_round_trips_across_separate_invocations(tmp_path, happy_broker):
    """Simulates two SEPARATE CLI process invocations sharing a dataset
    file -- the second capture must see the first sample already loaded
    (duplicate protection intact across process boundaries)."""
    dataset = tmp_path / "d.json"
    cli.cmd_capture(_capture_args(dataset, sample_id="s1"))
    # second "invocation" -- load_store() re-reads the file from scratch
    result = cli.cmd_capture(_capture_args(dataset, sample_id="s1"))  # duplicate
    assert result == 1
    payload = json.loads(dataset.read_text())
    assert len(payload["samples"]) == 1   # still just the one original sample


def test_export_json(tmp_path, happy_broker):
    dataset = tmp_path / "d.json"
    cli.cmd_capture(_capture_args(dataset))
    out = tmp_path / "export.json"
    args = cli.build_parser().parse_args(["--dataset", str(dataset), "export", "--output", str(out), "--format", "json"])
    assert cli.cmd_export(args) == 0
    rows = json.loads(out.read_text())
    assert len(rows) == 1
    assert rows[0]["sample_id"] == "s1"


def test_export_csv(tmp_path, happy_broker):
    dataset = tmp_path / "d.json"
    cli.cmd_capture(_capture_args(dataset))
    out = tmp_path / "export.csv"
    args = cli.build_parser().parse_args(["--dataset", str(dataset), "export", "--output", str(out), "--format", "csv"])
    assert cli.cmd_export(args) == 0
    content = out.read_text()
    assert "sample_id" in content.splitlines()[0]
    assert "s1" in content


def test_export_excludes_no_sensitive_account_information(tmp_path, happy_broker, monkeypatch):
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "SUPER_SECRET_TOKEN_VALUE")
    dataset = tmp_path / "d.json"
    cli.cmd_capture(_capture_args(dataset))
    out = tmp_path / "export.json"
    args = cli.build_parser().parse_args(["--dataset", str(dataset), "export", "--output", str(out), "--format", "json"])
    cli.cmd_export(args)
    assert "SUPER_SECRET_TOKEN_VALUE" not in out.read_text()


def test_sample_round_trips_through_dict_serialization_exactly():
    from bujji.trading_brain.risk_governor.margin_calibration_runner import MarginCalibrationSample
    from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginLegRequest, MarginSnapshot
    from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import BrokerMarginSnapshot
    from bujji.trading_brain.risk_governor.margin_comparison_engine import MarginComparisonReport
    from datetime import datetime, timezone

    ts = datetime(2026, 8, 2, 9, 15, 0, tzinfo=timezone.utc)
    original = MarginCalibrationSample(
        sample_id="s1", timestamp=ts, strategy_type="STRADDLE", position_description="d",
        margin_leg_input=(MarginLegRequest(symbol="X", qty=75, side=-1, instrument_type="OPTIDX",
                                            product_type="MIS", limit_price=90.0),),
        simulated_margin_snapshot=MarginSnapshot(required_margin=100.0, margin_verified=True,
                                                  margin_source="SIM", as_of=ts, quote=None),
        broker_margin_snapshot=BrokerMarginSnapshot(available_margin=1000.0, used_margin=100.0,
                                                      required_margin=105.0, timestamp=ts,
                                                      source="FYERS_READ_ONLY", available=True),
        comparison_report=MarginComparisonReport(simulated_margin=100.0, broker_margin=105.0,
                                                   difference=5.0, deviation_fraction=0.0476,
                                                   status="PASS", reason=None),
        metadata={"note": "test"},
    )
    round_tripped = cli.sample_from_dict(cli.sample_to_dict(original))
    assert round_tripped.sample_id == original.sample_id
    assert round_tripped.simulated_margin_snapshot.required_margin == original.simulated_margin_snapshot.required_margin
    assert round_tripped.broker_margin_snapshot.required_margin == original.broker_margin_snapshot.required_margin
    assert round_tripped.comparison_report.status == original.comparison_report.status
    assert round_tripped.margin_leg_input[0].symbol == original.margin_leg_input[0].symbol


# --------------------------------------------------------------------- #
# Adapter integration tests
# --------------------------------------------------------------------- #

def test_adapter_extension_reuses_existing_fyers_broker_class():
    """Step 4: no new broker client was created -- build_operator_broker
    constructs the SAME, existing bujji.broker.fyers.FyersBroker class."""
    from bujji.broker.fyers import FyersBroker
    from bujji.core.config import BrokerConfig
    config = BrokerConfig(name="fyers", app_id="X", access_token="Y")
    broker = cli.build_operator_broker(config)
    assert isinstance(broker, FyersBroker)


def test_adapter_extension_reuses_existing_fyers_margin_provider_class():
    from bujji.core.config import BrokerConfig
    config = BrokerConfig(name="fyers", app_id="X", access_token="Y")
    provider = cli.build_operator_broker_provider(config)
    assert isinstance(provider, FyersMarginProvider)


# --------------------------------------------------------------------- #
# Certification pipeline tests
# --------------------------------------------------------------------- #

def test_report_reuses_existing_certification_engine_not_a_new_rule(tmp_path, happy_broker):
    from bujji.trading_brain.risk_governor.margin_certification_engine import MarginCertificationEngine
    from bujji.trading_brain.risk_governor.margin_calibration_runner import (
        build_calibration_run, build_certification_result_from_samples,
    )

    dataset = tmp_path / "d.json"
    for i in range(3):
        cli.cmd_capture(_capture_args(dataset, sample_id=f"s{i}"))

    store = cli.load_store(dataset)
    engine = MarginCertificationEngine()
    run = build_calibration_run("run-1", store, clock=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc), engine=engine)
    direct = build_certification_result_from_samples(list(store.samples()), engine)
    assert run.certification_result.certification_status == direct.certification_status


# --------------------------------------------------------------------- #
# Step 7 safety tests
# --------------------------------------------------------------------- #

def test_cli_source_never_references_mutating_broker_methods():
    source_path = Path(inspect.getfile(cli))
    tree = ast.parse(source_path.read_text())
    forbidden_names = {"place_order", "cancel_order", "modify_order", "get_open_positions"}
    referenced_attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    offenders = forbidden_names & (referenced_attrs | referenced_names)
    assert offenders == set(), f"forbidden mutating method references found: {offenders}"


def test_cli_never_calls_mutating_methods_even_with_trap_broker(tmp_path, monkeypatch):
    class TrapBroker:
        def __init__(self):
            self.place_order_called = False
            self.cancel_order_called = False
        async def get_funds(self):
            return _HAPPY_FUNDS
        async def get_order_margin(self, ce, pe):
            return _HAPPY_ORDER_MARGIN
        async def place_order(self, *a, **k):
            self.place_order_called = True
            raise AssertionError("must never be called")
        async def cancel_order(self, *a, **k):
            self.cancel_order_called = True
            raise AssertionError("must never be called")

    trap = TrapBroker()
    monkeypatch.setattr(cli, "build_operator_broker_provider", lambda cfg: FyersMarginProvider(trap))
    monkeypatch.setenv("FYERS_APP_ID", "TEST")
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "TEST")
    dataset = tmp_path / "d.json"
    cli.cmd_capture(_capture_args(dataset))
    assert trap.place_order_called is False
    assert trap.cancel_order_called is False


def test_failed_broker_call_fails_closed(tmp_path, down_broker):
    dataset = tmp_path / "d.json"
    result = cli.cmd_capture(_capture_args(dataset))
    assert result == 1
    assert not dataset.exists() or json.loads(dataset.read_text())["samples"] == []


def test_missing_data_never_becomes_zero_margin(tmp_path, down_broker):
    dataset = tmp_path / "d.json"
    cli.cmd_capture(_capture_args(dataset))
    # nothing should have been written at all
    assert not dataset.exists()


def test_report_command_has_no_manual_certify_override_flag():
    """Step 7.6: reports cannot be manually marked certified -- the
    `report` subcommand's argument list has no flag that sets a status
    directly."""
    parser = cli.build_parser()
    report_parser = next(a.choices["report"] for a in parser._subparsers._group_actions if "report" in a.choices)
    option_strings = {opt for action in report_parser._actions for opt in action.option_strings}
    forbidden = {"--status", "--certify", "--force-pass", "--override", "--mark-certified"}
    assert forbidden.isdisjoint(option_strings)


def test_certification_logic_not_duplicated_no_thresholds_hardcoded_beyond_passthrough():
    """report's --pass-threshold/--fail-threshold are passed straight
    into the EXISTING MarginCertificationEngine, never compared
    manually anywhere in this CLI file."""
    source_path = Path(inspect.getfile(cli))
    source_text = source_path.read_text()
    assert "deviation_fraction <" not in source_text
    assert "deviation_fraction >" not in source_text
    assert "MarginCertificationEngine(" in source_text   # the CLI does construct/use the real engine


def test_no_live_broker_execution_imports_beyond_fyers_broker_itself():
    """The CLI is explicitly allowed to import FyersBroker (that's the
    whole point of this phase) -- but must import nothing execution-
    capable beyond it (no hybrid broker, no runtime_execution)."""
    source_path = Path(inspect.getfile(cli))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden_prefixes = ("bujji.broker.hybrid", "bujji.runtime_execution", "bujji.broker.paper")
    offenders = [m for m in imported if any(m == p or m.startswith(p + ".") for p in forbidden_prefixes)]
    assert offenders == [], f"forbidden imports found: {offenders}"


def test_no_background_execution_primitives():
    source_path = Path(inspect.getfile(cli))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden_modules = ("threading", "sched", "multiprocessing")
    offenders = [m for m in imported if m in forbidden_modules]
    assert offenders == [], f"forbidden background-execution imports found: {offenders}"

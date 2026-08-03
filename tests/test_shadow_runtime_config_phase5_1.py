"""Tests -- Shadow Runtime Configuration, Phase-5.1 (ShadowSessionConfig)."""
from __future__ import annotations

import dataclasses

import pytest

from bujji.core.enums import OptionType
from bujji.core.models import OptionContract
from bujji.shadow_runtime.shadow_config import InvalidShadowSessionConfigError, ShadowSessionConfig

CE = OptionContract("NIFTY25000CE", "NIFTY", 25000, OptionType.CE, "2026-08-06", 75)
PE = OptionContract("NIFTY25000PE", "NIFTY", 25000, OptionType.PE, "2026-08-06", 75)


def base_kwargs(**overrides):
    defaults = dict(
        session_id="SHADOW-DAY-0", contracts=(CE, PE), poll_interval_seconds=30,
        market_start="09:15", market_end="15:20", artifact_path="shadow_sessions/SHADOW-DAY-0/quotes.jsonl",
    )
    defaults.update(overrides)
    return defaults


# 1. Valid configuration creation
def test_valid_configuration_creation():
    config = ShadowSessionConfig(**base_kwargs())
    assert config.session_id == "SHADOW-DAY-0"
    assert config.contracts == (CE, PE)
    assert config.poll_interval_seconds == 30
    assert config.market_start == "09:15"
    assert config.market_end == "15:20"
    assert config.artifact_path == "shadow_sessions/SHADOW-DAY-0/quotes.jsonl"


# 2. Empty watchlist rejection
def test_empty_contracts_rejected():
    with pytest.raises(InvalidShadowSessionConfigError):
        ShadowSessionConfig(**base_kwargs(contracts=()))


# 3. Invalid poll interval rejection
@pytest.mark.parametrize("interval", [0, -1, -30])
def test_invalid_poll_interval_rejected(interval):
    with pytest.raises(InvalidShadowSessionConfigError):
        ShadowSessionConfig(**base_kwargs(poll_interval_seconds=interval))


# 4. Invalid market window rejection
def test_market_start_after_end_rejected():
    with pytest.raises(InvalidShadowSessionConfigError):
        ShadowSessionConfig(**base_kwargs(market_start="15:20", market_end="09:15"))


def test_market_start_equal_end_rejected():
    with pytest.raises(InvalidShadowSessionConfigError):
        ShadowSessionConfig(**base_kwargs(market_start="09:15", market_end="09:15"))


def test_empty_market_bounds_rejected():
    with pytest.raises(InvalidShadowSessionConfigError):
        ShadowSessionConfig(**base_kwargs(market_start=""))
    with pytest.raises(InvalidShadowSessionConfigError):
        ShadowSessionConfig(**base_kwargs(market_end=""))


# 5. Immutability
def test_config_is_frozen():
    config = ShadowSessionConfig(**base_kwargs())
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.session_id = "changed"


# 6. Existing OptionContract compatibility
def test_reuses_real_option_contract_and_option_type_no_duplication():
    config = ShadowSessionConfig(**base_kwargs())
    for contract in config.contracts:
        assert isinstance(contract, OptionContract)
        assert isinstance(contract.option_type, OptionType)
    assert config.contracts[0].option_type == OptionType.CE
    assert config.contracts[1].option_type == OptionType.PE


# 7. Forbidden-field schema safety check
def test_no_decision_or_execution_fields_present():
    field_names = set(ShadowSessionConfig.__dataclass_fields__.keys())
    forbidden = {
        "strategy", "signal", "entry", "exit", "trade", "position", "order",
        "quantity", "size", "risk", "capital", "margin", "pnl", "approval",
        "execution", "broker_action",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"
    assert field_names == {
        "session_id", "contracts", "poll_interval_seconds", "market_start", "market_end", "artifact_path",
    }


# 8. Import isolation
def test_no_protected_imports_in_shadow_config():
    import subprocess
    result = subprocess.run(
        ["grep", "-nE",
         r"^\s*(from|import)\s+(bujji\.)?(broker|msi_trade_construction|trading_brain|"
         r"trading_session_governor|execution_reality|execution_integration|risk_governor|"
         r"runtime_execution|production_runtime)\b",
         "bujji/shadow_runtime/shadow_config.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"forbidden import found: {result.stdout}"

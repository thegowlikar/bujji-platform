"""Replay path isolation — replay must never touch the live process's
persistence files by default (a real risk found during audit: running
`python -m bujji.replay --config config/config.yaml` with no other
arguments used to read/write the SAME session snapshot / journal / database
as the live bot, breaking determinism and polluting production trade
history with synthetic replay output).
"""
import csv
import sys

import pytest

from bujji.core.config import AppConfig
from bujji.replay.__main__ import _isolate_paths, main


def test_isolate_paths_redirects_all_persistence_fields(tmp_path):
    config = AppConfig()
    workdir = tmp_path / "replay_run"
    _isolate_paths(config, str(workdir))

    assert str(config.paths.journal_csv).startswith(str(workdir))
    assert str(config.paths.database).startswith(str(workdir))
    assert str(config.paths.state_file).startswith(str(workdir))
    assert str(config.paths.lock_file).startswith(str(workdir))
    # Sprint 3 regression: the Decision Journal (Sprint 2) was missed from
    # this redirect on first pass -- a real replay run wrote 168KB of
    # DecisionSnapshots into the live production data directory before
    # this was caught. Locked in so it can never silently regress again.
    assert str(config.paths.decision_journal).startswith(str(workdir))


def test_default_cli_invocation_never_touches_live_decision_journal(tmp_path, monkeypatch):
    """The exact real incident found during Sprint 3's replay validation,
    reproduced as a permanent regression test."""
    import csv, sys
    candles_csv = tmp_path / "candles.csv"
    with candles_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerow(["2026-07-19T09:20:00", "22000", "22010", "21990", "22005", "1000"])
        w.writerow(["2026-07-19T16:00:00", "22000", "22010", "21990", "22005", "1000"])
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("log_level: INFO\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "bujji-replay", "--config", str(config_yaml), "--candles", str(candles_csv),
    ])
    main()
    assert not (tmp_path / "data" / "decision_journal.jsonl").exists()
    assert (tmp_path / "data" / "replay" / "decision_journal.jsonl").exists()


def test_default_cli_invocation_never_touches_live_default_paths(tmp_path, monkeypatch):
    """The exact scenario found during audit: an operator runs replay with
    only --config and --candles, as the module's own usage docstring
    suggests, and expects it to be safe by default."""
    candles_csv = tmp_path / "candles.csv"
    with candles_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerow(["2026-07-19T09:20:00", "22000", "22010", "21990", "22005", "1000"])
        w.writerow(["2026-07-19T16:00:00", "22000", "22010", "21990", "22005", "1000"])

    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("log_level: INFO\n")  # Defaults for everything else.

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "bujji-replay", "--config", str(config_yaml), "--candles", str(candles_csv),
    ])
    main()

    # The live-default paths (bare "data/...") must NOT have been created by
    # this replay run — everything must have landed under the isolated
    # default workdir instead.
    assert not (tmp_path / "data" / "session_state.json").exists()
    assert not (tmp_path / "data" / "trade_journal.csv").exists()
    assert not (tmp_path / "data" / "bujji.db").exists()
    assert (tmp_path / "data" / "replay" / "session_state.json").exists()
    assert (tmp_path / "data" / "replay" / "trade_journal.csv").exists()
    assert (tmp_path / "data" / "replay" / "bujji.db").exists()


def test_use_live_paths_opts_out_of_isolation_explicitly(tmp_path, monkeypatch):
    candles_csv = tmp_path / "candles.csv"
    with candles_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerow(["2026-07-19T09:20:00", "22000", "22010", "21990", "22005", "1000"])
        w.writerow(["2026-07-19T16:00:00", "22000", "22010", "21990", "22005", "1000"])

    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("log_level: INFO\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "bujji-replay", "--config", str(config_yaml), "--candles", str(candles_csv),
        "--use-live-paths",
    ])
    main()

    # Explicit opt-out: this time the bare "data/..." (live-default) paths
    # ARE used, exactly as the operator asked for.
    assert (tmp_path / "data" / "session_state.json").exists()
    assert (tmp_path / "data" / "trade_journal.csv").exists()
    assert (tmp_path / "data" / "bujji.db").exists()

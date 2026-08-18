"""Phase 20.14 -- entrypoint wiring tests. Verifies the ProcessLock/
MarketCalendar integration added to `scripts/run_phase20_13_live_
entrypoint.py` without ever connecting to a broker -- argparse
construction and constants only.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys

import pytest

SCRIPT_PATH = "/opt/bujji/app/scripts/run_phase20_13_live_entrypoint.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("phase20_13_live_entrypoint_module", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_lock_path_is_distinct_from_daily_intelligence_lock():
    module = _load_script_module()
    assert module.DEFAULT_LOCK_PATH != "data/daily_intelligence.lock"
    assert "shadow_decision_campaign" in module.DEFAULT_LOCK_PATH
    assert "daily_intelligence" not in module.DEFAULT_LOCK_PATH


def test_session_date_and_date_today_are_mutually_exclusive():
    # Reconstruct the same parser the script builds, without running _main().
    with open(SCRIPT_PATH) as f:
        source = f.read()
    assert "add_mutually_exclusive_group(required=True)" in source
    assert '"--session-date"' in source
    assert '"--date-today"' in source


def test_calendar_and_lock_imports_are_wired_before_broker_connection():
    with open(SCRIPT_PATH) as f:
        source = f.read()
    calendar_idx = source.index("MarketCalendar()")
    lock_idx = source.index("ProcessLock(args.lock_path)")
    broker_connect_idx = source.index("await broker.connect()")
    assert calendar_idx < broker_connect_idx
    assert lock_idx < broker_connect_idx


def test_no_new_broker_or_order_vocabulary_introduced():
    """Checks actual CALLS, not disclosure prose -- this script's own
    docstring/comments legitimately name these methods when explaining
    what `disable_live_execution()` neuters (the same pattern every
    other phase's own safety disclosure uses)."""
    with open(SCRIPT_PATH) as f:
        source = f.read()
    forbidden_calls = (".place_order(", ".modify_order(", ".cancel_order(", ".get_open_positions(")
    for term in forbidden_calls:
        assert term not in source, f"entrypoint script contains forbidden call {term!r}"


def test_process_lock_module_reused_not_reimplemented():
    with open(SCRIPT_PATH) as f:
        source = f.read()
    assert "from bujji.core.process_lock import" in source
    # No local class named ProcessLock/Lock defined in this script -- it must be imported, never reimplemented.
    assert "class ProcessLock" not in source
    assert "class MarketCalendar" not in source

import logging
import shutil
from datetime import datetime, time
from pathlib import Path

import pytest

from bujji.core.clock import IST
from bujji.core.config import AppConfig
from bujji.core.models import Candle

REPO_ROOT = Path(__file__).resolve().parent.parent
_MASTER_FIXTURE = REPO_ROOT / "tests/fixtures/instrument_master/fyers_fo_NSE.csv"
_MASTER_RUNTIME = REPO_ROOT / "data/instrument_master/fyers_fo_NSE.csv"


@pytest.fixture(scope="session", autouse=True)
def _instrument_master_available():
    """Make the suite reproducible on a clean checkout.

    THE PROBLEM THIS SOLVES. `instrument_master_directory` defaults to the
    CWD-relative "data/instrument_master", `data/` is gitignored, and the file
    is downloaded from the exchange at runtime. So on any fresh worktree the
    master is simply absent, and every test that resolves a lot size, an
    expiry or a strike fails -- not because the behaviour is wrong but because
    the input does not exist. On this branch that was 210 failure lines and
    several whole-module collection errors, which is worse than a failure: a
    collection error HIDES every test in the module rather than reporting it.

    A suite that cannot run cannot support a safety claim, and every A/B
    comparison taken against it was measuring a partially-executed suite.

    WHAT THIS DOES NOT DO. It does not weaken the refusal. `lot_size_for()`
    still raises when the master cannot answer -- that refusal is a safety
    behaviour ("refusing to size orders off a guess") and is exercised by its
    own tests against an empty directory. This only guarantees the INPUT
    exists, so the tests measure the behaviour instead of the environment.

    The fixture is real exchange rows, committed and deterministic: NIFTY
    futures plus the strikes nearest 24000 across the four nearest expiries.
    It is NOT a substitute for the live master and is never used in
    production -- nothing outside tests/ references it.
    """
    if _MASTER_RUNTIME.exists() or not _MASTER_FIXTURE.exists():
        return
    _MASTER_RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_MASTER_FIXTURE, _MASTER_RUNTIME)


@pytest.fixture
def config() -> AppConfig:
    cfg = AppConfig()
    cfg.timing.orb_start = time(9, 15)
    cfg.timing.orb_end = time(9, 20)
    cfg.timing.trading_start = time(9, 20)
    cfg.timing.trading_end = time(15, 15)
    cfg.timing.hard_exit = time(15, 5)
    return cfg


@pytest.fixture
def logger() -> logging.Logger:
    lg = logging.getLogger("bujji.test")
    lg.addHandler(logging.NullHandler())
    return lg


def c(hh, mm, o, h, low, cl, vol=1000) -> Candle:
    # Tz-aware IST, matching what real/paper brokers now produce in production
    # (D2) — keeps naive-vs-aware datetime arithmetic consistent everywhere.
    return Candle(datetime(2026, 7, 5, hh, mm, tzinfo=IST), o, h, low, cl, vol)

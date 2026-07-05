import logging
from datetime import datetime, time

import pytest

from bujji.core.clock import IST
from bujji.core.config import AppConfig
from bujji.core.models import Candle


@pytest.fixture
def config() -> AppConfig:
    cfg = AppConfig()
    cfg.timing.orb_start = time(9, 15)
    cfg.timing.orb_end = time(9, 20)
    cfg.timing.trading_start = time(9, 20)
    cfg.timing.trading_end = time(15, 15)
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

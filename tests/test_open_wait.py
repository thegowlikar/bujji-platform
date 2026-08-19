"""First-sample capture: the wait-for-open contract.

A 09:14 fire must WAIT to the open instant (+ its stagger offset) and then
proceed; a far-from-open start must refuse, never hang; and the wait must
never sleep past the target instant.
"""
from __future__ import annotations

import datetime
import logging

from bujji.market_reality.open_wait import wait_until_open

LOG = logging.getLogger("test")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
OPEN = datetime.time(9, 15)


class _Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


def _dt(h, m, s=0):
    return datetime.datetime(2026, 8, 20, h, m, s, tzinfo=IST)


def test_already_open_proceeds_without_sleeping():
    slept = []
    ok = wait_until_open(now_fn=_Clock(_dt(9, 30)), market_open=OPEN,
                         sleep_fn=slept.append, logger=LOG)
    assert ok is True and slept == []


def test_a_0914_fire_waits_to_the_open_instant_then_proceeds():
    clock = _Clock(_dt(9, 14, 10))
    slept = []

    def sleep(sec):
        slept.append(sec)
        clock.now += datetime.timedelta(seconds=sec)

    ok = wait_until_open(now_fn=clock, market_open=OPEN, sleep_fn=sleep, logger=LOG)
    assert ok is True
    assert clock.now == _dt(9, 15, 0), "must land ON the open instant, not past it"
    assert sum(slept) == 50.0


def test_the_stagger_offset_shifts_the_target():
    clock = _Clock(_dt(9, 14, 55))

    def sleep(sec):
        clock.now += datetime.timedelta(seconds=sec)

    ok = wait_until_open(now_fn=clock, market_open=OPEN, open_offset_seconds=5,
                         sleep_fn=sleep, logger=LOG)
    assert ok is True and clock.now == _dt(9, 15, 5)


def test_far_from_open_refuses_instead_of_hanging():
    slept = []
    ok = wait_until_open(now_fn=_Clock(_dt(5, 0)), market_open=OPEN,
                         sleep_fn=slept.append, logger=LOG)
    assert ok is False and slept == [], "a 05:00 start must refuse, never wait 4 hours"


def test_the_boundary_of_max_wait_is_honoured():
    clock = _Clock(_dt(9, 10, 0))  # 300s away == default max

    def sleep(sec):
        clock.now += datetime.timedelta(seconds=sec)

    assert wait_until_open(now_fn=clock, market_open=OPEN, sleep_fn=sleep, logger=LOG) is True
    assert wait_until_open(now_fn=_Clock(_dt(9, 9, 59)), market_open=OPEN,
                           sleep_fn=lambda s: None, logger=LOG) is False

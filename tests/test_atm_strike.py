"""ATM strike rounding — production-critical, exact-midpoint edge cases.

Python's builtin round() is round-half-to-even (banker's rounding), which
silently resolves the two possible exact-midpoint spots on a strike grid
*inconsistently* (one rounds down, the adjacent one rounds up) purely based
on which candidate happens to be an even integer. Broker.atm_strike() must
be deterministic and symmetric instead: always round an exact midpoint UP.
"""
import pytest

from bujji.broker.base import Broker


@pytest.mark.parametrize("spot,interval,expected", [
    # Non-midpoint spots: unambiguous "nearest" strike.
    (25224, 50, 25200),
    (25226, 50, 25250),
    (25249, 50, 25250),
    (25250, 50, 25250),
    (25251, 50, 25250),
    (25274, 50, 25250),
    (25276, 50, 25300),
    (25237, 50, 25250),
    # Exact midpoints — the case Python's round() gets inconsistent on.
    # 25225 is exactly 25 away from both 25200 and 25250; must round UP.
    (25225, 50, 25250),
    # 25275 is exactly 25 away from both 25250 and 25300; must ALSO round UP
    # (not "round to even", which would flip this one the other way vs 25225).
    (25275, 50, 25300),
    # Sanity check against the smaller NIFTY grid used elsewhere in the suite.
    (22000, 50, 22000),
    (22025, 50, 22050),
    (22050, 50, 22050),
])
def test_atm_strike_rounding_is_deterministic_at_every_midpoint(spot, interval, expected):
    assert Broker.atm_strike(spot, interval) == expected


def test_atm_strike_midpoints_round_the_same_direction_consistently():
    """The two adjacent midpoints on a grid must round the SAME way (both up,
    or both down) — never one up and the other down. This is the exact bug
    Python's round-half-to-even introduces if used directly."""
    lower_mid = Broker.atm_strike(25225, 50)   # midpoint between 25200/25250
    upper_mid = Broker.atm_strike(25275, 50)   # midpoint between 25250/25300
    # Both must resolve to the strike ABOVE the midpoint.
    assert lower_mid == 25250
    assert upper_mid == 25300

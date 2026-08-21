"""bujji.market_calendar — Sprint 112 Deliverable 6.

Deterministic, offline market calendar. No web lookups at runtime --
every date this module reasons about comes from either real Python
`date` arithmetic (weekends) or a versioned, static, in-repo list
(holidays/half-days), never a live API call.

Honest disclosure, consistent with this project's own "never fabricate
market information" discipline (Series 108's own event-calendar
finding): the 2026 dates in HOLIDAY_CALENDAR below (Phase 19.18) were
populated by cross-checking THREE independent secondary sources
(cleartax.in, groww.in, bajajamc.com -- all fetched live, all agreeing
exactly on all 16 dates) -- NOT the primary NSE circular itself, which
timed out when fetched directly (nseindia.com/resources/exchange-communication-holidays).
This is real, disclosed, three-way-cross-verified due diligence, not
guesswork or memory -- but it is still secondary-source verification,
which is why `MarketCalendar.holiday_calendar_verified` remains `False`
by default (unchanged from before this phase): `is_trading_day()`
itself does NOT gate on that flag at all (it only controls whether
`verification_warning()` returns a warning) -- populating this dict
alone already makes every listed date correctly excluded from
`is_trading_day()`. An operator who independently confirms this list
against the official NSE circular can construct
`MarketCalendar(holiday_calendar_verified=True)` explicitly to silence
the warning; this module does not do that for them.

Deliberately NOT modeled here: the November 8, 2026 (Sunday) Muhurat
trading session -- a special ADDITIONAL session on what is normally a
non-trading day, the opposite case from a holiday, which this
calendar's data model (`is_trading_day`/`is_half_day`) has no field
for. Out of scope for "populate the holiday calendar" -- disclosed, not
silently ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Dict, Optional, Set, Tuple

CALENDAR_VERSION = "2026.1-cross-verified-secondary-sources"

# ---------------------------------------------------------------------------
# SESSION TIMES -- THE SINGLE AUTHORITY. There are TWO closes, not one.
#
# WHY THIS EXISTS. Before it, fifteen production files and scripts each
# declared their own close-time literal, and they did not agree: 15:15, 15:20,
# 15:30 and 15:40 all appeared, some as "MARKET_CLOSE", some as an exit time,
# some as a bhavcopy row stamp. A reader could not tell which were describing
# the same fact and which were describing different ones, so a future NSE
# change would mean finding every literal and guessing at each.
#
# THE TWO CLOSES ARE REAL, NOT A BUG. The cash/index segment closes at 15:30.
# The F&O segment closes at 15:40 -- NSE circular 2026-05-30, effective
# 2026-08-03, when a Closing Auction Session was added to the cash segment and
# derivatives followed it. Verified 2026-08-13 and recorded in
# scripts/certify_websocket_access.py, which was already citing it correctly.
# scripts/certify_fyers_optionchain_reality_access.py deliberately flagged the
# divergence rather than silently reconciling it -- that was the right call and
# this module is the place it should have been reconciled TO.
#
# WHICH ONE TO USE. Bujji trades NIFTY OPTIONS, which are F&O. Anything
# reasoning about Bujji's own instruments -- when its market is open, when a
# position can still be traded out of, how long to keep observing -- wants
# FO_MARKET_CLOSE. CASH_MARKET_CLOSE is correct only for cash/index
# instruments (NSE:NIFTY50-INDEX spot, INDIAVIX). Import the one whose SEGMENT
# you mean, by name, so the choice is visible to the next reader instead of
# being an anonymous "15:30".
#
# WHAT THIS IS NOT. It is not an exit time. `hard_exit`, `mandatory_exit_time`,
# `monitor_until` and `observe_until` sit at 15:15 and 15:30 because somebody
# decided to be out well before the bell -- a TRADING judgement, and it belongs
# in session config where an operator can change it, not here. A buffer stated
# as an absolute time reads like a belief about when the market closes; it is
# not one. Nor is NSE_BHAVCOPY_MARKET_CLOSE_TIME, which stamps EOD rows in a
# file format and is a third, unrelated fact.
# ---------------------------------------------------------------------------

MARKET_OPEN = time(9, 15)                 # both segments

CASH_MARKET_CLOSE = time(15, 30)          # equity / index / VIX
FO_MARKET_CLOSE = time(15, 40)            # futures & options -- what Bujji trades

SESSION_TIMES_SOURCE = (
    "NSE circular 2026-05-30, effective 2026-08-03 (Closing Auction Session in "
    "the cash segment; derivatives follow it). Verified 2026-08-13."
)


def market_close_for(segment: str) -> time:
    """The close for a named segment. Refuses an unknown one.

    Deliberately not defaulted: a caller that cannot say which segment it means
    has not decided, and picking one for it is how the split started.
    """
    key = (segment or "").strip().upper()
    if key in ("FO", "F&O", "DERIVATIVES", "OPTIONS", "FUTURES"):
        return FO_MARKET_CLOSE
    if key in ("CASH", "EQUITY", "INDEX", "SPOT"):
        return CASH_MARKET_CLOSE
    raise ValueError(
        f"unknown market segment {segment!r} -- say CASH or FO explicitly; "
        f"they close ten minutes apart ({SESSION_TIMES_SOURCE})"
    )

# Real NSE 2026 trading holidays (equity/equity-derivatives segment),
# cross-verified against 3 independent secondary sources (cleartax.in,
# groww.in, bajajamc.com — fetched 2026-08-17, all in exact agreement).
# See module docstring for the full disclosure of what "verified" means
# here and what it does not.
HOLIDAY_CALENDAR: Dict[str, str] = {
    "2026-01-15": "Municipal Corporation Election in Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}

HALF_DAY_CALENDAR: Dict[str, str] = {
    # "YYYY-MM-DD": "reason" -- e.g. Muhurat trading sessions.
}


@dataclass
class MarketCalendar:
    """`holiday_calendar_verified=False` (the honest default) means
    `is_trading_day` will treat an UNLISTED weekday as a trading day
    (since weekends are the only thing this module can verify with zero
    external data) but will surface a loud, structural warning via
    `verification_warning()` -- it never silently pretends the holiday
    list is complete."""
    version: str = CALENDAR_VERSION
    holidays: Dict[str, str] = field(default_factory=lambda: dict(HOLIDAY_CALENDAR))
    half_days: Dict[str, str] = field(default_factory=lambda: dict(HALF_DAY_CALENDAR))
    manual_closures: Dict[str, str] = field(default_factory=dict)
    holiday_calendar_verified: bool = False

    def add_manual_closure(self, day: date, reason: str) -> None:
        """Deliverable 6: unexpected closures (manual override).
        Real, operator-driven, never automated/guessed."""
        self.manual_closures[day.isoformat()] = reason

    def is_weekend(self, day: date) -> bool:
        return day.weekday() >= 5  # real Python date arithmetic; Sat=5, Sun=6

    def is_holiday(self, day: date) -> bool:
        return day.isoformat() in self.holidays

    def is_manually_closed(self, day: date) -> bool:
        return day.isoformat() in self.manual_closures

    def is_half_day(self, day: date) -> bool:
        return day.isoformat() in self.half_days

    def is_trading_day(self, day: date) -> Tuple[bool, str]:
        """Returns (is_trading_day, reason). Deliverable 6's own
        requirement: decision cadence must not start on a non-trading
        day -- this is the single real gate for that check."""
        if self.is_weekend(day):
            return False, f"{day.isoformat()} is a weekend ({day.strftime('%A')})"
        if self.is_manually_closed(day):
            return False, f"{day.isoformat()} manually closed: {self.manual_closures[day.isoformat()]}"
        if self.is_holiday(day):
            return False, f"{day.isoformat()} is a listed NSE holiday: {self.holidays[day.isoformat()]}"
        return True, f"{day.isoformat()} is a trading day"

    def verification_warning(self) -> Optional[str]:
        if self.holiday_calendar_verified:
            return None
        return (
            "HOLIDAY_CALENDAR has not been marked verified against a real, published NSE trading "
            "calendar (holiday_calendar_verified=False) -- weekends are correctly detected by real "
            "date arithmetic, but any UNLISTED NSE holiday will be incorrectly treated as a trading "
            "day until an operator populates and verifies the real holiday list for the relevant year(s)."
        )

    def next_trading_day(self, after: date, max_lookahead_days: int = 14) -> Optional[date]:
        for offset in range(1, max_lookahead_days + 1):
            candidate = after + timedelta(days=offset)
            ok, _ = self.is_trading_day(candidate)
            if ok:
                return candidate
        return None

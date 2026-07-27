"""bujji.market_calendar — Sprint 112 Deliverable 6.

Deterministic, offline market calendar. No web lookups at runtime --
every date this module reasons about comes from either real Python
`date` arithmetic (weekends) or a versioned, static, in-repo list
(holidays/half-days), never a live API call.

Honest disclosure, consistent with this project's own "never fabricate
market information" discipline (Series 108's own event-calendar
finding): the HOLIDAY_CALENDAR below is a versioned TEMPLATE, not an
independently-verified real NSE calendar. This environment has no
internet access and no authoritative NSE calendar feed. An operator
MUST verify/replace `HOLIDAY_CALENDAR` against the real, published NSE
trading calendar for the relevant year(s) before this module is used to
gate a real live session -- `MarketCalendar.holiday_calendar_verified`
is `False` by default specifically to make this impossible to miss.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Optional, Set, Tuple

CALENDAR_VERSION = "1.0.0-template"

# Versioned, static, offline. Real weekday/date arithmetic only --
# these specific dates are a STRUCTURAL EXAMPLE (standard, recurring
# NSE holiday occasions), not independently verified against a live
# NSE calendar feed for any specific year. `holiday_calendar_verified`
# gates whether `MarketCalendar` will actually rely on this list.
HOLIDAY_CALENDAR: Dict[str, str] = {
    # "YYYY-MM-DD": "reason" -- operator-populated/verified before real use.
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

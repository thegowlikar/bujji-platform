"""There are two NSE closes, and exactly one place that gets to say so.

THE SPLIT. Fifteen files declared their own close-time literal and they did
not agree -- 15:15, 15:20, 15:30 and 15:40 all appeared. Some were describing
the cash close, some the F&O close, some an exit buffer, one a bhavcopy row
stamp. Nothing distinguished them, so a reader could not tell which were the
same fact and a future NSE change would mean finding every literal and
guessing at each.

THE TWO CLOSES ARE REAL. Cash closes 15:30, F&O closes 15:40 (NSE circular
2026-05-30, effective 2026-08-03). Reconciling them to one number would not
have been a fix -- it would have been a different bug. The fix is one module
that states both, names the segment, and refuses to guess when a caller does
not say which it means.

WHAT THESE TESTS DO NOT DO. They do not police exit buffers.
`hard_exit`/`mandatory_exit_time`/`monitor_until`/`observe_until` sit before
the bell because somebody decided to be out early -- a trading judgement that
belongs in session config, not here. A test that forced those to equal the
close would be enforcing an opinion, not an invariant.
"""
from __future__ import annotations

import ast
import re
import sys
from datetime import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.market_calendar import (CASH_MARKET_CLOSE, FO_MARKET_CLOSE, MARKET_OPEN,
                                   SESSION_TIMES_SOURCE, market_close_for)

# Files whose close literal is a DIFFERENT fact, or an operator tool that
# states its own segment deliberately. Each is here for a reason, not to make
# the sweep pass.
EXEMPT = {
    # A bhavcopy row stamp: when EOD rows are timestamped in a file format.
    # Not a market close at all.
    "bujji/futures_observation/runner.py",
    "bujji/options_observation/runner.py",
    "bujji/replay/option_chain_ingestion.py",
    # Black-76 expiry moment for time-to-expiry maths. A pricing convention;
    # changing it moves every IV and delta, so it needs its own decision.
    "bujji/options_analytics/engine.py",
    "bujji/options_analytics/black76.py",
    # Exit buffers -- trading judgements, see the module docstring.
    "bujji/trading_brain/exit_engine/config.py",
    "bujji/core/config.py",
    "bujji/production_runtime/trading_session_governor/exit_policy.py",
    # The authority itself.
    "bujji/market_calendar.py",
}

_CLOSE_LITERAL = re.compile(r"time\(\s*15\s*,\s*(30|40)\s*\)|[\"']15:(30|40)(:00)?[\"']")


class TestBothClosesExistAndAreNamed:
    def test_the_two_closes_are_ten_minutes_apart(self):
        assert CASH_MARKET_CLOSE == time(15, 30)
        assert FO_MARKET_CLOSE == time(15, 40)
        assert MARKET_OPEN == time(9, 15)

    def test_the_source_is_recorded_not_remembered(self):
        """A constant this load-bearing must carry its provenance."""
        assert "2026-05-30" in SESSION_TIMES_SOURCE
        assert "Verified" in SESSION_TIMES_SOURCE

    @pytest.mark.parametrize("segment,expected", [
        ("FO", time(15, 40)), ("options", time(15, 40)), ("DERIVATIVES", time(15, 40)),
        ("CASH", time(15, 30)), ("index", time(15, 30)), ("spot", time(15, 30)),
    ])
    def test_a_named_segment_resolves(self, segment, expected):
        assert market_close_for(segment) == expected

    @pytest.mark.parametrize("bad", ["", None, "NSE", "equity derivatives maybe"])
    def test_an_unnamed_segment_is_refused_not_defaulted(self, bad):
        """Defaulting is how the split started: a caller that cannot say which
        segment it means has not decided, and picking one for it hides that."""
        with pytest.raises(ValueError, match="unknown market segment"):
            market_close_for(bad)


class TestNothingElseDeclaresACloseTime:
    """The sweep. Any production module that restates 15:30 or 15:40 has
    re-created the split."""

    @staticmethod
    def _offenders():
        found = []
        for path in sorted(REPO_ROOT.joinpath("bujji").rglob("*.py")):
            rel = str(path.relative_to(REPO_ROOT))
            if rel in EXEMPT:
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                if _CLOSE_LITERAL.search(code):
                    found.append(f"{rel}:{i}: {line.strip()[:80]}")
        return found

    def test_no_production_module_restates_a_close_literal(self):
        offenders = self._offenders()
        assert not offenders, (
            "these restate a close time instead of importing it from "
            "bujji.market_calendar:\n  " + "\n  ".join(offenders))

    def test_the_sweep_can_actually_fail(self):
        """Positive control. An absence claim from a search I wrote is worth
        nothing until the search is shown capable of finding something."""
        assert _CLOSE_LITERAL.search("MARKET_CLOSE = time(15, 40)")
        assert _CLOSE_LITERAL.search('CLOSE = "15:30:00"')
        assert not _CLOSE_LITERAL.search("hard_exit = time(15, 15)")

    def test_every_exemption_still_exists(self):
        """An exemption for a deleted file is a hole nobody is watching."""
        missing = [e for e in EXEMPT if not (REPO_ROOT / e).exists()]
        assert not missing, f"exemptions naming files that no longer exist: {missing}"


class TestTheMigratedModulesDidNotChangeBehaviour:
    """The refactor removed duplication. It must not have moved a single time."""

    def test_market_understanding_still_uses_the_fo_close(self):
        from bujji.market_understanding.timeline import MARKET_CLOSE
        assert MARKET_CLOSE == time(15, 40)

    def test_the_shadow_operator_still_stands_down_at_the_cash_close(self):
        """Flagged, not changed: this very likely wants the F&O close, since
        Bujji trades options. Switching it alters when the operator stops
        generating decisions, which is a behavioural call -- so the segment was
        made explicit and the decision left open."""
        from bujji.live_shadow_operator.operator import MARKET_CLOSE_IST, MARKET_OPEN_IST
        assert MARKET_OPEN_IST == "09:15"
        assert MARKET_CLOSE_IST == "15:30"

    def test_the_eod_suffix_is_derived_and_unchanged(self):
        from bujji.shadow_runtime.campaign_continuity import EOD_AS_OF_TIME_SUFFIX
        assert EOD_AS_OF_TIME_SUFFIX == "T15:40:00+05:30"

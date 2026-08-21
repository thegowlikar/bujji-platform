"""`exp` says when a token WOULD die. Only FYERS knows if it is already dead.

MEASURED 2026-08-21 17:20, and the reason this exists. The JWT said
`exp` = 2026-08-22T06:00, the pre-flight said OK, and FYERS said
`code=-15, "Please provide valid token"`. Both were true. FYERS revokes
outstanding tokens on a new interactive login, so opening the dashboard is
enough to kill one hours before its `exp`.

That gap became load-bearing the moment six units were gated behind this
verdict: an `exp`-only OK opens the gate for a token the venue has already
thrown away -- precisely the lost day the gate was built to prevent.

THE DISTINCTION THESE TESTS EXIST TO PROTECT is UNKNOWN vs INVALID.
  INVALID  FYERS answered, and the answer was no  -> FAIL, block the day
  UNKNOWN  we could not ask (network, DNS, timeout) -> record it, say it out
           loud, and DO NOT downgrade an otherwise-OK verdict
Collapsing UNKNOWN into INVALID would block every unit whenever FYERS is
briefly unreachable, turning a transient into a lost day. Collapsing it the
other way -- which the first implementation did, by swallowing
AuthenticationError in a bare `except Exception` -- lets a dead token read as
"couldn't ask", which is the original defect wearing a new hat.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import preflight_fyers_token as pf

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
NOW = datetime.datetime(2026, 8, 24, 8, 45, tzinfo=IST)
ALIVE = datetime.datetime(2026, 8, 25, 6, 0, tzinfo=IST)
FIRE = ("bujji-options-os-trading.timer", datetime.datetime(2026, 8, 24, 9, 14, tzinfo=IST))


class TestInvalidBlocksTheDay:
    def test_a_revoked_token_fails_even_though_exp_is_in_the_future(self):
        """The exact 2026-08-21 case: exp is tomorrow, FYERS says no."""
        v = pf.assess(ALIVE, [FIRE], NOW, live=(pf.LIVE_INVALID, "code=-15 invalid token"))
        assert v["status"] == "FAIL"
        assert "rejected" in v["reason"]
        assert v["live_check"] == pf.LIVE_INVALID

    def test_it_overrides_an_otherwise_clean_exp_verdict(self):
        exp_only = pf.assess(ALIVE, [FIRE], NOW, live=(pf.LIVE_VALID, "ok"))
        assert exp_only["status"] == "OK"          # same inputs, valid token
        revoked = pf.assess(ALIVE, [FIRE], NOW, live=(pf.LIVE_INVALID, "code=-17"))
        assert revoked["status"] == "FAIL"         # only the live result differs


class TestUnknownNeverBecomesInvalid:
    def test_an_unreachable_fyers_does_not_block_a_token_that_looks_alive(self):
        """"I could not ask" must never become "it is dead" -- the same rule
        _broker_reports_flat states for position reads. Blocking here would
        convert a network blip into a lost trading day."""
        v = pf.assess(ALIVE, [FIRE], NOW, live=(pf.LIVE_UNKNOWN, "Timeout"))
        assert v["status"] == "OK"

    def test_but_it_says_so_in_the_reason(self):
        v = pf.assess(ALIVE, [FIRE], NOW, live=(pf.LIVE_UNKNOWN, "Timeout"))
        assert "NOT confirmed with FYERS" in v["reason"]
        assert v["live_check"] == pf.LIVE_UNKNOWN

    def test_unknown_does_not_rescue_an_expired_token(self):
        """An UNKNOWN live check must not paper over a dead `exp`."""
        dead = datetime.datetime(2026, 8, 24, 6, 0, tzinfo=IST)   # before the 09:14 fire
        v = pf.assess(dead, [FIRE], NOW, live=(pf.LIVE_UNKNOWN, "Timeout"))
        assert v["status"] == "FAIL"


class TestTheVerdictIsAlwaysRecorded:
    @pytest.mark.parametrize("status", [pf.LIVE_VALID, pf.LIVE_INVALID, pf.LIVE_UNKNOWN])
    def test_every_run_records_what_the_live_check_said(self, status):
        v = pf.assess(ALIVE, [FIRE], NOW, live=(status, "detail"))
        assert v["live_check"] == status
        assert v["live_detail"] == "detail"

    def test_skipping_the_check_records_unknown_never_valid(self):
        """--skip-live must not be a way to make a failing token pass."""
        import inspect
        src = inspect.getsource(pf.main)
        assert "LIVE_UNKNOWN" in src and "skip_live" in src
        assert "LIVE_VALID" not in src, "main() must never assert VALID without asking"


class TestAuthFailureIsAVerdictNotAnUnknown:
    def test_authentication_error_is_classified_invalid(self):
        """A negative control caught this: the first implementation swallowed
        AuthenticationError in a bare `except Exception` and reported a dead
        token as UNKNOWN. FyersBroker raises it ONLY for auth cases -- its
        classifier docstring says it "never raises for anything else" -- so
        that exception is the venue's own no."""
        import ast, inspect, textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(pf.live_token_check)))

        # AST, not string search: this function has TWO try blocks, and the
        # first is an import guard whose `except Exception` is legitimate.
        # A naive src.index("except Exception") finds that one and reports a
        # defect that is not there -- it did, on the first version of this
        # test.
        probes = [n for n in ast.walk(tree) if isinstance(n, ast.Try)
                  and any(isinstance(h.type, ast.Name) and h.type.id == "AuthenticationError"
                          for h in n.handlers)]
        assert len(probes) == 1, f"expected one AuthenticationError handler, found {len(probes)}"

        names = [getattr(h.type, "id", None) for h in probes[0].handlers]
        assert names.index("AuthenticationError") < names.index("Exception"), (
            f"handler order is {names}; the bare Exception precedes "
            "AuthenticationError, so a dead token is swallowed into UNKNOWN again")

        auth_handler = probes[0].handlers[names.index("AuthenticationError")]
        assert "LIVE_INVALID" in ast.unparse(auth_handler)

    def test_it_never_calls_a_trading_endpoint(self):
        """`profile` only: no market data, no order, no position mutation."""
        import inspect
        src = inspect.getsource(pf.live_token_check)
        assert '_call("profile")' in src
        for forbidden in ("place_order", "modify_order", "cancel_order", "positions", "funds"):
            assert forbidden not in src

    def test_execution_is_neutered_before_the_broker_is_used(self):
        import inspect
        src = inspect.getsource(pf.live_token_check)
        assert "disable_live_execution" in src

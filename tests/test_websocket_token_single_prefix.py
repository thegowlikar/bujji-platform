"""Exactly ONE "{app_id}:" prefix may reach the FYERS websocket SDK.

WHAT HAPPENED. `FyersTickFeed` builds the SDK's required
`"{app_id}:{access_token}"` form itself, at the FyersDataSocket construction
site. The trading runner ALSO built it before handing the token over, so the
SDK received `"APPID:APPID:TOKEN"`. It cannot authenticate that: the
symbol-token lookup fails, no symbol is ever subscribed, and the feed reports
itself CONNECTED while delivering zero ticks.

From the journal of 2026-08-21, the only unit that trades:

  09:52:35  tick_feed_error
  09:52:35  tick feed priced 0/2 legs -- falling back to entry prices
  ...       every subsequent cycle identical
  15:33:45  cycles_priced_from_ticks=0  cycles_blind=4  session_blind=True

Three blind cycles tripped the emergency brake on a naked short strangle, the
brake then failed, and the position ran unmanaged until the EOD sweep. This
one argument is the head of that chain.

WHY IT SURVIVED. The two callers that get it right are the ones that do not
trade: run_live_shadow.py:195 passes `live_tick_credentials()` verbatim (the
bare token), and the certification probe drives FyersDataSocket directly with
a single prefix -- the format proven against REST on 2026-08-20 (ws ltp ==
REST ltp, 0.0000% deviation). Only the trading runner double-prefixed, so
every observation-only path looked healthy.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class TestTheSDKReceivesExactlyOnePrefix:
    def test_fyers_tick_feed_adds_the_prefix_itself(self, monkeypatch):
        """The class owns the join. Callers pass the BARE token."""
        import bujji.broker.fyers_ws as ws
        import logging

        captured = {}

        class _FakeSocket:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def connect(self):
                pass

            def close_connection(self):
                pass

        monkeypatch.setattr(ws.data_ws, "FyersDataSocket", _FakeSocket)
        monkeypatch.setattr(ws.threading, "Thread",
                            lambda *a, **k: type("T", (), {"start": lambda self: None})())

        feed = ws.FyersTickFeed("MYAPP-100", "RAWTOKEN",
                                logging.getLogger("t"), log_path="/tmp")
        feed.start()

        token = captured.get("access_token")
        assert token == "MYAPP-100:RAWTOKEN", (
            f"the SDK received {token!r}; it must be exactly "
            f"'{{app_id}}:{{token}}'")
        assert token.count("MYAPP-100") == 1, (
            f"the app_id appears {token.count('MYAPP-100')} times in {token!r} -- "
            f"a double prefix authenticates nothing and subscribes no symbol")


class TestTheTradingRunnerPassesTheBareToken:
    """This is the site that was wrong, and the only one that can trade."""

    @staticmethod
    def _tick_feed_call():
        tree = ast.parse((REPO_ROOT / "bujji_options_os_runner.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and \
                    getattr(node.func, "id", None) == "FyersTickFeed":
                return node
        raise AssertionError("the runner no longer constructs a FyersTickFeed")

    def test_the_token_argument_is_not_an_f_string(self):
        call = self._tick_feed_call()
        assert len(call.args) >= 2, "FyersTickFeed is called with too few positional args"
        token_arg = call.args[1]
        assert not isinstance(token_arg, ast.JoinedStr), (
            "the runner is building '{app_id}:{token}' before handing it over; "
            "FyersTickFeed already does that at the SDK boundary, so this "
            "produces APPID:APPID:TOKEN and the feed subscribes NOTHING")
        assert isinstance(token_arg, ast.Name), (
            f"expected the bare token name, got {type(token_arg).__name__}")

    def test_the_runner_and_the_shadow_runner_agree(self):
        """run_live_shadow.py never had this bug. Both callers must now pass
        the token the same way -- one of them trades, and it was the wrong one."""
        shadow = (REPO_ROOT / "run_live_shadow.py").read_text()
        tree = ast.parse(shadow)
        call = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.Call)
                     and getattr(n.func, "id", None) == "FyersTickFeed"), None)
        assert call is not None
        assert not isinstance(call.args[1], ast.JoinedStr)
        assert not isinstance(self._tick_feed_call().args[1], ast.JoinedStr)

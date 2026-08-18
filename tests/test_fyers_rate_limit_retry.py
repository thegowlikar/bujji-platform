"""Rate-limit retry at the FYERS transport choke point.

Pacing lowers the odds of a refusal but cannot remove them: the ceiling is
per ACCOUNT and the pacer is per interpreter, so a second Bujji process can
push the account over on its own. Verified live on 2026-08-17, both ceilings
answer with code=429 -- per-second as "Bad request", per-minute as "request
limit reached".

The property that matters most here is NOT that reads recover. It is that a
refused WRITE is never repeated automatically: a refusal alone cannot tell
this codebase whether the exchange rejected the instruction or accepted it
and refused only the acknowledgement, and repeating it under the second
reading would duplicate it.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from bujji.broker import fyers as fyers_module
from bujji.broker.fyers import (
    _RATE_LIMIT_BACKOFF_SECONDS,
    _RETRYABLE_ACTIONS,
    FyersBroker,
    _is_rate_limited,
)
from bujji.core.config import BrokerConfig

LIMITED = {"s": "error", "code": 429, "message": "request limit reached"}
LIMITED_PER_SECOND = {"s": "error", "code": 429, "message": "Bad request"}
OK = {"s": "ok", "d": [{"v": {"lp": 24300.0}}]}
AUTH_FAILED = {"s": "error", "code": -8, "message": "Your token has expired"}
BAD_SYMBOL = {"s": "error", "code": -300, "message": "invalid symbol"}


@pytest.fixture
def broker():
    cfg = BrokerConfig(name="fyers", app_id="APP-100", access_token="tok")
    return FyersBroker(cfg, logging.getLogger("test-retry"))


@pytest.fixture
def slept(monkeypatch):
    """Record backoff waits without actually waiting."""
    waits = []

    async def _fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(fyers_module.asyncio, "sleep", _fake_sleep)
    return waits


def _scripted(broker, responses):
    """Replace the single SDK round-trip with a scripted sequence."""
    calls = []

    async def _invoke_once(action, **params):
        calls.append((action, params))
        return responses[min(len(calls) - 1, len(responses) - 1)]

    broker._invoke_once = _invoke_once
    return calls


class TestDetection:
    def test_only_429_counts_as_rate_limited(self):
        assert _is_rate_limited(LIMITED) is True
        assert _is_rate_limited(LIMITED_PER_SECOND) is True
        assert _is_rate_limited(AUTH_FAILED) is False
        assert _is_rate_limited(BAD_SYMBOL) is False
        assert _is_rate_limited(OK) is False

    def test_tolerates_odd_payload_shapes(self):
        assert _is_rate_limited({"code": "429"}) is True     # stringified
        assert _is_rate_limited({"code": None}) is False
        assert _is_rate_limited({}) is False
        assert _is_rate_limited(None) is False
        assert _is_rate_limited("not a dict") is False


class TestWritesAreNeverRetried:
    """The safety property. A refused write is surfaced, never repeated."""

    @pytest.mark.parametrize("action", ["place_order", "cancel_order"])
    def test_a_refused_write_is_returned_immediately(self, broker, slept, action):
        calls = _scripted(broker, [LIMITED, OK])
        result = asyncio.run(broker._call(action))
        assert len(calls) == 1, f"{action} was retried -- it must not be"
        assert result == LIMITED
        assert slept == []

    def test_the_allowlist_excludes_every_write_action(self, broker):
        """Guards against a future write action being added to the dispatch
        table and silently inheriting retry."""
        writes = set(FyersBroker._DATA_ARG_ACTIONS) - _RETRYABLE_ACTIONS
        assert "place_order" in writes
        assert "cancel_order" in writes

    def test_an_unknown_action_is_not_retried(self, broker, slept):
        """Allowlist, not denylist: anything unrecognised defaults to safe."""
        calls = _scripted(broker, [LIMITED, OK])
        asyncio.run(broker._call("some_future_write"))
        assert len(calls) == 1
        assert slept == []


class TestReadsRecover:
    def test_a_transient_refusal_is_retried_and_succeeds(self, broker, slept):
        calls = _scripted(broker, [LIMITED, OK])
        assert asyncio.run(broker._call("historical")) == OK
        assert len(calls) == 2
        assert slept == [_RATE_LIMIT_BACKOFF_SECONDS[0]]

    def test_both_observed_ceilings_are_retried(self, broker, slept):
        for refusal in (LIMITED, LIMITED_PER_SECOND):
            b_calls = _scripted(broker, [refusal, OK])
            assert asyncio.run(broker._call("optionchain")) == OK
            assert len(b_calls) == 2

    def test_success_never_sleeps_or_repeats(self, broker, slept):
        calls = _scripted(broker, [OK])
        assert asyncio.run(broker._call("ltp")) == OK
        assert len(calls) == 1
        assert slept == []


class TestBoundedAndHonest:
    def test_retries_are_bounded(self, broker, slept):
        """A permanently rate-limited account must not retry forever and
        stall a trading session indefinitely."""
        calls = _scripted(broker, [LIMITED])
        asyncio.run(broker._call("historical"))
        assert len(calls) == len(_RATE_LIMIT_BACKOFF_SECONDS) + 1
        assert slept == list(_RATE_LIMIT_BACKOFF_SECONDS)

    def test_exhaustion_returns_the_refusal_unchanged(self, broker, slept):
        """ERROR SEMANTICS PRESERVED: the caller's own _raise_if_error must
        still raise exactly as before this retry existed. Nothing swallowed,
        no empty result substituted."""
        _scripted(broker, [LIMITED])
        result = asyncio.run(broker._call("historical"))
        assert result == LIMITED
        with pytest.raises(RuntimeError, match="429"):
            broker._raise_if_error(result, "historical")

    def test_backoff_grows(self, broker):
        """One short wait clears the per-second ceiling; the per-minute one
        needs most of a minute, so later waits must be longer."""
        assert list(_RATE_LIMIT_BACKOFF_SECONDS) == sorted(_RATE_LIMIT_BACKOFF_SECONDS)
        assert _RATE_LIMIT_BACKOFF_SECONDS[0] < _RATE_LIMIT_BACKOFF_SECONDS[-1]
        assert sum(_RATE_LIMIT_BACKOFF_SECONDS) <= 60, "must not outlast a 300s cycle"


class TestOtherFailuresAreUntouched:
    @pytest.mark.parametrize("failure", [AUTH_FAILED, BAD_SYMBOL])
    def test_non_rate_limit_errors_are_not_retried(self, broker, slept, failure):
        """Retrying an expired token or a bad symbol just repeats the same
        failure more slowly."""
        calls = _scripted(broker, [failure, OK])
        assert asyncio.run(broker._call("historical")) == failure
        assert len(calls) == 1
        assert slept == []

    def test_an_auth_failure_still_raises_through_its_own_classifier(self, broker, slept):
        from bujji.broker.errors import AuthenticationError
        _scripted(broker, [AUTH_FAILED])
        result = asyncio.run(broker._call("historical"))
        with pytest.raises(AuthenticationError):
            broker._raise_if_auth_error(result)


class TestWiring:
    def test_the_retry_waits_on_the_event_loop(self):
        """Not the pacer's blocking wait: that one deliberately blocks its
        own worker thread, this one must yield so other coroutines run."""
        import inspect
        source = inspect.getsource(FyersBroker._call)
        assert "await asyncio.sleep(" in source
        assert "time.sleep(" not in source

    def test_the_single_round_trip_still_goes_through_the_pacer(self):
        import inspect
        source = inspect.getsource(FyersBroker._invoke_once)
        assert source.count("_paced(method)") == 2, source

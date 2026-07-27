"""Production Authentication Adapter — BUJJI Options OS v3, Engineering
Series 51, Sprint 1.

Satisfies `bujji.authentication.engine.AuthenticationProviderInterface`
(Series 49) using production's own existing authentication stack
(identified during the Series 41 architecture review):
`bujji.broker.fyers.FyersBroker.connect()`, which already internally
calls `bujji.broker.fyers_token_manager.FyersTokenManager.refresh()`
when the stored access token is invalid or expired. This adapter never
duplicates, redesigns, or replaces either -- it translates a call
through `connect()` into an `AuthenticationOutcome` (Series 49's own
type; no new outcome type is introduced here) and nothing else.

The adapter never refreshes a token itself, never stores a credential,
never retries, never caches a session across calls, and never
implements expiry logic of its own -- expiry remains
`authentication.engine.check_expiry()`'s job (Series 49), entirely
downstream of this adapter.

This module deliberately does NOT import `bujji.broker.fyers.FyersBroker`
or the FYERS SDK -- it accepts any object satisfying the narrow shape
production's `FyersBroker` already has: an async `connect()` method
that raises `bujji.broker.errors.AuthenticationError` on failure and
returns normally on success. Production's real `FyersBroker` instance
satisfies this shape without any change on its part; this adapter's
own tests use a lightweight stub with the same shape, so no test in
this sprint ever imports the FYERS SDK, opens a socket, or touches a
real broker account.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Optional

from ..authentication.models import AuthenticationOutcome
from ..broker.errors import AuthenticationError

Clock = Callable[[], datetime]


def _run_async(coro: Any) -> Any:
    """Run a coroutine from synchronous code.

    A known v1 limitation, disclosed rather than worked around: if
    this is called from within an already-running event loop, it
    raises rather than silently doing something surprising (nesting
    event loops). Calling from ordinary synchronous code -- the
    expected v1 usage -- works via `asyncio.run()`.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "ProductionAuthenticationAdapter.authenticate() cannot be called "
        "from within an already-running event loop in v1 -- call it from "
        "synchronous code."
    )


class ProductionAuthenticationAdapter:
    """Satisfies `AuthenticationProviderInterface` by translating one
    call through an already-constructed production broker object's own
    `connect()` method.

    Contains no authentication logic of its own: no retry, no token
    refresh, no credential storage, no session cache, no expiry
    calculation. Every one of those already exists in production
    (`FyersBroker`/`FyersTokenManager`) and is reused here, never
    duplicated.
    """

    def __init__(
        self,
        broker: Any,
        broker_identity: Optional[str] = None,
        clock: Clock = datetime.now,
    ) -> None:
        self._broker = broker
        self._broker_identity = broker_identity
        self._clock = clock
        self.last_trace: str = ""

    def authenticate(self) -> AuthenticationOutcome:
        """Invoke the wrapped production broker's own `connect()`
        exactly once and translate its outcome into an
        `AuthenticationOutcome` -- never fabricating success, never
        suppressing a production error, never retrying.
        """
        request_trace = "Runtime request: authenticate() via ProductionAuthenticationAdapter."
        method_trace = (
            "Production method invoked: broker.connect() "
            "(FyersBroker.connect() internally calls FyersTokenManager.refresh() "
            "on an invalid/expired token -- unchanged, not reimplemented here)."
        )

        try:
            _run_async(self._broker.connect())
        except AuthenticationError as exc:
            response_trace = f"Production response: AuthenticationError raised: {exc}"
            outcome = AuthenticationOutcome(success=False, broker_identity=None, expires_at=None, error=str(exc))
            outcome_trace = f"AuthenticationOutcome: FAILED ({exc})."
            self.last_trace = " | ".join([request_trace, method_trace, response_trace, outcome_trace])
            return outcome
        except Exception as exc:  # noqa: BLE001 - never suppressed; always surfaced as a failed outcome
            response_trace = f"Production response: unexpected exception raised: {exc!r}"
            outcome = AuthenticationOutcome(success=False, broker_identity=None, expires_at=None, error=repr(exc))
            outcome_trace = f"AuthenticationOutcome: FAILED ({exc!r})."
            self.last_trace = " | ".join([request_trace, method_trace, response_trace, outcome_trace])
            return outcome

        response_trace = "Production response: connect() returned without raising -- CONNECTED."
        outcome = AuthenticationOutcome(
            success=True, broker_identity=self._broker_identity, expires_at=None, error=None
        )
        outcome_trace = "AuthenticationOutcome: AUTHENTICATED / CONNECTED."
        self.last_trace = " | ".join([request_trace, method_trace, response_trace, outcome_trace])
        return outcome

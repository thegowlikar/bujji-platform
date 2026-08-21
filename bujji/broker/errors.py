"""Distinct broker error types (E1/E2 — auth/session failures).

Not every broker failure is a transient network blip. An expired access token
(FYERS tokens are typically valid ~24h and expire once daily) or a session
invalidated by a concurrent login elsewhere will not resolve by retrying with
the same credentials. Treating it like a network error wastes the entire retry
budget on a call that can never succeed, and — worse — delays the one signal an
operator actually needs (refresh the token, then restart) behind several
pointless backoff sleeps.

``AuthenticationError`` is the single, broker-agnostic signal every adapter
raises for this class of failure so the Execution Engine can short-circuit
retries and escalate immediately instead of following the ordinary retry
schedule.
"""
from __future__ import annotations


class AuthenticationError(RuntimeError):
    """Raised when a broker call fails due to an invalid/expired session.

    Covers: an expired or revoked access token, a session invalidated by a
    concurrent login elsewhere (E2), and any broker-reported "unauthorized"
    condition. Deliberately does NOT subclass :class:`ExecutionError` — it is
    a distinct failure class that must never be silently retried or absorbed
    into the generic "broker call failed" bucket; callers must handle it
    explicitly.

    This is a detection signal only. Resolving it (refreshing the token,
    re-authenticating) is a human action — nothing in this codebase attempts
    to automatically obtain new credentials.
    """


class PositionReadError(RuntimeError):
    """Raised when the broker's position book could not be READ.

    Never raised for a genuinely flat account -- an `s: "ok"` response with
    no open legs is a real answer and returns an empty list.

    Exists because `get_open_positions()` had no success check at all: it went
    straight to `data.get("netPositions", [])`, so an error response, a
    malformed body, or a renamed field all produced `[]` -- and `[]` means
    FLAT to every caller. The adapter was manufacturing the one answer the
    entire closure machine above it is built to distrust.

    Both callers already do the right thing with an exception --
    `_broker_reports_flat` and `eod_closure.discover_broker_positions` each
    convert it to None, meaning UNKNOWN, whose own docstring says: "A read
    that fails returns None, never False: 'I could not ask' must never become
    'there is nothing there'." Raising is what lets that machinery work.
    """


class UnverifiedPositionSchemaError(RuntimeError):
    """Raised instead of placing a REAL order while `netQty` / `symbol` in the
    FYERS positions payload are still unconfirmed against a live account.

    `FYERS_POSITION_SCHEMA_VERIFIED` documents itself as existing "so that fact
    is a gate rather than a comment" -- but nothing consulted it. Its own
    comment names the harm precisely: if `netQty` were actually called
    something else, every position row would read as qty 0, every position
    would be filtered out as flat, and the account would look EMPTY. It
    "manufactures flatness", and it does so silently and in the dangerous
    direction.

    Placing an order you cannot later prove you closed is the one thing an
    options-SELLING system must never do, so the gate sits on `place_order`:
    the only call that can create a position. Reads, cancels and exits are
    deliberately NOT gated -- blocking those would strand a position rather
    than prevent one.

    Clearing this is an OPERATOR action, never an inference: observe a real
    open position, confirm the field names against the live payload, then set
    the flag. It must not be flipped by reasoning.
    """


class LiveExecutionDisabledError(RuntimeError):
    """Raised instead of ever placing/modifying/cancelling a real order.

    This is the enforcement mechanism behind Paper Trading mode's composite
    broker (see :mod:`bujji.broker.hybrid`): the live data source's order-
    placing methods are neutered at construction time to raise this
    immediately, with no network call attempted, so that even a coding
    mistake reaching the live broker's `place_order`/`cancel_order`/
    `get_order`/`get_open_positions` cannot place, modify, or discover a real
    order or position. If you see this raised, real capital was NOT touched.
    """

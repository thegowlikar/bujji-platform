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

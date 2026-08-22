"""A paper position book whose reads can be made to FAIL.

WHY THIS EXISTS. `self._broker` is hardcoded to `PaperBroker`, whose
`get_open_positions()` returns `list(self._positions.values())` -- an
in-process dictionary read that cannot fail, cannot time out, and cannot
return a shape nobody expects. Every UNKNOWN branch downstream is therefore
correct and completely unexercised: the read they guard against is impossible.

A simulator that can only succeed does not simulate the thing that hurts. This
wraps a paper broker and lets a test make the read fail the way a real one
does, so the fail-closed paths are proven rather than merely present.

NOT A TEST DOUBLE. It lives in the production package deliberately: a fault
injector kept in tests would drift from the broker it wraps, and the paths it
exercises are production paths.
"""
from __future__ import annotations

from typing import Any, List, Optional


class ControllablePaperPositions:
    """Delegates to a real paper broker until told to misbehave."""

    def __init__(self, broker) -> None:
        self._broker = broker
        self._raise_next: Optional[BaseException] = None
        self._raise_always: Optional[BaseException] = None
        self._return_instead: Any = _UNSET
        self.reads = 0

    # -- fault injection -------------------------------------------------- #
    def fail_once(self, exc: BaseException) -> "ControllablePaperPositions":
        """The next read raises, and reads after it behave normally --
        a transient network failure, which is the common real shape."""
        self._raise_next = exc
        return self

    def fail_always(self, exc: BaseException) -> "ControllablePaperPositions":
        """Every read raises -- an expired token, a dead endpoint."""
        self._raise_always = exc
        return self

    def return_instead(self, value: Any) -> "ControllablePaperPositions":
        """Return `value` rather than a position list -- a shape change.

        The dangerous one: not a failure, an ANSWER that means nothing. `None`,
        a dict where a list belongs, rows missing a field. Each must land as
        UNKNOWN rather than as an empty, flat-looking book.
        """
        self._return_instead = value
        return self

    def behave(self) -> "ControllablePaperPositions":
        self._raise_next = None
        self._raise_always = None
        self._return_instead = _UNSET
        return self

    # -- the broker surface consumers use --------------------------------- #
    async def get_open_positions(self) -> List[dict]:
        self.reads += 1
        if self._raise_always is not None:
            raise self._raise_always
        if self._raise_next is not None:
            exc, self._raise_next = self._raise_next, None
            raise exc
        if self._return_instead is not _UNSET:
            return self._return_instead
        return await self._broker.get_open_positions()

    def __getattr__(self, name):
        # Everything else is the wrapped broker's. Only the position read is
        # under this class's control.
        return getattr(self._broker, name)


class _Unset:
    def __repr__(self) -> str:
        return "<unset>"


_UNSET = _Unset()

"""The one place this system asks a broker what it holds.

WHAT THIS REPLACES. Four consumers each answered the question their own way:

  * `PositionRealityRegistry._open_symbols` read the broker and then
    INTERSECTED the answer with a local table of symbols this process happened
    to register -- so a position Bujji never registered was invisible BY
    CONSTRUCTION. That is local process state used as broker truth, and it is
    the failure this boundary exists to end.
  * `eod_closure` read unfiltered and three-valued (correctly).
  * `_broker_reports_flat` read unfiltered and three-valued (correctly, and
    separately).
  * order recovery read the journal, which is a different question.

NO FILTERING, EVER. This adapter reports what the broker reports. A caller that
cares only about its own symbols filters the RESULT; it does not get to change
what "the account holds" means on the way in. The difference matters exactly
when it is most dangerous: a leg the process forgot about is still real.

QUANTITY PARSING FAILS CLOSED. A row whose quantity cannot be read is not
skipped and is not treated as zero -- the whole read becomes UNKNOWN. A single
unparseable row means this adapter no longer understands the payload, and a
partially-understood position book is more dangerous than an unreadable one
because it looks complete.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Optional

from .models import BrokerTruth, OpenLeg, flat, open_with, unknown


class BrokerPositionTruth:
    """Reads one broker's position book into a three-valued answer.

    `run_async` is injected because the brokers expose async methods while
    every consumer here is synchronous, and each of them had grown its own
    `asyncio.run` call.
    """

    def __init__(self, broker, *, source: str, schema_verified: bool,
                 run_async: Optional[Callable] = None, logger=None) -> None:
        if broker is None:
            raise ValueError(
                "BrokerPositionTruth requires a broker -- there is no default, "
                "because a boundary with no broker behind it would answer "
                "CONFIRMED_FLAT to every question")
        self._broker = broker
        self._source = source
        self._schema_verified = bool(schema_verified)
        self._logger = logger or logging.getLogger("bujji.broker_truth")
        if run_async is None:
            import asyncio

            run_async = asyncio.run
        self._run_async = run_async

    @property
    def source(self) -> str:
        return self._source

    @property
    def schema_verified(self) -> bool:
        return self._schema_verified

    def _unknown(self, detail: str) -> BrokerTruth:
        """Every UNKNOWN answer is built here, and none of them carries this
        adapter's `schema_verified` flag.

        An answer we could not read tells us nothing about whether we would
        have understood it, so an UNKNOWN result must not claim verification.
        Six callsites passing the flag by hand is how that rule would drift.
        """
        return unknown(detail, self._source)

    def read(self) -> BrokerTruth:
        """Ask the broker, synchronously. Never raises: every failure is an
        UNKNOWN answer.

        Raising here would push each caller into its own try/except, which is
        precisely how the four divergent readings arose. The answer type
        carries the failure instead.
        """
        try:
            rows = self._run_async(self._broker.get_open_positions())
        except Exception as exc:  # noqa: BLE001 -- a failed read is an answer, not a crash
            return self._unknown(
                f"position read failed: {type(exc).__name__}: {exc}")
        return self._interpret(rows)

    async def read_async(self) -> BrokerTruth:
        """The same answer, awaited.

        Both doors exist because the consumers genuinely differ: the registry
        and lifecycle layers are async, while the EOD closure and the runner's
        own checks are synchronous. `read()` would raise inside a running loop
        (asyncio.run refuses to nest), and an async-only boundary would push
        every synchronous caller back into growing its own asyncio.run --
        which is the duplication this boundary exists to remove.

        Transport is the ONLY difference. Interpretation is shared, so the two
        doors cannot drift into disagreeing about what a payload means.
        """
        try:
            rows = await self._broker.get_open_positions()
        except Exception as exc:  # noqa: BLE001 -- a failed read is an answer, not a crash
            return self._unknown(
                f"position read failed: {type(exc).__name__}: {exc}")
        return self._interpret(rows)

    def _interpret(self, rows) -> BrokerTruth:
        """Turn whatever the broker returned into one of three answers."""
        if rows is None:
            return self._unknown("broker returned no position list at all")
        if not isinstance(rows, Iterable) or isinstance(rows, (str, bytes)):
            return self._unknown(
                f"broker returned {type(rows).__name__}, which is not a "
                f"position list -- the payload shape is not one this adapter "
                f"understands")

        legs = []
        for index, row in enumerate(rows):
            if not hasattr(row, "get"):
                return self._unknown(
                    f"position row {index} is {type(row).__name__}, not a "
                    f"mapping -- refusing to infer the account from a payload "
                    f"this adapter no longer understands")
            symbol = row.get("symbol")
            if not symbol:
                return self._unknown(
                    f"position row {index} carries no symbol -- a holding that "
                    f"cannot be named cannot be reconciled or closed")
            raw_qty = row.get("qty", row.get("quantity"))
            try:
                quantity = int(raw_qty or 0)
            except (TypeError, ValueError):
                return self._unknown(
                    f"position row {index} ({symbol}) has an unreadable "
                    f"quantity {raw_qty!r} -- a partially understood position "
                    f"book is more dangerous than an unreadable one, because "
                    f"it looks complete")
            if quantity <= 0:
                continue        # a closed or zero row is not a holding
            legs.append(OpenLeg(
                symbol=str(symbol), quantity=quantity,
                side=row.get("side"),
                average_price=_optional_float(row.get("avg_price",
                                                      row.get("average_price"))),
                raw=dict(row)))

        if not legs:
            return flat("broker reports no open legs", self._source,
                        self._schema_verified)
        return open_with(
            legs,
            "open: " + ", ".join(f"{leg.symbol}x{leg.quantity}" for leg in legs),
            self._source, self._schema_verified)


def _optional_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def for_paper(broker, *, run_async=None, logger=None) -> BrokerPositionTruth:
    """The simulator's book. `schema_verified=True` -- its rows are produced by
    this codebase, so their shape is not a guess about someone else's API.

    NOTE what that does NOT mean: the paper broker's book is an in-process
    dictionary, so this reports simulator truth, not market truth. It is
    labelled `paper` in every result so no reader can mistake the two.
    """
    return BrokerPositionTruth(broker, source="paper", schema_verified=True,
                               run_async=run_async, logger=logger)


def for_fyers_read_only(broker, *, run_async=None, logger=None) -> BrokerPositionTruth:
    """The live account, READ ONLY.

    `schema_verified` comes from `FYERS_POSITION_SCHEMA_VERIFIED`, which is
    False and must stay False. The response's TOP-LEVEL shape was confirmed
    live against an empty book; the per-row field names (`netQty`, `netAvg`)
    have never been seen with a real position in the account, and seeing one
    requires placing a real order. Until that happens this boundary reports
    every answer as schema-unverified, and no consumer may treat a
    CONFIRMED_FLAT from it as certified.
    """
    from bujji.broker.fyers import FYERS_POSITION_SCHEMA_VERIFIED

    return BrokerPositionTruth(
        broker, source="fyers_read_only",
        schema_verified=bool(FYERS_POSITION_SCHEMA_VERIFIED),
        run_async=run_async, logger=logger)


def for_broker(broker, *, run_async=None, logger=None) -> BrokerPositionTruth:
    """The right adapter for a broker this caller did not choose.

    Exists so components that are handed a broker (the registry, for one) get
    a correctly LABELLED reader without every construction site having to know
    which broker it holds.

    An unrecognised broker gets schema_verified=False. A row shape this
    codebase has never confirmed is precisely the case where a verification
    claim would be a guess, and the fallback must be the honest one.
    """
    try:
        from bujji.broker.paper import PaperBroker
        if isinstance(broker, PaperBroker):
            return for_paper(broker, run_async=run_async, logger=logger)
    except ImportError:  # pragma: no cover -- paper is always importable
        pass
    try:
        from bujji.broker.hybrid import HybridPaperBroker
        if isinstance(broker, HybridPaperBroker):
            # Real market data, paper ledger: the position rows are this
            # codebase's own, so their shape is not a guess -- but the source
            # says "hybrid" so no reader mistakes it for a live account.
            return BrokerPositionTruth(
                broker, source="hybrid_paper_ledger", schema_verified=True,
                run_async=run_async, logger=logger)
    except ImportError:  # pragma: no cover
        pass
    try:
        from bujji.broker.fyers import FyersBroker
        if isinstance(broker, FyersBroker):
            return for_fyers_read_only(broker, run_async=run_async, logger=logger)
    except ImportError:  # pragma: no cover
        pass
    return BrokerPositionTruth(
        broker, source=type(broker).__name__, schema_verified=False,
        run_async=run_async, logger=logger)

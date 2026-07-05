"""Serialization of a live :class:`Position` for crash recovery (C1).

A position must survive a process restart so the bot can resume managing it (or
safely flatten it) rather than abandoning an open short option. This module is
the single source of truth for turning a ``Position`` into a plain dict and
back, including its nested contract, opening range, and thesis.

It carries no trading logic — it only preserves state faithfully.

Schema safety (C_D2): a snapshot file is operator-editable JSON and can drift
from what this module expects (missing keys, wrong nesting, garbage values —
e.g. from manual edits, a future field rename, or partial disk corruption that
survives JSON parsing). ``position_from_dict`` must NEVER raise an assortment
of raw ``KeyError``/``TypeError``/``ValueError`` for a caller to guess at;
every failure is normalized into a single :class:`PositionSchemaError` so
recovery can catch one exception type and safely fall back to broker
reconciliation instead of crashing startup.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .enums import Direction, OptionType, Side
from .models import OpeningRange, OptionContract, Position
from .thesis import TradeThesis


class PositionSchemaError(ValueError):
    """Raised when a serialized position dict cannot be reconstructed.

    Always carries the original cause via exception chaining (``raise ... from
    exc``) so the underlying ``KeyError``/``TypeError``/etc. is still visible
    in logs/tracebacks for diagnosis, while giving callers one exception type
    to catch defensively.
    """


def _thesis_to_dict(t: TradeThesis) -> dict[str, Any]:
    return {
        "direction": t.direction.value,
        "created_at": t.created_at.isoformat(),
        "entry_spot": t.entry_spot,
        "entry_vwap": t.entry_vwap,
        "breakout_close": t.breakout_close,
        "breakout_body_ratio": t.breakout_body_ratio,
        "body_threshold": t.body_threshold,
        "orb_high": t.orb.high,
        "orb_low": t.orb.low,
    }


def _thesis_from_dict(d: dict[str, Any], orb: OpeningRange) -> TradeThesis:
    return TradeThesis(
        direction=Direction(d["direction"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        entry_spot=float(d["entry_spot"]),
        entry_vwap=float(d["entry_vwap"]),
        orb=orb,
        breakout_close=float(d["breakout_close"]),
        breakout_body_ratio=float(d["breakout_body_ratio"]),
        body_threshold=float(d["body_threshold"]),
        conditions=(),  # Not needed to manage or journal; narrative is derived.
    )


def position_to_dict(pos: Position) -> dict[str, Any]:
    """Serialize a Position to a JSON-safe dict."""
    return {
        "contract": {
            "symbol": pos.contract.symbol,
            "underlying": pos.contract.underlying,
            "strike": pos.contract.strike,
            "option_type": pos.contract.option_type.value,
            "expiry": pos.contract.expiry,
            "lot_size": pos.contract.lot_size,
        },
        "direction": pos.direction.value,
        "entry_side": pos.entry_side.value,
        "quantity": pos.quantity,
        "entry_price": pos.entry_price,
        "entry_spot": pos.entry_spot,
        "entry_time": pos.entry_time.isoformat(),
        "orb": {
            "high": pos.orb.high,
            "low": pos.orb.low,
            "start": pos.orb.start.isoformat(),
            "end": pos.orb.end.isoformat(),
        },
        "max_favourable_excursion": pos.max_favourable_excursion,
        "max_adverse_excursion": pos.max_adverse_excursion,
        "max_profit_seen": pos.max_profit_seen,
        "max_loss_seen": pos.max_loss_seen,
        "candles_held": pos.candles_held,
        "thesis": _thesis_to_dict(pos.thesis) if pos.thesis else None,
    }


def _position_from_dict_unsafe(d: dict[str, Any]) -> Position:
    """Rebuild a Position from its serialized dict.

    Raises the raw underlying exception (``KeyError``/``TypeError``/
    ``ValueError``/``AttributeError``) on any schema mismatch. Callers that
    need crash-safety must use :func:`position_from_dict` instead, which wraps
    this in :class:`PositionSchemaError`.
    """
    c = d["contract"]
    contract = OptionContract(
        symbol=str(c["symbol"]),
        underlying=str(c["underlying"]),
        strike=int(c["strike"]),
        option_type=OptionType(c["option_type"]),
        expiry=str(c["expiry"]),
        lot_size=int(c["lot_size"]),
    )
    o = d["orb"]
    orb = OpeningRange(
        high=float(o["high"]),
        low=float(o["low"]),
        start=datetime.fromisoformat(o["start"]),
        end=datetime.fromisoformat(o["end"]),
    )
    thesis: Optional[TradeThesis] = (
        _thesis_from_dict(d["thesis"], orb) if d.get("thesis") else None
    )
    return Position(
        contract=contract,
        direction=Direction(d["direction"]),
        entry_side=Side(d["entry_side"]),
        # int()/float() tolerate numeric-as-string (e.g. "75") but reject
        # genuinely garbage values ("abc", None, a list) with a clear error
        # instead of silently building a Position that will explode later
        # during MTM arithmetic.
        quantity=int(d["quantity"]),
        entry_price=float(d["entry_price"]),
        entry_spot=float(d["entry_spot"]),
        entry_time=datetime.fromisoformat(d["entry_time"]),
        orb=orb,
        max_favourable_excursion=float(d.get("max_favourable_excursion", 0.0)),
        max_adverse_excursion=float(d.get("max_adverse_excursion", 0.0)),
        max_profit_seen=float(d.get("max_profit_seen", 0.0)),
        max_loss_seen=float(d.get("max_loss_seen", 0.0)),
        candles_held=int(d.get("candles_held", 0)),
        thesis=thesis,
    )


def position_from_dict(d: dict[str, Any]) -> Position:
    """Rebuild a Position from its serialized dict, safely.

    Any schema mismatch — a missing key, wrong nesting, an invalid enum value,
    or a non-numeric value where a number is required — is normalized into a
    single :class:`PositionSchemaError` (with the original exception chained)
    so recovery can catch exactly one type and fall back to broker
    reconciliation rather than crash startup or silently build a broken
    ``Position``.
    """
    try:
        return _position_from_dict_unsafe(d)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PositionSchemaError(
            f"corrupt/incompatible position snapshot: {exc!r}"
        ) from exc

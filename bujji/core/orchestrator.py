"""Orchestrator — wires the three modules together via the state machine.

The orchestrator owns NO trading logic of its own. It:
  * pulls completed candles from the Execution Engine (broker),
  * feeds them to the Signal Engine (Module 1),
  * hands accepted signals to the Execution Engine (Module 3) to open a position,
  * and on every subsequent candle asks the Trade Manager (Module 2) to
    reassess, executing an exit when told to.

All branching is expressed as FSM transitions, not nested ifs.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from ..broker.base import Broker
from ..broker.errors import AuthenticationError
from ..execution.engine import ExecutionEngine, ExecutionError
from ..journal.journal import TradeJournal, TradeRecord
from ..signal.engine import SignalEngine
from ..signal.vwap_audit import VwapAuditRecord
from ..trade.manager import TradeManager
from .clock import now_ist
from .config import AppConfig
from .enums import Direction, OptionType, Side, State
from .event_bus import Event, EventBus, EventType
from .logging_setup import log_event
from .models import Candle, OptionContract, OrderRequest, Position, Signal
from .position_codec import PositionSchemaError, position_from_dict, position_to_dict
from .runtime_status import RuntimeStatus
from .session_state import SessionSnapshot, SessionStore
from .state_machine import StateMachine


class Orchestrator:
    """Drives one trading day through the finite state machine."""

    def __init__(
        self,
        config: AppConfig,
        logger: logging.Logger,
        signal_engine: SignalEngine,
        trade_manager: TradeManager,
        execution: ExecutionEngine,
        journal: TradeJournal,
        session_store: SessionStore,
        status: RuntimeStatus,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._cfg = config
        self._log = logger
        self._signal = signal_engine
        self._trade = trade_manager
        self._exec = execution
        self._journal = journal
        self._store = session_store
        self._status = status
        self._bus = event_bus or EventBus(logger)
        self._fsm = StateMachine(logger, on_transition=self._on_transition)
        self._trades_taken = 0
        self._last_candle_ts: Optional[datetime] = None
        self._cycle_decision = ""  # Decision label for the current audit cycle.
        # Idempotency keys for in-flight orders (C3), persisted for recovery.
        self._entry_cid: Optional[str] = None
        self._exit_cid: Optional[str] = None
        # How many times to re-attempt flattening a residual before giving up.
        self._flatten_attempts = max(1, config.broker.retry_attempts)
        # D1: whether the wall clock is currently trusted. When False, new
        # entries are blocked (capital-safety gate); existing positions are
        # still managed/exited normally — distrust of the clock must never
        # strand a position.
        self._clock_trusted = True
        self._clock_distrust_detail = ""

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def startup(self) -> None:
        await self._exec.connect()
        await self._recover()

    async def _recover(self) -> None:
        """Rebuild session state after a restart and reconcile with the broker.

        Capital-protection contract (C1): an open position is NEVER abandoned.
        We cross-check the persisted snapshot against live broker positions and
        take the safe action for every combination:

          parsed + live   -> resume management of the known position
          parsed + flat    -> the position already closed; finalize
          none   + live    -> an orphan (or an unparseable snapshot, see below)
                              we don't recognize; flatten immediately
          none   + flat    -> clean start; honor trade count only

        C_D2 (schema safety): ``snap.position`` is operator-editable JSON and
        may be corrupt or schema-mismatched even though the file itself parsed
        as valid JSON (a missing/renamed key, wrong nesting, a garbage value).
        We NEVER let that crash startup or silently build a broken Position.
        If it can't be reconstructed, we treat it exactly like "no saved
        position" — broker reconciliation is the ground truth and still finds
        (and flattens) any real live position via the orphan path below.
        """
        snap = self._store.load()
        self._trades_taken = snap.trades_taken
        self._entry_cid = snap.entry_client_order_id
        self._exit_cid = snap.exit_client_order_id

        parsed_pos = self._safe_parse_position(snap.position)
        # Only use the saved dict to *prefer-match* a broker position when it
        # parsed successfully; a corrupt dict must not influence matching.
        match_hint = snap.position if parsed_pos is not None else None

        broker_positions = await self._exec.reconcile()
        live_short = self._find_live_short(broker_positions, match_hint)

        if parsed_pos is not None and live_short:
            await self._resume_position(parsed_pos, snap, live_short)
            return
        if parsed_pos is not None and not live_short:
            log_event(self._log, "recovery_position_already_flat",
                      symbol=parsed_pos.contract.symbol)
            self._fsm.restore(State.DONE_FOR_DAY, "position_already_closed")
            self._persist()
            return
        if live_short:  # No usable saved position, but a live short exists.
            log_event(self._log, "recovery_orphan_position", position=live_short)
            self._status.healthy = False
            # Preserve a prior corrupt-snapshot detail (set by
            # _safe_parse_position) instead of clobbering it — both facts
            # matter for the operator investigating this incident.
            prior = self._status.health_detail
            corrupt_note = f"{prior}; " if prior and "corrupt" in prior else ""
            self._status.health_detail = f"{corrupt_note}orphan_position_flattening"
            await self._flatten_orphan(live_short)
            self._fsm.restore(State.DONE_FOR_DAY, "orphan_flattened")
            self._persist()
            return

        # Clean slate: only carry forward the trade count (never re-open).
        if snap.trades_taken >= self._cfg.strategy.max_trades_per_day:
            self._fsm.restore(State.DONE_FOR_DAY, "recovered_max_trades")
            self._persist()

    def _safe_parse_position(self, data: Optional[dict]) -> Optional[Position]:
        """Parse a saved position dict, never raising (C_D2).

        Returns ``None`` — and marks the session unhealthy so an operator
        notices — instead of propagating a schema mismatch out of startup.
        """
        if not data:
            return None
        try:
            return position_from_dict(data)
        except PositionSchemaError as exc:
            log_event(self._log, "recovery_snapshot_position_corrupt", err=str(exc))
            self._status.healthy = False
            self._status.health_detail = f"snapshot_position_corrupt: {exc}"
            return None

    def _find_live_short(self, broker_positions: list[dict],
                         saved: Optional[dict]) -> Optional[dict]:
        """Return the broker short position we own, if any.

        Prefers an exact symbol match with the saved position; otherwise falls
        back to any open short (side SELL, qty > 0) so an unexpected position is
        still surfaced for safety handling.
        """
        shorts = [
            p for p in broker_positions
            if str(p.get("side")) == Side.SELL.value and int(p.get("qty", 0)) > 0
        ]
        if saved:
            want = saved.get("contract", {}).get("symbol")
            for p in shorts:
                if p.get("symbol") == want:
                    return p
        return shorts[0] if shorts else None

    async def _resume_position(self, pos: Position, snap: SessionSnapshot,
                               live_short: dict) -> None:
        """Reinstate a known open position and resume managing it."""
        broker_qty = int(live_short.get("qty", pos.quantity))
        if broker_qty != pos.quantity:
            # Trust the broker's actual size (covers partial-fill drift).
            log_event(self._log, "recovery_qty_adjusted",
                      saved=pos.quantity, broker=broker_qty)
            pos.quantity = broker_qty
        # A synthetic flat candle at the entry gives the momentum check a base.
        synth = Candle(pos.entry_time, pos.entry_spot, pos.entry_spot,
                       pos.entry_spot, pos.entry_spot, 0.0)
        self._trade.open_position(pos, synth)
        resume = State.EXITING if snap.state == State.EXITING.value else State.IN_POSITION
        self._fsm.restore(resume, "resume_open_position")
        self._update_status_position(pos.entry_price, pos.entry_price)
        log_event(self._log, "recovery_resumed", symbol=pos.contract.symbol,
                  qty=pos.quantity, state=resume.value)
        self._persist()
        # Restart-recovery gap fix: without this, a resumed position never
        # re-triggers the Tick Engine's WebSocket subscription (it only
        # listens for POSITION_OPENED), silently losing continuous tick-based
        # MTM/stop-loss monitoring after every restart — falling back to the
        # candle-only backstop indefinitely instead of just until reconnect.
        await self._bus.publish(Event(EventType.POSITION_OPENED, {
            "symbol": pos.contract.symbol, "qty": pos.quantity,
            "entry_premium": pos.entry_price,
            "narrative": pos.thesis.narrative if pos.thesis else "",
        }))

    async def _flatten_orphan(self, live_short: dict) -> None:
        """Immediately buy back an unrecognized short — pure risk reduction."""
        symbol = str(live_short["symbol"])
        qty = int(live_short["qty"])
        opt = OptionType.CE if symbol.endswith("CE") else OptionType.PE
        contract = OptionContract(
            symbol=symbol, underlying=self._cfg.market.underlying, strike=0,
            option_type=opt, expiry="", lot_size=self._cfg.market.lot_size,
        )
        cid = f"ORPHAN-EXIT-{now_ist().strftime('%Y%m%d%H%M%S')}"
        request = OrderRequest(contract=contract, side=Side.BUY, quantity=qty,
                               client_order_id=cid, tag="orphan_exit")
        try:
            await self._exec.submit_and_confirm(request)
            self._clear_auth_flag()
            log_event(self._log, "orphan_flattened", symbol=symbol, qty=qty)
        except AuthenticationError as exc:
            self._mark_auth_expired("orphan_flatten", exc)
        except ExecutionError as exc:
            # Could not flatten — keep unhealthy so operators are alerted.
            log_event(self._log, "orphan_flatten_failed", symbol=symbol,
                      err=str(exc))
            self._status.health_detail = f"orphan_flatten_failed: {exc}"

    # ------------------------------------------------------------------ #
    # Auth/session failure handling (E1/E2) — detection + alerting only.
    # Resolving an expired/invalidated session (refreshing the token) is a
    # human action; nothing here attempts to obtain new credentials.
    # ------------------------------------------------------------------ #
    def _mark_auth_expired(self, context: str, exc: Exception) -> None:
        """Flag a distinct, human-actionable auth failure.

        Deliberately separate from the generic ``healthy``/``health_detail``
        churn other failure classes cause, so an operator (or the dashboard)
        can tell at a glance "this needs a token refresh and a restart" apart
        from "something else went wrong."
        """
        log_event(self._log, "auth_expired_detected", context=context, err=str(exc))
        self._status.healthy = False
        self._status.auth_expired = True
        self._status.health_detail = f"auth_expired ({context}): {exc}"

    def _clear_auth_flag(self) -> None:
        """Self-heal the distinct auth flag once a broker call succeeds again.

        Only touches this specific flag/detail — never overwrites an unrelated
        unhealthy condition another failure class may have set — so a resolved
        auth issue (token refreshed, process restarted) or a one-off
        misclassification doesn't linger as a false alarm.
        """
        if self._status.auth_expired:
            self._status.auth_expired = False
            if self._status.health_detail.startswith("auth_expired"):
                self._status.healthy = True
                self._status.health_detail = "ok"

    # ------------------------------------------------------------------ #
    # Clock trust (D1) — driven by the app run loop's ClockGuard
    # ------------------------------------------------------------------ #
    def set_clock_trust(self, trusted: bool, detail: str = "") -> None:
        """Update whether the wall clock is currently trusted.

        Called once per loop boundary by the app run loop after checking for
        drift. Only gates NEW entries (see ``_handle_pre_position``) — it
        never interferes with managing or exiting an existing position, and
        never overrides the wall-clock EOD square-off, since distrust of the
        clock must never be used as a reason to strand a position.
        """
        if trusted and not self._clock_trusted:
            log_event(self._log, "clock_trust_restored")
        self._clock_trusted = trusted
        self._clock_distrust_detail = detail
        self._status.clock_trusted = trusted
        self._status.clock_drift_detail = detail

    @property
    def bus(self) -> EventBus:
        return self._bus

    def _on_transition(self, previous: State, target: State) -> None:
        self._status.state = target.value
        self._persist()
        self._bus.publish_nowait(Event(
            EventType.STATE_CHANGED,
            {"from": previous.value, "to": target.value},
        ))

    def _persist(self) -> None:
        pos = self._trade.position
        snap = SessionSnapshot(
            state=self._fsm.state.value,
            trades_taken=self._trades_taken,
            position=position_to_dict(pos) if pos else None,
            entry_client_order_id=self._entry_cid,
            exit_client_order_id=self._exit_cid,
        )
        self._store.save(snap)

    # ------------------------------------------------------------------ #
    # Per-candle driver
    # ------------------------------------------------------------------ #
    async def on_candle(self, candle: Candle) -> None:
        """Main entry point — process exactly one completed candle.

        C_D1 (detection only, no remediation): a duplicate/stale candle
        (timestamp at or before the last processed one) is ignored outright so
        it can never double-count momentum/excursion or trigger a spurious
        re-evaluation. A gap larger than expected is logged and surfaced for
        operator visibility — it does not halt trading or attempt to backfill.
        """
        if self._fsm.is_terminal():
            return

        if self._last_candle_ts is not None:
            if candle.timestamp <= self._last_candle_ts:
                self._status.duplicate_candles_ignored += 1
                log_event(
                    self._log, "duplicate_or_stale_candle_ignored",
                    candle_ts=candle.timestamp.isoformat(),
                    last_ts=self._last_candle_ts.isoformat(),
                )
                return
            expected = timedelta(minutes=self._cfg.timing.candle_minutes)
            actual = candle.timestamp - self._last_candle_ts
            if actual > expected * 1.5:  # Tolerate minor scheduler jitter.
                gap_seconds = actual.total_seconds()
                self._status.last_candle_gap_seconds = gap_seconds
                log_event(
                    self._log, "candle_gap_detected",
                    expected_minutes=self._cfg.timing.candle_minutes,
                    actual_gap_seconds=gap_seconds,
                    previous_ts=self._last_candle_ts.isoformat(),
                    current_ts=candle.timestamp.isoformat(),
                )
        self._last_candle_ts = candle.timestamp

        await self._bus.publish(Event(
            EventType.CANDLE_CLOSED,
            {"timestamp": candle.timestamp, "close": candle.close},
        ))

        # Module 1 always runs to keep VWAP/ORB current.
        signal = self._signal.on_candle(candle)
        self._update_status_market(candle)
        self._cycle_decision = f"NO_TRADE:{signal.reason}"

        if self._fsm.state in (State.WAITING, State.READY):
            await self._handle_pre_position(candle, signal)
        elif self._fsm.state is State.IN_POSITION:
            await self._handle_in_position(candle)

        # Audit runs LAST and reads state only — never affects trading logic.
        self._emit_vwap_audit(candle)

    async def _handle_pre_position(self, candle: Candle, signal: Signal) -> None:
        if self._signal.orb_ready and self._fsm.state is State.WAITING:
            self._fsm.transition(State.READY, "orb_complete")

        if not signal.is_trade:
            return
        if self._trades_taken >= self._cfg.strategy.max_trades_per_day:
            self._fsm.transition(State.DONE_FOR_DAY, "max_trades_reached")
            return
        if not self._clock_trusted:
            # D1: refuse to open a NEW position while the wall clock is not
            # trusted (a drift/jump was just detected). This never affects an
            # existing position — only new-entry capital is gated.
            log_event(self._log, "entry_blocked_clock_untrusted",
                      detail=self._clock_distrust_detail)
            return

        await self._bus.publish(Event(EventType.SIGNAL_GENERATED, {
            "direction": signal.direction.value if signal.direction else None,
            "narrative": signal.thesis.narrative if signal.thesis else "",
        }))
        self._fsm.transition(State.CONFIRMED, "signal_confirmed")
        try:
            await self._enter(candle, signal)
        except AuthenticationError as exc:
            # E1/E2: distinct from a generic failure — the broker session/
            # token is invalid. Retrying won't help; a human must refresh
            # credentials and restart. Safe outcome: no position was created.
            self._mark_auth_expired("entry", exc)
            self._fsm.transition(State.READY, "entry_failed_auth")
        except ExecutionError as exc:
            log_event(self._log, "entry_failed", err=str(exc))
            self._status.healthy = False
            self._status.health_detail = f"entry_failed: {exc}"
            # Roll back to READY; do not consume the day on a broker glitch.
            self._fsm.transition(State.READY, "entry_failed_rollback")

    async def _enter(self, candle: Candle, signal: Signal) -> None:
        if signal.direction is None or signal.orb is None:
            raise ExecutionError("entry requested without direction/orb")
        contract = await self._exec_resolve(signal.direction, candle.close)
        requested_qty = self._cfg.risk.lots * contract.lot_size
        cid = f"ENTRY-{candle.timestamp.strftime('%Y%m%d%H%M%S')}"
        # Persist the idempotency key BEFORE placing, so a crash between broker
        # acceptance and our bookkeeping is recoverable (C3).
        self._entry_cid = cid
        self._persist()
        request = OrderRequest(
            contract=contract, side=Side.SELL, quantity=requested_qty,
            client_order_id=cid, tag="entry",
        )
        result = await self._exec.submit_and_confirm(request)
        self._clear_auth_flag()

        # Size the position off what ACTUALLY filled, not what we asked for (C4).
        filled_qty = result.filled_quantity
        position = Position(
            contract=contract,
            direction=signal.direction,
            entry_side=Side.SELL,
            quantity=filled_qty,
            entry_price=result.average_price or 0.0,
            entry_spot=candle.close,
            entry_time=candle.timestamp,
            orb=signal.orb,
            thesis=signal.thesis,
        )
        qty = filled_qty
        self._cycle_decision = f"ENTER:{signal.direction.value}"
        self._trade.open_position(position, candle)
        self._trades_taken += 1
        self._fsm.transition(State.IN_POSITION, "position_filled")
        self._update_status_position(result.average_price, result.average_price)
        await self._bus.publish(Event(EventType.POSITION_OPENED, {
            "symbol": contract.symbol, "qty": qty,
            "entry_premium": result.average_price,
            "narrative": signal.thesis.narrative if signal.thesis else "",
        }))

    async def _exec_resolve(self, direction: Direction, spot: float):
        # Broker resolution is a broker-only concern; the Execution Engine's
        # broker is used directly here for contract resolution.
        return await self._exec._broker.resolve_atm_contract(  # noqa: SLF001
            self._cfg.market.underlying, spot, direction,
            self._cfg.market.strike_interval, self._cfg.market.lot_size,
        )

    async def _handle_in_position(self, candle: Candle) -> None:
        pos = self._trade.position
        if pos is None:
            self._fsm.transition(State.DONE_FOR_DAY, "no_position_in_state")
            return
        try:
            premium = await self._exec.get_ltp(pos.contract)
            self._clear_auth_flag()
        except AuthenticationError as exc:
            # E1/E2: the position is left untouched — we simply could not get
            # a fresh premium this cycle. Retried on the next candle; the
            # wall-clock EOD square-off (C2) is broker-call-independent in
            # timing but will hit this same failure until credentials are
            # refreshed and the process restarted.
            self._mark_auth_expired("in_position_get_ltp", exc)
            return
        except ExecutionError as exc:
            log_event(self._log, "get_ltp_failed", err=str(exc))
            self._status.healthy = False
            self._status.health_detail = f"get_ltp_failed: {exc}"
            return
        decision = self._trade.reassess(candle, self._signal.vwap, premium)
        self._cycle_decision = f"{decision.decision.value}:{decision.reason}"
        self._update_status_position(pos.entry_price, premium, decision.reason,
                                     decision.decision.value)
        await self._bus.publish(Event(EventType.DECISION_MADE, {
            "decision": decision.decision.value, "reason": decision.reason,
        }))

        if decision.should_exit:
            self._fsm.transition(State.EXITING, decision.reason)
            ok = await self._do_exit(candle.timestamp, candle.close, premium,
                                     decision.reason)
            if ok:
                self._fsm.transition(State.DONE_FOR_DAY, "trade_closed")
            else:
                # Not flat — retry on the next candle / EOD square-off.
                self._fsm.transition(State.IN_POSITION, "exit_retry")

    # ------------------------------------------------------------------ #
    # Exit / square-off — guarantees we end flat (C2, C4)
    # ------------------------------------------------------------------ #
    async def _do_exit(self, exit_time: datetime, exit_spot: float,
                       premium: float, reason: str) -> bool:
        """Flatten the position fully, handling partial exit fills.

        Returns True only when the position is completely flat and journaled.
        On failure it leaves the position intact and flags unhealthy, so the
        caller can retry rather than falsely believing we are flat (C4).
        """
        pos = self._trade.position
        if pos is None:
            return True
        if self._exit_cid is None:
            self._exit_cid = f"EXIT-{exit_time.strftime('%Y%m%d%H%M%S')}"
            self._persist()

        remaining = pos.quantity
        exit_premium = premium
        for _ in range(self._flatten_attempts):
            request = OrderRequest(
                contract=pos.contract, side=Side.BUY, quantity=remaining,
                client_order_id=f"{self._exit_cid}-{remaining}", tag="exit",
            )
            try:
                result = await self._exec.submit_and_confirm(request)
                self._clear_auth_flag()
            except AuthenticationError as exc:
                self._mark_auth_expired("exit", exc)
                return False
            except ExecutionError as exc:
                log_event(self._log, "exit_failed", err=str(exc),
                          remaining=remaining)
                self._status.healthy = False
                self._status.health_detail = f"exit_failed: {exc}"
                return False
            exit_premium = result.average_price or exit_premium
            remaining -= result.filled_quantity
            if remaining <= 0:
                break
            log_event(self._log, "exit_partial_remaining", remaining=remaining)

        if remaining > 0:
            self._status.healthy = False
            self._status.health_detail = f"exit_incomplete_remaining_{remaining}"
            log_event(self._log, "exit_incomplete", remaining=remaining)
            return False

        self._journal_trade(pos, exit_time, exit_spot, exit_premium, reason)
        await self._bus.publish(Event(EventType.POSITION_CLOSED, {
            "symbol": pos.contract.symbol, "exit_premium": exit_premium,
            "pnl": round(pos.mtm(exit_premium), 2), "reason": reason,
        }))
        self._trade.close_position()
        self._exit_cid = None
        self._persist()
        return True

    async def square_off(self, reason: str) -> bool:
        """Force-flatten any open position (used by the EOD hard exit, C2).

        This is capital protection, not strategy: it does not consult entry or
        exit rules — it simply ensures we are flat.
        """
        pos = self._trade.position
        if pos is None:
            return True
        if self._fsm.state is State.IN_POSITION:
            self._fsm.transition(State.EXITING, reason)
        try:
            premium = await self._exec.get_ltp(pos.contract)
            self._clear_auth_flag()
        except AuthenticationError as exc:
            self._mark_auth_expired("square_off_get_ltp", exc)
            premium = pos.entry_price
        except ExecutionError:
            premium = pos.entry_price
        try:
            spot = await self._exec.get_spot(self._cfg.market.underlying)
            self._clear_auth_flag()
        except AuthenticationError as exc:
            self._mark_auth_expired("square_off_get_spot", exc)
            spot = pos.entry_spot
        except ExecutionError:
            spot = pos.entry_spot
        # D2: must be tz-aware IST, matching pos.entry_time's timezone-awareness
        # in production — a naive datetime here would raise on the holding-time
        # subtraction in `_journal_trade` once entry_time is IST-aware.
        ok = await self._do_exit(now_ist(), spot, premium, reason)
        if ok:
            self._fsm.transition(State.DONE_FOR_DAY, "squared_off")
        return ok

    async def end_of_day(self) -> bool:
        """Terminate the session safely: flatten if needed, else mark done."""
        if self._trade.position is not None:
            return await self.square_off("eod_hard_exit")
        if not self._fsm.is_terminal():
            self._fsm.transition(State.DONE_FOR_DAY, "eod_no_position")
        return True

    def has_open_position(self) -> bool:
        return self._trade.position is not None

    @property
    def position(self) -> Optional[Position]:
        """Read-only accessor for the current position, if any.

        Added for the Tick Engine (continuous tick-driven MTM/risk
        monitoring) so it doesn't need to reach into TradeManager's private
        state. Read-only — nothing about position management changes.
        """
        return self._trade.position

    def _journal_trade(self, pos: Position, exit_time: datetime, exit_spot: float,
                       exit_premium: float, reason: str) -> None:
        holding_min = (exit_time - pos.entry_time).total_seconds() / 60.0
        record = TradeRecord(
            date=pos.entry_time.date().isoformat(),
            direction=pos.direction.value,
            orb_high=pos.orb.high,
            orb_low=pos.orb.low,
            entry_time=pos.entry_time.isoformat(),
            entry_spot=pos.entry_spot,
            atm_strike=pos.contract.strike,
            entry_premium=pos.entry_price,
            exit_time=exit_time.isoformat(),
            exit_premium=exit_premium,
            exit_spot=exit_spot,
            exit_reason=reason,
            holding_time_min=round(holding_min, 1),
            max_profit_seen=round(pos.max_profit_seen, 2),
            max_loss_seen=round(pos.max_loss_seen, 2),
            max_favourable_excursion=round(pos.max_favourable_excursion, 2),
            max_adverse_excursion=round(pos.max_adverse_excursion, 2),
            total_candles_held=pos.candles_held,
            daily_result=round(pos.mtm(exit_premium), 2),
            thesis=pos.thesis.narrative if pos.thesis else "",
        )
        self._journal.record(record)
        log_event(self._log, "trade_journaled", **{"pnl": record.daily_result,
                                                    "reason": reason})

    # ------------------------------------------------------------------ #
    # VWAP Audit — production auditability, never affects trading logic
    # ------------------------------------------------------------------ #
    def _emit_vwap_audit(self, candle: Candle) -> None:
        """Log and publish VWAP quality metadata for the completed cycle.

        Read-only: it observes VWAP quality, FSM state, trade state, and the
        decision already made this cycle. It changes nothing.
        """
        record = VwapAuditRecord(
            timestamp=candle.timestamp,
            strategy_state=self._fsm.state.value,
            trade_state="IN_POSITION" if self._trade.position else "FLAT",
            decision=self._cycle_decision,
            quality=self._signal.vwap_quality(),
        )
        log_event(self._log, "vwap_audit", **record.to_log())
        self._status.market_data_health = record.to_dashboard()
        self._status.push_audit(record.to_dashboard())

    # ------------------------------------------------------------------ #
    # Status updates for the dashboard
    # ------------------------------------------------------------------ #
    def _update_status_market(self, candle: Candle) -> None:
        s = self._status
        s.spot = candle.close
        s.vwap = round(self._signal.vwap, 2)
        s.vwap_real = self._signal.vwap_is_real
        orb = self._signal.opening_range
        if orb:
            s.orb_high, s.orb_low = orb.high, orb.low
        s.touch()

    def _update_status_position(self, entry: Optional[float],
                                current: Optional[float],
                                reason: str = "", decision: str = "") -> None:
        s = self._status
        pos = self._trade.position
        if pos:
            s.direction = pos.direction.value
            s.position_symbol = pos.contract.symbol
            s.entry_premium = entry
            s.current_premium = current
            if entry is not None and current is not None:
                s.mtm = round((entry - current) * pos.quantity, 2)
        if reason:
            s.last_reason = reason
        if decision:
            s.last_decision = decision
        s.touch()

    @property
    def state(self) -> State:
        return self._fsm.state

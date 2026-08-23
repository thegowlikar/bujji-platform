"""M2 ACCEPTANCE: durable, full-fidelity tick evidence and deterministic replay.

The milestone criterion, from ARCHITECTURE.md:

    A recorded session replays to an identical decision sequence; a corrupted
    journal refuses rather than degrades.

WHY A DEDICATED JOURNAL AND NOT LAYER 0. `market_reality.store` is reachable
and already reserves `KIND_MARKET_TICK`, and nothing has ever produced one. It
is the wrong home for a reason its own docstring gives: an identical
observation id "means an identical fact", and re-capturing one is a no-op where
"nothing is written, and nothing is lost". Correct for observations; fatal for
ticks. Lite-mode payloads carry only symbol/ltp/type with no exchange
timestamp, so a quiet symbol emits byte-identical ticks -- and collapsing those
destroys the rate, gaps and order this journal exists to record.
"""
from __future__ import annotations

import ast
import io
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from bujji.tick_journal import (
    ALL_PURPOSES, JournalIntegrityError, JournalManifest, JournalNotFaithfulError,
    MANIFEST_VERSION, PURPOSE_DECISION_EQUIVALENCE, PURPOSE_RESEARCH,
    PURPOSE_SAFETY_CERTIFICATION, PURPOSE_SESSION_SUCCESS_EVIDENCE,
    STATE_FAITHFUL, STATE_INCOMPLETE, TickJournal, open_journal, payloads,
    read_journal, recover_unsealed,
)


def _manifest_kwargs(m):
    """Constructor kwargs from a manifest, minus the DERIVED fields.

    `as_dict()` publishes `complete` and the crash-loss window for readers;
    none of them is a constructor argument."""
    data = m.as_dict()
    for derived in ("complete", "max_crash_loss_records", "max_crash_loss_seconds"):
        data.pop(derived, None)
    data["notes"] = tuple(data.get("notes") or ())
    return data

REPO_ROOT = Path(__file__).resolve().parent.parent

FULL_MODE_TICK = {
    "symbol": "NSE:NIFTY2682524200CE", "ltp": 101.5, "type": "sf",
    "bid_price": 101.0, "ask_price": 102.0, "bid_size": 900, "ask_size": 750,
    "oi": 344630, "prev_oi": 321035, "vol_traded_today": 137475,
    "exch_feed_time": 1787047800, "last_traded_qty": 75, "avg_trade_price": 100.9,
    "low_price": 88.0, "high_price": 121.0, "open_price": 95.0, "prev_close_price": 99.0,
}
ACK = {"type": "cn", "message": "subscribed"}


def _seal(journal, tmp, session_id="ACC", universe_symbols=248):
    stats = journal.close()
    manifest_path = journal.path.with_suffix(".manifest.json")
    JournalManifest(
        version=MANIFEST_VERSION, session_id=session_id, as_of_date="2026-08-24",
        journal_filename=journal.path.name, started_at="T0", ended_at="T1",
        offered=stats.offered, written=stats.written, dropped=stats.dropped,
        max_queue_depth=stats.max_queue_depth, bytes_written=stats.bytes_written,
        content_sha256=journal.content_sha256(),
        fsync_every_records=journal._fsync_every_records,
        fsync_every_seconds=journal._fsync_every_seconds,
        max_queue=journal._queue.maxsize, universe_symbols=universe_symbols,
    ).write(manifest_path)
    return manifest_path, stats


@pytest.fixture
def journal(tmp_path):
    j = TickJournal(tmp_path / "ticks.jsonl", session_id="ACC")
    yield j
    try:
        j.close()
    except Exception:
        pass


# ------------------------------------------------ 1. full fidelity, verbatim
def test_every_field_survives_the_callback_boundary(tmp_path):
    """`on_message` keeps symbol and ltp and discards the other ~21 full-mode
    fields. The journal is written BEFORE that, so all of them survive."""
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer(dict(FULL_MODE_TICK))
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    result = read_journal(j.path, mp)
    assert result.records[0].payload == FULL_MODE_TICK
    assert set(result.records[0].payload) == set(FULL_MODE_TICK)


def test_byte_identical_ticks_are_both_kept(tmp_path):
    """THE REASON THIS IS NOT LAYER 0. Content-addressed storage collapses
    these to one fact and reports nothing lost. Two arrivals are two arrivals."""
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    for _ in range(3):
        j.offer(dict(FULL_MODE_TICK))
    time.sleep(0.3)
    mp, stats = _seal(j, tmp_path)
    assert stats.written == 3
    assert len(read_journal(j.path, mp).records) == 3


def test_acknowledgements_are_recorded_too(tmp_path):
    """An ack is not a price, but it IS evidence the subscription was accepted
    -- the question nobody could answer after 2026-08-21."""
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer(dict(ACK))
    j.offer(dict(FULL_MODE_TICK))
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    kinds = [p.get("type") for p in payloads(read_journal(j.path, mp))]
    assert kinds == ["cn", "sf"]


def test_arrival_order_is_preserved_and_sequenced(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    for i in range(25):
        j.offer({"symbol": "S", "ltp": float(i), "type": "sf"})
    time.sleep(0.4)
    mp, _ = _seal(j, tmp_path)
    result = read_journal(j.path, mp)
    assert [r.seq for r in result.records] == list(range(1, 26))
    assert [p["ltp"] for p in payloads(result)] == [float(i) for i in range(25)]


# --------------------------------------- 2. replay reproduces the consumer
def _derive(stream):
    """The derivation `on_message` performs, applied to a payload stream.

    Mirrors fyers_ws deliberately -- `test_the_derivation_mirrors_on_message`
    below pins that it still does, so this cannot drift into testing a
    consumer production does not have."""
    ltp, seen = {}, []
    for msg in stream:
        symbol, value = msg.get("symbol"), msg.get("ltp")
        if symbol is None or value is None:
            continue
        ltp[symbol] = float(value)
        seen.append(symbol)
    return ltp, seen


def test_a_recorded_session_replays_to_an_identical_derived_state(tmp_path):
    """THE CRITERION. Same journal, same consumer, same result -- with no
    clock, network or randomness in the path."""
    live = [
        {"symbol": "A", "ltp": 1.0, "type": "sf"},
        dict(ACK),
        {"symbol": "B", "ltp": 2.0, "type": "sf"},
        {"symbol": "A", "ltp": 3.0, "type": "sf"},
        {"symbol": "B", "ltp": 2.0, "type": "sf"},      # identical to an earlier B
    ]
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    for msg in live:
        j.offer(dict(msg))
    time.sleep(0.4)
    mp, _ = _seal(j, tmp_path)

    from_live = _derive(live)
    from_journal = _derive(payloads(read_journal(j.path, mp)))
    assert from_journal == from_live
    assert from_journal[0] == {"A": 3.0, "B": 2.0}


def test_replay_is_deterministic_across_reads(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    for i in range(10):
        j.offer({"symbol": f"S{i%3}", "ltp": float(i), "type": "sf"})
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    first = [r.payload for r in read_journal(j.path, mp).records]
    second = [r.payload for r in read_journal(j.path, mp).records]
    assert first == second


def test_the_derivation_mirrors_on_message():
    """Keeps `_derive` honest: if on_message's extraction changes, this fails
    rather than the acceptance test quietly proving the wrong thing."""
    src = io.open(REPO_ROOT / "bujji" / "broker" / "fyers_ws.py", encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "on_message")
    body = ast.unparse(fn)
    assert "msg.get('symbol')" in body and "msg.get('ltp')" in body
    # Both guards must still exist; they are now separate statements so that
    # an acknowledgement (symbol, no ltp) is recorded as coverage instead of
    # being dropped. `_derive` mirrors the same extraction either way.
    assert "if symbol is None" in body, "the symbol guard disappeared"
    assert "if ltp is None" in body, "the ltp guard disappeared"


# ------------------------------------------- 3. corruption REFUSES
def test_a_tampered_journal_refuses(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer(dict(FULL_MODE_TICK))
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    j.path.write_text(j.path.read_text().replace("101.5", "999.9"), encoding="utf-8")
    with pytest.raises(JournalIntegrityError, match="content hash"):
        read_journal(j.path, mp)


def test_a_truncated_journal_refuses(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    for i in range(5):
        j.offer({"symbol": "S", "ltp": float(i), "type": "sf"})
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    lines = j.path.read_text(encoding="utf-8").splitlines(keepends=True)
    j.path.write_text("".join(lines[:3]), encoding="utf-8")
    with pytest.raises(JournalIntegrityError):
        read_journal(j.path, mp)


def test_an_unparseable_record_refuses_rather_than_being_skipped(tmp_path):
    """Skipping it would present partial evidence as complete."""
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer({"symbol": "S", "ltp": 1.0, "type": "sf"})
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    with open(j.path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    # the hash check fires first, which is itself correct -- so verify the
    # parser refuses when the manifest is regenerated over the damaged file
    import hashlib
    m = JournalManifest.read(mp)
    data = _manifest_kwargs(m)
    data["content_sha256"] = hashlib.sha256(j.path.read_bytes()).hexdigest()
    data["written"] = m.written + 1
    JournalManifest(**data).write(mp)
    with pytest.raises(JournalIntegrityError, match="not valid JSON"):
        read_journal(j.path, mp)


def test_a_manifest_that_disagrees_about_the_count_refuses(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer({"symbol": "S", "ltp": 1.0, "type": "sf"})
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    data = _manifest_kwargs(JournalManifest.read(mp)); data["written"] = 99
    JournalManifest(**data).write(mp)
    with pytest.raises(JournalIntegrityError, match="manifest claims"):
        read_journal(j.path, mp)


def test_a_manifest_with_unknown_fields_refuses(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer({"symbol": "S", "ltp": 1.0, "type": "sf"})
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    raw = json.loads(mp.read_text(encoding="utf-8"))
    raw["invented_field"] = True
    mp.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields"):
        read_journal(j.path, mp)


# ------------------------------- 4. loss is recorded, never hidden
def test_a_full_queue_drops_and_counts_rather_than_blocking(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC", max_queue=2,
                    fsync_every_records=10_000, fsync_every_seconds=10_000)
    j._stop.set(); j._thread.join(timeout=2)     # stall the writer deliberately
    for i in range(50):
        j.offer({"symbol": "S", "ltp": float(i), "type": "sf"})
    stats = j.stats()
    assert stats.dropped > 0, "a full queue must drop, not grow without bound"
    assert stats.offered == 50
    assert stats.as_dict()["accounted"] is False or stats.offered == stats.written + stats.dropped


def test_an_incomplete_journal_is_replayable_but_not_faithful(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer({"symbol": "S", "ltp": 1.0, "type": "sf"})
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    data = _manifest_kwargs(JournalManifest.read(mp))
    data["dropped"] = 7; data["offered"] = data["written"] + 7
    JournalManifest(**data).write(mp)
    result = read_journal(j.path, mp)
    assert result.faithful is False
    assert any("dropped" in r for r in result.reasons)
    assert len(result.records) == 1, "an honestly short journal is still readable"


def test_offer_never_raises_on_an_unserialisable_payload(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")

    class _Hostile:
        def __repr__(self):
            raise RuntimeError("nope")

    j.offer({"symbol": "S", "bad": _Hostile()})     # must not raise
    time.sleep(0.2)
    j.close()


# ------------------------------------------------ 5. the wiring
FEED_SRC = io.open(REPO_ROOT / "bujji" / "broker" / "fyers_ws.py", encoding="utf-8").read()
RUNNER_SRC = io.open(REPO_ROOT / "bujji_options_os_runner.py", encoding="utf-8").read()


def test_the_journal_records_before_any_field_is_read():
    """ORDERING, not presence. A journal after the extraction would faithfully
    record the loss instead of the arrival."""
    fn = next(n for n in ast.walk(ast.parse(FEED_SRC))
              if isinstance(n, ast.FunctionDef) and n.name == "on_message")
    body = ast.unparse(fn)
    assert "self._journal.offer(msg)" in body
    assert body.index("self._journal.offer(msg)") < body.index("msg.get('symbol')"), \
        "the journal is written after the payload has already been picked apart"


def test_the_journal_records_before_the_ack_early_return():
    fn = next(n for n in ast.walk(ast.parse(FEED_SRC))
              if isinstance(n, ast.FunctionDef) and n.name == "on_message")
    # STRUCTURAL, NOT TEXTUAL. The invariant is that the journal offer
    # precedes EVERY early return -- not that the guard is spelled one
    # particular way. Asserting the literal made a correct refactor (splitting
    # the symbol and ltp guards so an acknowledgement is recorded as coverage
    # rather than discarded) look like a violation, while a genuine reordering
    # spelled differently would have slipped past. This checks the property.
    offer_line = next(
        (n.lineno for n in ast.walk(fn)
         if isinstance(n, ast.Call)
         and getattr(n.func, "attr", None) == "offer"
         and "journal" in ast.unparse(n.func)), None)
    assert offer_line is not None, "on_message no longer offers to the journal"
    returns = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert returns, "on_message has no early return to protect against"
    assert offer_line < min(returns), (
        f"a return at line {min(returns)} precedes the journal offer at "
        f"{offer_line} -- a callback could be discarded unrecorded")
    # And nothing may read a field before the offer either.
    first_get = next(
        (n.lineno for n in ast.walk(fn)
         if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "get"
         and ast.unparse(n.func).startswith("msg.")), None)
    if first_get is not None:
        assert offer_line < first_get, (
            "a field was read from the payload before it was journaled")


def test_the_runner_opens_a_journal_and_hands_it_to_the_feed():
    """BUILT-NOT-WIRED. A journal nothing passes records nothing."""
    assert "TickJournal(" in RUNNER_SRC
    assert "journal=self._tick_journal" in RUNNER_SRC


def test_the_journal_is_sealed_on_every_exit_path():
    """`_shutdown` runs from run()'s finally, so a crashed or refusing session
    still leaves a sealed journal and a manifest."""
    fn = next(n for n in ast.walk(ast.parse(RUNNER_SRC))
              if isinstance(n, ast.FunctionDef) and n.name == "_shutdown")
    assert "_seal_tick_journal()" in ast.unparse(fn)
    run_fn = next(n for n in ast.walk(ast.parse(RUNNER_SRC))
                  if isinstance(n, ast.FunctionDef) and n.name == "run")
    assert any(isinstance(h, ast.Try) and h.finalbody for h in ast.walk(run_fn)), \
        "run() no longer has a finally, so shutdown is not guaranteed"


# ============================================================ CRASH RECOVERY
#
# `_shutdown()` runs from run()'s `finally`, which covers ORDERLY termination
# only -- an exception, a SIGTERM, a refusal to trade. It does NOT cover
# SIGKILL, power loss, or the container being torn out, because no handler
# runs. Nor does it cover the CRASH TAIL: records written since the last fsync
# that the OS had not yet committed.
#
# So a journal found WITHOUT a manifest was interrupted, and is INCOMPLETE by
# DEFINITION rather than by inspection -- no examination of the file can
# establish that what survived is all of it.


def _kill_without_sealing(tmp_path, n=6):
    """A journal whose process died before `close()` could seal it."""
    j = TickJournal(tmp_path / "crash.jsonl", session_id="CRASH")
    for i in range(n):
        j.offer({"symbol": "S", "ltp": float(i), "type": "sf"})
    time.sleep(0.4)
    j._stop.set(); j._thread.join(timeout=3)
    j._file.flush(); os.fsync(j._file.fileno()); j._file.close()   # no manifest
    return j.path


def test_an_unsealed_journal_is_INCOMPLETE_not_silently_complete(tmp_path):
    path = _kill_without_sealing(tmp_path)
    assert not path.with_suffix(".manifest.json").exists()
    result = recover_unsealed(path)
    assert result.state == STATE_INCOMPLETE
    assert result.faithful is False
    assert result.sealed is False
    assert len(result.records) == 6, "surviving records are real and remain readable"
    assert any("no manifest" in r for r in result.reasons)


def test_an_unsealed_journal_cannot_be_read_by_the_sealed_reader(tmp_path):
    """`read_journal` must not quietly invent a verdict for a file that never
    stated one."""
    path = _kill_without_sealing(tmp_path)
    with pytest.raises(JournalIntegrityError, match="never sealed"):
        read_journal(path, path.with_suffix(".manifest.json"))


def test_a_partial_final_record_is_discarded_and_reported(tmp_path):
    """The signature of a kill mid-write. Tolerated ONLY here, where a crash is
    already established -- and never silently."""
    path = _kill_without_sealing(tmp_path, n=4)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"v":"tick-journal/1","seq":5,"recv_epoch":1.0,"recv_mon')  # torn
    result = recover_unsealed(path)
    assert result.discarded_partial_tail is True
    assert len(result.records) == 4
    assert any("partial final record" in r for r in result.reasons)
    assert result.state == STATE_INCOMPLETE


def test_a_malformed_record_that_is_not_the_tail_still_refuses(tmp_path):
    """Only the FINAL record may be torn. Damage anywhere else is corruption,
    and skipping it would present partial evidence as complete."""
    path = _kill_without_sealing(tmp_path, n=4)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[1] = "{not json\n"
    path.write_text("".join(lines), encoding="utf-8")
    with pytest.raises(JournalIntegrityError, match="not valid JSON"):
        recover_unsealed(path)


def test_a_sealed_prior_journal_is_unaffected_by_an_incomplete_current_one(tmp_path):
    """Two journals side by side: yesterday's sealed, today's interrupted.
    Neither contaminates the other, and each reports its own state."""
    prior = TickJournal(tmp_path / "prior.jsonl", session_id="PRIOR")
    prior.offer(dict(FULL_MODE_TICK))
    time.sleep(0.3)
    prior_mp, _ = _seal(prior, tmp_path, session_id="PRIOR")

    current = _kill_without_sealing(tmp_path, n=3)

    good = open_journal(prior.path, prior_mp)
    bad = open_journal(current)
    assert good.state == STATE_FAITHFUL and good.sealed is True
    assert bad.state == STATE_INCOMPLETE and bad.sealed is False
    assert good.manifest.session_id == "PRIOR"
    assert bad.manifest is None
    # and the sealed one still serves a strict purpose
    good.require(PURPOSE_DECISION_EQUIVALENCE)


def test_open_journal_routes_on_the_manifest_never_on_a_guess(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer(dict(FULL_MODE_TICK))
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    assert open_journal(j.path).sealed is True
    mp.unlink()
    assert open_journal(j.path).sealed is False


# ============================================================ PURPOSE GATING
@pytest.mark.parametrize("purpose", [
    PURPOSE_DECISION_EQUIVALENCE, PURPOSE_SAFETY_CERTIFICATION,
    PURPOSE_SESSION_SUCCESS_EVIDENCE,
])
def test_an_incomplete_journal_is_refused_for_every_strict_purpose(tmp_path, purpose):
    path = _kill_without_sealing(tmp_path)
    with pytest.raises(JournalNotFaithfulError, match=purpose):
        recover_unsealed(path, purpose=purpose)


def test_an_incomplete_journal_remains_available_for_research(tmp_path):
    """Recoverable records are still evidence. Refusing them entirely would
    destroy the only account of an interrupted session."""
    path = _kill_without_sealing(tmp_path)
    result = recover_unsealed(path, purpose=PURPOSE_RESEARCH)
    assert len(result.records) == 6


def test_a_faithful_journal_serves_every_purpose(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer(dict(FULL_MODE_TICK))
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    for purpose in sorted(ALL_PURPOSES):
        assert read_journal(j.path, mp, purpose=purpose).faithful is True


def test_a_dropped_record_makes_the_journal_unfit_for_strict_purposes(tmp_path):
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC")
    j.offer(dict(FULL_MODE_TICK))
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    data = _manifest_kwargs(JournalManifest.read(mp))
    data["dropped"] = 3; data["offered"] = data["written"] + 3
    JournalManifest(**data).write(mp)
    with pytest.raises(JournalNotFaithfulError):
        read_journal(j.path, mp, purpose=PURPOSE_SAFETY_CERTIFICATION)


# ==================================================== CRASH-LOSS WINDOW
def test_the_manifest_states_the_acknowledged_crash_loss_window(tmp_path):
    """A reader must be able to say what a SIGKILL could have cost without
    reading the writer's source."""
    j = TickJournal(tmp_path / "t.jsonl", session_id="ACC",
                    fsync_every_records=250, fsync_every_seconds=1.5)
    j.offer(dict(FULL_MODE_TICK))
    time.sleep(0.3)
    mp, _ = _seal(j, tmp_path)
    m = JournalManifest.read(mp)
    assert m.max_crash_loss_records == 250
    assert m.max_crash_loss_seconds == 1.5
    assert m.as_dict()["max_crash_loss_records"] == 250
    assert read_journal(j.path, mp).as_dict()["max_crash_loss_seconds"] == 1.5


def test_the_runner_records_the_loss_window_in_the_session_summary():
    assert "max_crash_loss_records" in RUNNER_SRC
    assert "max_crash_loss_seconds" in RUNNER_SRC


# ==================================================== SESSION VERDICT
def test_an_open_position_without_a_sealed_journal_is_unsafe():
    from bujji.production_runtime.session_safety_verdict import evaluate_session_safety
    v = evaluate_session_safety({
        "entry_filled": True, "final_positions_status": "FLAT",
        "tick_journal": {"sealed": False, "faithful": False, "error": "SIGKILL"}})
    assert v.safe is False
    assert any("never sealed" in r for r in v.reasons)


def test_an_open_position_with_an_unfaithful_journal_is_unsafe():
    from bujji.production_runtime.session_safety_verdict import evaluate_session_safety
    v = evaluate_session_safety({
        "entry_filled": True, "final_positions_status": "FLAT",
        "tick_journal": {"sealed": True, "faithful": False,
                         "dropped": 12, "offered": 100, "written": 88}})
    assert v.safe is False
    assert any("not faithful" in r for r in v.reasons)


def test_an_open_position_with_no_journal_at_all_is_unsafe():
    from bujji.production_runtime.session_safety_verdict import evaluate_session_safety
    v = evaluate_session_safety({"entry_filled": True, "final_positions_status": "FLAT"})
    assert v.safe is False
    assert any("no tick journal" in r for r in v.reasons)


def test_an_open_position_with_a_sealed_faithful_journal_is_safe():
    from bujji.production_runtime.session_safety_verdict import evaluate_session_safety
    v = evaluate_session_safety({
        "entry_filled": True, "final_positions_status": "FLAT",
        "tick_journal": {"sealed": True, "faithful": True,
                         "dropped": 0, "offered": 10, "written": 10}})
    assert v.safe is True, v.reasons


def test_a_no_trade_session_is_not_made_unsafe_by_an_unsealed_journal():
    """The rule is about explaining an OPEN POSITION. A session that opened
    nothing has no exposure to account for, and firing here would wake an
    operator for a crash that risked nothing."""
    from bujji.production_runtime.session_safety_verdict import evaluate_session_safety
    v = evaluate_session_safety({"tick_journal": {"sealed": False, "faithful": False}})
    assert v.safe is True, v.reasons

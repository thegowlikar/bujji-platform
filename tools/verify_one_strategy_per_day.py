"""Re-verify, by EXECUTION, that one strategy per day survives a restart.

WHY THIS IS A COMMITTED TOOL AND NOT A TEST. The unit tests use fixtures. This
runs the same decisions against the REAL production journal and the REAL
broker factory, so it catches the class of error the fixtures cannot: a config
pointing at a different journal file, a broker that behaves differently when
actually constructed, a gate that moved off the entry path.

It found exactly that kind of error once already. An earlier claim -- "a
restart while holding a position is already blocked by reconciliation" -- was
made from reading `reconcile()` with a hand-supplied open leg. Constructing the
real broker showed its position book is an in-process dict, empty on every
start, so reconciliation reports RECONCILED whether or not a position is open.
The stated defect was broader than reported, and only running it showed that.

READ ONLY. It copies the journal before touching it and asserts the original's
checksum is unchanged. It places no order and calls no broker endpoint.

  /opt/bujji/.venv/bin/python tools/verify_one_strategy_per_day.py
"""
from __future__ import annotations

import argparse
import asyncio
import ast
import hashlib
import inspect
import logging
import pathlib
import shutil
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_JOURNAL = "/opt/bujji/app/data/options_os_trading_journal.db"

_failures = []


def check(label, got, want, note=None):
    passed = got == want
    if not passed:
        _failures.append(label)
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    if not passed or note:
        print(f"         got={got!r} want={want!r}")
    if note:
        print(f"         {note}")


def _digest(path):
    return hashlib.md5(pathlib.Path(path).read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", default=DEFAULT_JOURNAL,
                        help="a REAL production journal to verify against")
    args = parser.parse_args()

    logging.disable(logging.CRITICAL)
    log = logging.getLogger("verify")

    from bujji.broker.factory import _production_paper_broker
    from bujji.journal.position_group_journal import PositionGroupJournal
    from bujji.production_runtime.position_reconciliation import reconcile
    from bujji.production_runtime.prior_fills import (
        FILL_EVENT, group_ids_filled_on, prior_fills_snapshot)
    from bujji.production_runtime.trading_session_governor.entry_control import (
        can_enter_trade)
    from bujji.production_runtime.trading_session_governor.session_trading_state import (
        TradingSessionState)
    from bujji_options_os_runner import OptionsOSRunner

    source = pathlib.Path(args.journal)
    if not source.exists():
        print(f"no journal at {source} -- nothing to verify against")
        return 2
    before = _digest(source)
    work = pathlib.Path(tempfile.mkdtemp()) / "journal.db"
    shutil.copy(source, work)
    journal = PositionGroupJournal(str(work))

    print("\n1. THE BROKER FORGETS ACROSS A RESTART.")
    broker = _production_paper_broker()
    book = asyncio.run(broker.get_open_positions())
    check("a freshly constructed production broker's position book", book, [])
    result = reconcile(set(), book)
    check("so reconciliation cannot see a pre-restart position",
          (result.verdict, result.blocks_new_risk), ("RECONCILED", False),
          "this is why the journal, not the broker, has to answer the question")

    print("\n2. THE IN-MEMORY DISCIPLINE DOES NOT SURVIVE.")

    class _FreshLock:
        def is_locked(self):
            return True

    decision = can_enter_trade(TradingSessionState.STRATEGY_LOCKED, _FreshLock())
    check("can_enter_trade with a freshly rebuilt lock",
          (decision.allowed, decision.reason), (True, "ALLOWED"),
          "nothing durable is consulted; a restart re-arms entry")

    print("\n3. THE JOURNAL REMEMBERS -- DISCOVERED FROM REAL DATA.")
    dates = sorted({
        event.recorded_at.date().isoformat()
        for group_id in journal.read_all_group_ids()
        for event in journal.read_events(group_id)
        if event.event_type == FILL_EVENT and event.recorded_at is not None
    })
    print(f"         trading days with fills in this journal: {dates or 'none'}")
    if dates:
        traded = dates[-1]
        check(f"fills are found for {traded}",
              bool(group_ids_filled_on(journal, traded)), True)
    else:
        traded = None
        print("         [SKIP] this journal records no fills; the refusal path "
              "below is exercised with an injected fill instead")
    check("a day with no fills reports none",
          group_ids_filled_on(journal, "1970-01-01"), [])

    print("\n4. THE GATE REFUSES -- END TO END.")

    def gate(prior, unreadable):
        blocked = []

        class _Stub:
            _as_of_date = traded or "1970-01-01"
            _prior_fills_today = prior
            _prior_fills_unreadable = unreadable
            _reconciliation_blocks_entry = False
            _last_reconciliation = object()
            _reconciliation_ran = True
            _data_quality = None
            _intelligence_origin = None
            _governor_result_summary = {}
            _logger = log

            def _ensure_universe_subscribed(self):
                pass

            def _record_universe_coverage(self):
                pass

            def _block_entry(self, reason):
                blocked.append(reason)

        return OptionsOSRunner._data_quality_permits_entry(_Stub()), blocked

    filled = prior_fills_snapshot(journal, traded, log) if traded else (["PG-INJECTED"], False)
    check("a restart on a day that already filled is refused",
          gate(*filled), (False, ["STRATEGY_ALREADY_DEPLOYED_TODAY"]))
    check("a restart on a day that has not is permitted", gate([], False), (True, []),
          "a guard that always refuses is not a guard")

    print("\n5. IT FAILS CLOSED.")

    class _Unreadable:
        def read_all_group_ids(self):
            raise OSError("database is locked")

    fills, unreadable = prior_fills_snapshot(_Unreadable(), "2026-01-01", log)
    check("an unreadable journal is not an empty one", (bool(fills), unreadable), (True, True))
    check("and it refuses, saying it could not look",
          gate(fills, unreadable), (False, ["PRIOR_FILLS_UNREADABLE"]))

    print("\n6. THE GATE IS ON THE PRODUCTION PATH.")
    tree = ast.parse(inspect.getsource(OptionsOSRunner))
    entry = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_attempt_entry")
    body = [st for st in entry.body if not (isinstance(st, ast.Expr)
                                            and isinstance(st.value, ast.Constant))]
    check("the gate is the first statement of _attempt_entry",
          "_data_quality_permits_entry" in ast.unparse(body[0]), True,
          "_attempt_entry is the choke point both entry modes share")

    run_fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "run")
    handlers = [h for node in ast.walk(run_fn) if isinstance(node, ast.Try)
                for h in node.handlers]
    entered_on_failure = [
        risky for h in handlers
        for risky in ("_attempt_entry", "_entry_window", "_continuous_session")
        if risky in ast.unparse(ast.Module(body=h.body, type_ignores=[]))
    ]
    check("a startup failure cannot still reach entry", entered_on_failure, [])

    print("\n7. THE REAL JOURNAL WAS NOT TOUCHED.")
    check("checksum unchanged", _digest(source), before)

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} claim(s) -- {', '.join(_failures)}")
        return 1
    print("ALL LOAD-BEARING CLAIMS VERIFIED against a real journal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

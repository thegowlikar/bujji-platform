"""Three false-certainty defects I introduced, and the guards against them.

A 37-agent adversarially-verified audit found these in work I had committed
over the preceding two days while describing it as runtime-proven. Each one
manufactured certainty:

  A. An orphaned partial-entry leg was registered under a position_group_id
     the management pass could never read, while the runner logged CRITICAL
     "position is LIVE ... Management cycles will revalue and exit it".
  B. Startup recovery resolved crash-left orders against a fresh in-memory
     PaperBroker, durably recording RECOVERY_CONFIRMED_NEVER_RECEIVED -- an
     authoritative claim about the exchange derived from this process's
     amnesia.
  C. Evidence was claimed to be persisted "every cycle"; its only call site
     sits behind a stability gate that passes ~8% of cycles.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RUNNER = (REPO_ROOT / "bujji_options_os_runner.py").read_text()


class TestOrphanHasAManagementIdentity:
    def test_the_orphan_sets_the_governor_position_group_id(self):
        """The management pass reads self._governor._position_group_id. On an
        orphan (filled=False) it was never set, so every pass read
        valuations.get(None) -> None -> 'no valuation available for None'."""
        i = RUNNER.index("def _register_orphaned_legs")
        block = RUNNER[i:i + 2600]
        assert "self._governor._position_group_id = pg_id" in block

    def test_continuous_mode_manages_an_orphan(self):
        """_attempt_entry returns False on an orphan, so `if entered:` never
        started management -- the loop moved on while a naked leg sat live."""
        assert 'if entered or getattr(self, "_orphan_position_live", False):' in RUNNER

    def test_the_flag_starts_false(self):
        assert "self._orphan_position_live = False" in RUNNER

    def test_the_identity_the_registry_gets_is_the_one_the_governor_gets(self):
        """A mismatch here reintroduces the defect in a subtler form."""
        i = RUNNER.index("def _register_orphaned_legs")
        block = RUNNER[i:i + 2600]
        assert 'pg_id = f"{cycle_result.proposal.assessment_id}-ORPHAN"' in block
        assert "self._registry.register_entry(\n                pg_id," in block
        assert "self._governor._position_group_id = pg_id" in block


class TestRecoveryNeverManufacturesNeverReceived:
    def test_paper_broker_declares_that_it_forgets(self):
        from bujji.broker.paper import PaperBroker
        assert PaperBroker.order_book_survives_restart is False

    def test_fyers_broker_declares_that_the_exchange_remembers(self):
        from bujji.broker.fyers import FyersBroker
        assert FyersBroker.order_book_survives_restart is True

    def test_recovery_checks_the_marker_before_resolving(self):
        src = (REPO_ROOT / "bujji" / "production_runtime"
               / "execution_journal_bridge.py").read_text()
        i = src.index("def recover_unresolved_at_startup")
        block = src[i:i + 3000]
        assert 'getattr(broker, "order_book_survives_restart", False)' in block

    def test_the_default_is_forgetful(self):
        """A broker that does not declare the capability must be assumed
        unable to vouch -- fail closed, not open."""
        src = (REPO_ROOT / "bujji" / "production_runtime"
               / "execution_journal_bridge.py").read_text()
        assert 'getattr(broker, "order_book_survives_restart", False)' in src


class TestEvidenceIsPersistedAtThePoll:
    def test_both_poll_sites_persist(self):
        """The rolling window feeds PSI/MSSI citations. Persisting only on
        stable cycles left ~92% of the window's observations unwritten, so
        cited ids from those cycles dangled."""
        assert RUNNER.count("self._persist_polled_evidence(_fresh)") == 2

    def test_the_helper_exists_and_never_raises(self):
        assert "def _persist_polled_evidence" in RUNNER
        i = RUNNER.index("def _persist_polled_evidence")
        block = RUNNER[i:i + 1800]
        assert "except Exception" in block
        assert "return 0" in block

    def test_the_stale_every_cycle_claim_is_gone(self):
        """The docstring asserted a coverage property its call site did not
        deliver, and that assertion was the justification in the commit."""
        i = RUNNER.index("def _persist_cycle_evidence")
        block = RUNNER[i:i + 2200]
        assert "EVERY CYCLE, not only cycles that produced a thesis" not in block
        assert "~8%" in block, "the real coverage must be stated, not implied"

    def test_persisting_is_idempotent_across_repolls(self):
        """The same fact polled on two cycles is one fact; append_evidence
        dedupes by content hash, so per-poll persistence is not unbounded."""
        import inspect

        from bujji.shadow_observatory import evidence_store
        src = inspect.getsource(evidence_store.append_evidence)
        assert "known = set(load_evidence_index(path))" in src

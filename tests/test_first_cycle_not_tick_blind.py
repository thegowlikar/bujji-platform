"""The first market snapshot must be taken with the tick path already live.

WHAT WAS WRONG. The thesis derivation ran inside the regime block, before the
tick source was constructed and long before the universe was subscribed --
subscription happened in the entry gate, which runs after the first derivation
has already produced the regime that decides whether to enter at all. So the
first MarketSnapshot of every session was built with no tick path in
existence. That was structural, not a race: no amount of waiting helps when
nothing has subscribed.

WHAT MUST STAY TRUE. The tick block is documented as constructed "AFTER the
regime block, on purpose", and it is -- the dependency is on
`self._intelligence_broker`, which the regime block builds. Only the
DERIVATION moved. A future edit that moves the broker construction, or that
moves the derivation back, must fail here rather than silently restoring a
tick-blind first cycle that nothing would notice.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

RUNNER = pathlib.Path(__file__).resolve().parent.parent / "bujji_options_os_runner.py"
SRC = RUNNER.read_text()
TREE = ast.parse(SRC)


def _fn(name):
    return next(n for n in ast.walk(TREE)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name)


def _call_line(fn, attr):
    """Line of the first call to self.<attr> inside fn."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == attr:
            return node.lineno
    return None


def _assign_line(fn, attr):
    """Line of the first assignment to self.<attr> inside fn."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == attr:
                    return node.lineno
    return None


class TestTheStartupOrderingIsWhatItClaims:
    def test_the_universe_is_subscribed_before_the_first_derivation(self):
        """THE POINT OF THE REORDER. If subscription does not precede the
        derivation, the first snapshot cannot see a single tick."""
        startup = _fn("_startup")
        subscribe = _call_line(startup, "_pre_subscribe_for_first_derivation")
        derive = _call_line(startup, "_build_market_thesis_regime_provider")
        assert subscribe is not None, "startup no longer pre-subscribes the universe"
        assert derive is not None, "startup no longer derives the regime"
        assert subscribe < derive, (
            f"the universe is subscribed at line {subscribe} but the first "
            f"derivation runs at {derive}; the first snapshot is tick-blind")

    def test_the_tick_source_exists_before_the_first_derivation(self):
        """Subscribing needs a feed. If the tick block still ran after the
        derivation, pre-subscribing would find `_tick_feed` None and record
        NOT_APPLICABLE -- passing the test above while changing nothing."""
        startup = _fn("_startup")
        feed = _assign_line(startup, "_tick_feed")
        derive = _call_line(startup, "_build_market_thesis_regime_provider")
        assert feed is not None, "startup no longer constructs a tick feed"
        assert feed < derive, (
            f"the tick feed is built at line {feed}, after the derivation at "
            f"{derive}; there is no feed to subscribe")

    def test_the_intelligence_broker_is_still_built_before_the_tick_source(self):
        """THE DEPENDENCY THAT DID NOT MOVE. `type: broker` hands the
        management loop the same execution-neutered broker the regime path
        builds. If the broker construction ever drifts after the tick block,
        the tick source silently loses its data broker."""
        startup = _fn("_startup")
        broker = _assign_line(startup, "_intelligence_broker")
        feed = _assign_line(startup, "_tick_feed")
        assert broker is not None and feed is not None
        assert broker < feed, (
            "the intelligence broker is now built after the tick source, which "
            "depends on it")

    def test_the_derivation_no_longer_runs_inside_the_regime_block(self):
        """The regime block sets a flag; it must not also derive, or the
        derivation happens twice and the first one is tick-blind again."""
        startup = _fn("_startup")
        derivations = [n for n in ast.walk(startup)
                       if isinstance(n, ast.Call)
                       and getattr(n.func, "attr", None)
                       == "_build_market_thesis_regime_provider"]
        assert len(derivations) == 1, (
            f"{len(derivations)} derivation call sites in _startup; there must be "
            f"exactly one, after the universe is subscribed")


class TestTheDeferralCannotLoseTheRegime:
    def test_the_flag_is_initialised_before_every_branch(self):
        """A path that sets neither branch must still reach a defined flag --
        otherwise startup raises UnboundLocalError, which is the shape that
        took the emergency brake down once already."""
        body = ast.unparse(_fn("_startup"))
        init = body.index("derive_thesis_regime = False")
        first_use = body.index("if derive_thesis_regime:")
        assert init < first_use
        assert body.count("derive_thesis_regime = False") == 1

    def test_every_thesis_regime_type_sets_the_flag(self):
        """Both market_thesis and market_thesis_live must defer; if one stops
        setting the flag it silently gets no regime provider at all."""
        body = ast.unparse(_fn("_startup"))
        assert body.count("derive_thesis_regime = True") == 2, (
            "expected exactly two thesis regime types to request derivation")

    def test_the_human_supplied_path_does_not_derive(self):
        """A fixed regime builds no snapshot, so there is nothing to derive
        and nothing to pre-subscribe for."""
        body = ast.unparse(_fn("_startup"))
        idx = body.index("HumanSuppliedRegimeProvider")
        tail = body[idx:idx + 400]
        assert "derive_thesis_regime = True" not in tail


class _Runner:
    """The minimum surface `_pre_subscribe_for_first_derivation` touches."""

    def __init__(self, outcome):
        self._outcome = outcome
        self._universe = None
        self._universe_error = None
        self.calls = 0
        import logging
        self._logger = logging.getLogger("test-presubscribe")

    def _ensure_universe_subscribed(self):
        self.calls += 1
        if self._outcome == "ok":
            self._universe = object()
        elif self._outcome == "error":
            self._universe_error = "NO_SPOT: broker unreachable"
        elif self._outcome == "raise":
            raise RuntimeError("feed exploded")


def _bind(runner):
    import bujji_options_os_runner as R
    return R.OptionsOSRunner._pre_subscribe_for_first_derivation.__get__(runner)


class TestAStartupFailureDoesNotLatch:
    """`_ensure_universe_subscribed` returns early forever once
    `_universe_error` is set. That is right at entry time and wrong at
    startup: a transient failure on a path that previously could not fail at
    all would refuse entry for the whole session."""

    def test_a_failed_pre_subscribe_is_rolled_back_to_unattempted(self):
        r = _Runner("error")
        _bind(r)()
        assert r._universe_error is None, (
            "a startup failure latched; the entry gate will now refuse entry all "
            "session for a question it never got to ask itself")
        assert r._universe is None

    def test_a_raising_pre_subscribe_does_not_propagate(self):
        """Startup must not die because an optimisation failed."""
        r = _Runner("raise")
        _bind(r)()          # must not raise
        assert r._universe_error is None

    def test_a_successful_pre_subscribe_stays_latched(self):
        """The idempotency doing its job: the entry gate's later call must
        become a no-op, not a second subscription."""
        r = _Runner("ok")
        _bind(r)()
        assert r._universe is not None
        assert r._universe_error is None
        assert r.calls == 1

    def test_it_subscribes_exactly_once(self):
        r = _Runner("error")
        _bind(r)()
        assert r.calls == 1, "the startup attempt retried on its own"

"""The entry-point list must name what systemd actually runs.

WHAT WENT WRONG. `tools/reachability.py` declared its list as "the eight entry
points systemd actually runs". It was wrong in BOTH directions:

  * `bujji.app` was annotated as bujji-shadow-decision-campaign.service. That
    service runs scripts/run_phase20_13_live_entrypoint.py. `bujji.app` is the
    DEPRECATED ORB-VWAP bot -- bujji-orb-vwap-legacy.service, disabled, no
    timer, zero journal entries.
  * `run_live_shadow` and `scripts.run_paper_intelligence_campaign` are
    reached by no enabled unit.
  * The module the shadow campaign REALLY runs was absent entirely.

The cost was not cosmetic. `bujji.core.orchestrator` is reachable only from
`bujji.app`, so it sat in ARCHITECTURE.md's "known reachable violations --
these DO define what production trades" table while nothing ran it; and
everything the shadow campaign really reaches was classified unreachable.

These are FIXTURES, not a live systemctl call: the suite must pass on a
developer machine with no systemd. The fixture below is the observed truth,
and re-deriving it is a documented one-liner in the tool's own docstring.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location(
        "reachability_under_test", REPO_ROOT / "tools" / "reachability.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Observed 2026-08-22 on root@139.59.76.137 via:
#   systemctl list-unit-files --type=timer --state=enabled
#   systemctl show -p Unit --value <timer>
#   systemctl cat <service> | grep ExecStart
ENABLED_UNITS_TO_MODULE = {
    "bujji-options-os-trading.timer":       "bujji_options_os_runner",
    "bujji-daily-intelligence.timer":       "run_daily_intelligence_session",
    "bujji-shadow-decision-campaign.timer": "scripts.run_phase20_13_live_entrypoint",
    "bujji-price-levels.timer":             "scripts.refresh_price_levels",
    "bujji-futures-depth-poller.timer":     "scripts.run_futures_depth_poller",
    "bujji-backup.timer":                   "scripts.backup_observation_stores",
    "bujji-token-preflight.timer":          "scripts.preflight_fyers_token",
}

# Enabled units whose ExecStart is OUTSIDE this repository, so they can never
# be modules in this import graph.
ENABLED_UNITS_OUTSIDE_THE_REPO = {
    "bujji-certify-ws-option.timer": "/opt/bujji/certify_ws_option.py",
    "bujji-token-capture.service":   "/opt/bujji/token_capture.py",
    "bujji-token-inbox.path":        "/opt/bujji/promote_from_inbox.py",
}

NOT_ENABLED = {
    "bujji.app": "bujji-orb-vwap-legacy.service -- disabled, no timer, never ran",
    "run_live_shadow": "no unit references it",
    "scripts.run_paper_intelligence_campaign": "its timer is disabled",
}


# --------------------------------------------------------------------------
# The list is exactly the enabled units' modules.
# --------------------------------------------------------------------------

def test_every_enabled_unit_module_is_declared():
    declared = set(_tool().ENTRY_POINTS)
    for unit, module in ENABLED_UNITS_TO_MODULE.items():
        assert module in declared, (
            f"{unit} runs {module}, which is not in ENTRY_POINTS -- everything "
            f"it reaches would be misclassified as unreachable")


def test_the_shadow_campaigns_real_entrypoint_is_declared():
    """The specific one that was missing, named on its own so a regression
    reads as what it is."""
    assert "scripts.run_phase20_13_live_entrypoint" in _tool().ENTRY_POINTS


def test_no_unenabled_unit_is_declared_as_an_entry_point():
    declared = set(_tool().ENTRY_POINTS)
    for module, why in NOT_ENABLED.items():
        assert module not in declared, f"{module} is declared but {why}"


def test_the_declared_list_contains_nothing_else():
    """Both directions. A list that may quietly grow is a list that will."""
    assert set(_tool().ENTRY_POINTS) == set(ENABLED_UNITS_TO_MODULE.values())


def test_every_declared_entry_point_resolves_to_a_real_module():
    tool = _tool()
    modules = tool.module_map(REPO_ROOT)
    missing = [e for e in tool.ENTRY_POINTS if e not in modules]
    assert missing == [], f"declared entry points that do not exist: {missing}"


# --------------------------------------------------------------------------
# The quarantined stack is not production-reachable.
# --------------------------------------------------------------------------

def _reachable():
    tool = _tool()
    modules = tool.module_map(REPO_ROOT)
    graph = {n: tool.imports_of(p, n, is_package=(p.name == "__init__.py"))
             for n, p in modules.items()}
    return tool.reachable_from(tool.ENTRY_POINTS, graph), modules


@pytest.mark.parametrize("module", sorted(NOT_ENABLED))
def test_a_quarantined_entrypoint_is_not_production_reachable(module):
    reachable, modules = _reachable()
    if module not in modules:
        pytest.skip(f"{module} is not present in this checkout")
    assert module not in reachable, (
        f"{module} became production-reachable ({NOT_ENABLED[module]}). Either "
        f"a production module now imports it -- which puts a retired stack back "
        f"in the runtime -- or its unit was enabled and ENTRY_POINTS was not "
        f"updated. Both must be deliberate.")


@pytest.mark.parametrize("module", [
    "bujji.core.orchestrator",
    "bujji.core.session_state",
    "bujji.replay.broker",
    "bujji.shadow_lifecycle.orchestrator",
])
def test_the_legacy_stack_behind_those_entrypoints_is_not_reachable_either(module):
    """These are reachable only from `bujji.app`. Correcting the entry-point
    list is what makes that visible -- before it, `bujji.core.orchestrator`
    was declared as a violation that "defines what production trades"."""
    reachable, modules = _reachable()
    if module not in modules:
        pytest.skip(f"{module} is not present in this checkout")
    assert module not in reachable, (
        f"{module} is part of the retired bujji.app stack and is now reachable "
        f"from a real entry point. Migrate it deliberately or keep it out.")


def test_the_quarantine_map_and_the_entry_points_cannot_both_claim_a_module():
    tool = _tool()
    overlap = set(tool.QUARANTINED_ENTRY_POINTS) & set(tool.ENTRY_POINTS)
    assert overlap == set(), f"declared as both live and quarantined: {overlap}"


def test_out_of_repo_units_are_not_expected_as_modules():
    """Three enabled units run scripts outside this repository. Recording them
    here stops a future reader 'fixing' their absence by inventing a module."""
    tool = _tool()
    modules = tool.module_map(REPO_ROOT)
    for unit, path in ENABLED_UNITS_OUTSIDE_THE_REPO.items():
        stem = pathlib.Path(path).stem
        assert stem not in modules, (
            f"{unit} runs {path}, which lives outside the repo; a module named "
            f"{stem} now exists and may be confused for it")

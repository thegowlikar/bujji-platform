"""Phase 17E — Layer 0 safety / anti-drift.

Turns Phase 17E's "Forbidden in Layer 0" list from a code-review
convention into a mechanical, permanently-enforced property. Follows the
codebase's established `test_*_safety.py` AST-scan pattern.

Layer 0 stores RAW OBSERVATION ONLY. If a future change ever computes an
IV, a Greek, a VWAP, a regime or a signal inside this package -- or
reaches into MSI / MIC / strategy / execution / broker code -- these
tests fail loudly rather than letting the memory foundation quietly
become an intelligence layer.
"""
import ast
import os

_PACKAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "bujji",
    "market_reality",
)

# Layers Layer 0 must never reach into. Phase 17E is the foundation of
# memory and nothing else; a dependency on any of these would invert the
# architecture's direction (intelligence depends on memory, never the
# reverse).
_FORBIDDEN_MODULE_PREFIXES = (
    "bujji.msi_",
    "bujji.mic_replay",
    "mic_v2",
    "bujji.trading_brain",
    "bujji.production_runtime",
    "bujji.strategy_selector",
    "bujji.execution",
    "bujji.execution_engine",
    "bujji.runtime_execution",
    "bujji.broker",
    "bujji.intelligence",
    "bujji.signal",
    "bujji.market_timeseries",
    "fyers_apiv3",
)

# Derived-product vocabulary. None of it belongs at Layer 0 -- these are
# materializer concerns (Layer 1+), where the derivation itself becomes
# part of the record's lineage.
_FORBIDDEN_COMPUTATION_TERMS = (
    "implied_vol",
    "black_scholes",
    "vwap",
    "moving_average",
    "rsi_",
    "bollinger",
    "classify_regime",
    "trade_signal",
    "strategy_",
    "place_order",
)


def _package_files():
    return [
        os.path.join(_PACKAGE_DIR, name)
        for name in sorted(os.listdir(_PACKAGE_DIR))
        if name.endswith(".py")
    ]


def _imported_names(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def test_layer0_never_imports_an_intelligence_or_execution_module():
    for path in _package_files():
        with open(path) as fh:
            tree = ast.parse(fh.read(), filename=path)
        for name in _imported_names(tree):
            for forbidden in _FORBIDDEN_MODULE_PREFIXES:
                assert not name.startswith(forbidden), (
                    f"{os.path.basename(path)} imports forbidden module {name} -- "
                    "Layer 0 is the foundation of memory and must not depend on "
                    "any layer above it"
                )


def test_layer0_defines_no_derived_computation():
    """Function and class NAMES must not describe a derived product. The
    forbidden-field constant lists in taxonomy.py are exempt: naming a
    thing in order to reject it is the opposite of computing it."""
    for path in _package_files():
        if os.path.basename(path) == "taxonomy.py":
            continue
        with open(path) as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lowered = node.name.lower()
                for term in _FORBIDDEN_COMPUTATION_TERMS:
                    assert term not in lowered, (
                        f"{os.path.basename(path)} defines {node.name!r}, which names a "
                        "derived product -- that belongs to a materializer, not Layer 0"
                    )


def test_layer0_never_reads_the_wall_clock():
    """Every timestamp is injected by the caller. A wall-clock read would
    make a replayed capture sequence produce different records than the
    original -- replay determinism depends on this."""
    for path in _package_files():
        with open(path) as fh:
            source = fh.read()
        for banned in ("datetime.now(", "datetime.utcnow(", "time.time(", "uuid4("):
            assert banned not in source, (
                f"{os.path.basename(path)} calls {banned} -- Layer 0 must be "
                "deterministic; inject the timestamp instead"
            )


def test_layer0_writes_no_bytes_of_its_own():
    """Durability is delegated entirely to state_persistence.EventStore.
    A raw `open(..., 'w')` here would mean a parallel persistence system,
    which Phase 17E explicitly forbids."""
    for path in _package_files():
        if os.path.basename(path) in ("store.py", "replay.py"):
            continue  # These compose EventStore; verified separately below.
        with open(path) as fh:
            source = fh.read()
        assert "open(" not in source, (
            f"{os.path.basename(path)} opens a file directly -- persistence "
            "belongs to EventStore alone"
        )


def test_store_delegates_persistence_to_the_existing_eventstore():
    with open(os.path.join(_PACKAGE_DIR, "store.py")) as fh:
        source = fh.read()
    assert "from bujji.state_persistence.store import EventStore" in source
    # No hand-rolled writing: the store routes records, it does not
    # serialize or fsync them itself.
    assert "json.dump" not in source
    assert "os.fsync" not in source


def test_forbidden_payload_fields_are_actually_enforced_at_runtime():
    """The taxonomy's forbidden list must be wired into the validator,
    not merely declared. A list nobody checks is documentation, not a
    guarantee."""
    from bujji.market_reality import taxonomy, validator
    from bujji.market_reality.capture import build_raw_observation

    for field in taxonomy.FORBIDDEN_PAYLOAD_FIELDS:
        raw = build_raw_observation(
            kind=taxonomy.KIND_QUOTE,
            instrument="NSE:NIFTY50-INDEX",
            instrument_type=taxonomy.INSTRUMENT_SPOT,
            payload={"ltp": 100.0, field: 1.0},
            source="fyers",
            access_method="direct_sdk_fyers_broker_py",
            capture_timestamp="2026-08-12T09:20:00+00:00",
            certification_status=taxonomy.CERTIFIED_AVAILABLE,
            identity_fields={},
        )
        outcome = validator.validate(
            raw,
            certification_status=taxonomy.CERTIFIED_AVAILABLE,
            now="2026-08-12T09:30:00+00:00",
        )
        assert not outcome.is_valid, f"{field!r} must be rejected from Layer 0"


def test_market_depth_type_registered_in_the_canonical_taxonomy():
    from bujji.market_observation import taxonomy as moc_taxonomy

    assert moc_taxonomy.TYPE_MARKET_DEPTH in moc_taxonomy.ALL_OBSERVATION_TYPES


def test_schema_bump_did_not_invalidate_existing_records():
    """1.1.0 adds a domain; it removes and reshapes nothing. Records
    written under 1.0.0 must remain recognized -- a version bump gates
    new consumers, it never invalidates old facts."""
    from bujji.market_observation import taxonomy as moc_taxonomy

    assert moc_taxonomy.MARKET_OBSERVATION_VERSION == "1.1.0"
    assert "1.0.0" in moc_taxonomy.RECOGNIZED_SCHEMA_VERSIONS
    assert "1.1.0" in moc_taxonomy.RECOGNIZED_SCHEMA_VERSIONS

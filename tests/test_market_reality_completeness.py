"""Phase 17E — Observation Completeness Monitor.

Measures capture, never market behaviour.
"""
from bujji.market_reality import completeness, taxonomy
from bujji.market_reality.capture import build_raw_observation

CERT = taxonomy.CERTIFIED_AVAILABLE
WINDOW_START = "2026-08-12T09:15:00+00:00"
WINDOW_END = "2026-08-12T09:20:00+00:00"  # 5 x 60s intervals.


def _obs(minute, second=0, instrument="NSE:NIFTY50-INDEX", kind=taxonomy.KIND_QUOTE):
    stamp = f"2026-08-12T09:{minute:02d}:{second:02d}+00:00"
    return build_raw_observation(
        kind=kind,
        instrument=instrument,
        instrument_type=taxonomy.INSTRUMENT_SPOT,
        payload={"ltp": 24300.0 + minute},
        source="fyers",
        access_method="direct_sdk_fyers_broker_py",
        capture_timestamp=stamp,
        event_timestamp=stamp,
        certification_status=CERT,
        identity_fields={},
    )


def _measure(observations, **overrides):
    kwargs = dict(
        instrument="NSE:NIFTY50-INDEX",
        kind=taxonomy.KIND_QUOTE,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        interval_seconds=60,
    )
    kwargs.update(overrides)
    return completeness.measure(observations, **kwargs)


def test_expected_count_is_derived_from_the_window():
    assert completeness.expected_interval_count(WINDOW_START, WINDOW_END, 60) == 5


def test_a_fully_delivered_feed_is_healthy_with_no_gaps():
    report = _measure([_obs(m) for m in range(15, 20)])
    assert report.expected_count == 5
    assert report.received_count == 5
    assert report.missing_intervals == ()
    assert report.source_health == taxonomy.SOURCE_HEALTHY


def test_missing_intervals_are_named_explicitly():
    """A gap is a first-class recorded fact, never a silent absence."""
    report = _measure([_obs(15), _obs(16), _obs(19)])
    assert report.received_count == 3
    assert len(report.missing_intervals) == 2
    starts = [pair[0] for pair in report.missing_intervals]
    assert "2026-08-12T09:17:00+00:00" in starts
    assert "2026-08-12T09:18:00+00:00" in starts


def test_a_partially_delivering_feed_is_degraded():
    report = _measure([_obs(15), _obs(16)])
    assert report.source_health == taxonomy.SOURCE_DEGRADED


def test_a_feed_delivering_nothing_is_silent():
    report = _measure([])
    assert report.received_count == 0
    assert report.source_health == taxonomy.SOURCE_SILENT
    assert len(report.missing_intervals) == 5


def test_nothing_expected_means_healthy_not_invented_alarm():
    """When no observations were expected there is no evidence of a
    problem; inventing one would be as dishonest as hiding a real gap."""
    assert completeness.classify_source_health(0, 0) == taxonomy.SOURCE_HEALTHY


def test_multiple_observations_in_one_interval_do_not_fake_completeness():
    """Five ticks in a single minute do not mean five minutes were
    covered -- coverage is measured per interval, not per record."""
    report = _measure([_obs(15, second=s) for s in (0, 10, 20, 30, 40)])
    assert report.received_count == 5
    assert len(report.missing_intervals) == 4
    assert report.source_health == taxonomy.SOURCE_DEGRADED


def test_other_instruments_are_not_counted():
    """Completeness of a NIFTY feed says nothing about a BANKNIFTY feed;
    conflating them would report a healthy feed as degraded."""
    observations = [_obs(m) for m in range(15, 20)]
    observations.append(_obs(15, instrument="NSE:BANKNIFTY-INDEX"))
    report = _measure(observations)
    assert report.received_count == 5


def test_other_kinds_are_not_counted():
    observations = [_obs(m) for m in range(15, 20)]
    observations.append(_obs(15, kind=taxonomy.KIND_MARKET_DEPTH))
    report = _measure(observations)
    assert report.received_count == 5


def test_observations_outside_the_window_are_excluded():
    report = _measure([_obs(15), _obs(16), _obs(45)])
    assert report.received_count == 2


def test_invalid_window_yields_zero_expectation_not_a_crash():
    assert completeness.expected_interval_count(WINDOW_END, WINDOW_START, 60) == 0
    assert completeness.expected_interval_count("garbage", WINDOW_END, 60) == 0
    assert completeness.expected_interval_count(WINDOW_START, WINDOW_END, 0) == 0


def test_report_serializes_cleanly():
    report = _measure([_obs(15)])
    as_dict = report.to_dict()
    assert as_dict["expected_count"] == 5
    assert as_dict["received_count"] == 1
    assert as_dict["source_health"] in taxonomy.ALL_SOURCE_HEALTH_STATES

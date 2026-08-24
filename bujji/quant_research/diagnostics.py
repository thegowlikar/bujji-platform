"""Descriptive diagnostics over a phase-filtered tick corpus.

DESCRIPTIVE MEANS DESCRIPTIVE. Nothing here is a signal, a forecast or a
ranking. These functions answer "what can this data support?" -- which methods
are even testable given the observed update rate, spread and coverage -- and
that question must be settled before any method is tried, or the first
backtest silently inherits a data limitation as a result.

EVERY STATISTIC CARRIES ITS DENOMINATOR. A percentage without the count it was
taken over has repeatedly hidden a defect in this project: a blended
availability figure once averaged options and index frames into one number
that described neither. Each result below reports `n`, and where a population
is heterogeneous it reports per class rather than blended.

ABSENCE IS NEVER ZERO. A missing bid is not a bid of zero, and a symbol with
no ticks is excluded from a rate rather than counted at rate zero.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .dataset import SOURCE_REST_CHAIN, SOURCE_TICK, TickDataset, TickRecord

# Andersen & Bollerslev's practical standard. Below roughly five minutes,
# realized variance is increasingly dominated by microstructure noise -- bid-ask
# bounce and discreteness -- rather than by price variation, so the estimator
# measures the venue's plumbing instead of the market's volatility.
RV_SAMPLING_SECONDS = 300

CLASS_OPTION = "OPTION"
CLASS_FUTURE = "FUTURE"
CLASS_INDEX = "INDEX"
CLASS_OTHER = "OTHER"

INSUFFICIENT = "INSUFFICIENT_DATA"


def classify_symbol(symbol: Optional[str], payload: Optional[Dict] = None) -> str:
    if payload and payload.get("type") == "if":
        return CLASS_INDEX
    if not symbol:
        return CLASS_OTHER
    s = symbol.upper()
    if s.endswith("-INDEX"):
        return CLASS_INDEX
    if s.endswith(("CE", "PE")):
        return CLASS_OPTION
    if "FUT" in s:
        return CLASS_FUTURE
    return CLASS_OTHER


def _quantile(sorted_vals: List[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))
    return sorted_vals[idx]


def _dist(values: Iterable[float]) -> Dict[str, Any]:
    v = sorted(x for x in values if x is not None)
    if not v:
        return {"n": 0, "status": INSUFFICIENT}
    return {
        "n": len(v),
        "median": statistics.median(v),
        "p10": _quantile(v, 0.10), "p25": _quantile(v, 0.25),
        "p75": _quantile(v, 0.75), "p90": _quantile(v, 0.90),
        "p95": _quantile(v, 0.95),
        "min": v[0], "max": v[-1],
    }


class CorpusDiagnostics:
    """One pass over the corpus, then every diagnostic off the accumulated state.

    A single pass matters: these corpora run to hundreds of megabytes and
    millions of records, and re-streaming per statistic would make the whole
    suite too slow to run routinely -- a diagnostic nobody runs is not a
    diagnostic.
    """

    def __init__(self, dataset: TickDataset):
        self.dataset = dataset
        self._ts: Dict[str, List[float]] = defaultdict(list)
        self._ltp: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
        self._spreads: Dict[str, List[float]] = defaultdict(list)
        self._bid_sizes: Dict[str, List[float]] = defaultdict(list)
        self._one_sided = defaultdict(int)
        self._two_sided = defaultdict(int)
        self._crossed = defaultdict(int)
        self._records = 0
        self._class_of: Dict[str, str] = {}
        self._field_present: Dict[str, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int))
        self._class_records = defaultdict(int)
        self._scanned = False

    def scan(self) -> "CorpusDiagnostics":
        for rec in self.dataset.stream():
            self._consume(rec)
        self._scanned = True
        return self

    def _consume(self, rec: TickRecord) -> None:
        sym = rec.symbol
        if not sym:
            return
        self._records += 1
        cls = self._class_of.get(sym)
        if cls is None:
            cls = classify_symbol(sym, rec.payload)
            self._class_of[sym] = cls
        self._class_records[cls] += 1

        for key, val in rec.payload.items():
            if val is not None:
                self._field_present[cls][key] += 1

        if rec.recv_ts is not None:
            self._ts[sym].append(rec.recv_ts)
            ltp = rec.field("ltp")
            if isinstance(ltp, (int, float)) and ltp > 0:
                self._ltp[sym].append((rec.recv_ts, float(ltp)))

        bid, ask = rec.field("bid_price"), rec.field("ask_price")
        has_bid = isinstance(bid, (int, float)) and bid > 0
        has_ask = isinstance(ask, (int, float)) and ask > 0
        if has_bid and has_ask:
            if ask < bid:
                # A crossed book is a data-quality event, not a spread. Folding
                # it into the distribution as a negative number would drag the
                # median toward a market state that did not exist.
                self._crossed[cls] += 1
            else:
                self._two_sided[cls] += 1
                mid = (bid + ask) / 2.0
                if mid > 0:
                    self._spreads[cls].append((ask - bid) / mid)
        elif has_bid or has_ask:
            self._one_sided[cls] += 1

        bs = rec.field("bid_size")
        if isinstance(bs, (int, float)):
            self._bid_sizes[cls].append(float(bs))

    # ---- diagnostics ----------------------------------------------------
    def coverage(self) -> Dict[str, Any]:
        all_ts = [t for v in self._ts.values() for t in v]
        span = (max(all_ts) - min(all_ts)) if all_ts else None
        by_class = defaultdict(int)
        for cls in self._class_of.values():
            by_class[cls] += 1
        return {
            "records": self._records,
            "distinct_symbols": len(self._class_of),
            "symbols_by_class": dict(by_class),
            "records_by_class": dict(self._class_records),
            "span_seconds": span,
            "basis": "phase-filtered stream; unclassified records excluded",
        }

    def update_rates(self) -> Dict[str, Any]:
        """Messages per second per symbol, and the inter-arrival distribution.

        This is the diagnostic that decides which methods are testable at all.
        A method needing a fresh quote every few seconds is untestable on a
        symbol updating once every five minutes, and that is a fact about the
        data, discoverable here, not something a backtest should reveal later
        as an unexplained result.
        """
        out: Dict[str, Any] = {}
        for cls in set(self._class_of.values()):
            rates, gaps, stale = [], [], []
            for sym, ts in self._ts.items():
                if self._class_of.get(sym) != cls or len(ts) < 3:
                    continue
                t = sorted(ts)
                span = t[-1] - t[0]
                if span > 0:
                    rates.append(len(t) / span)
                d = [b - a for a, b in zip(t, t[1:])]
                if d:
                    gaps.append(statistics.median(d))
                    stale.append(max(d))
            out[cls] = {
                "symbols_measured": len(rates),
                "msgs_per_second": _dist(rates),
                "median_inter_arrival_s": _dist(gaps),
                "worst_staleness_s": _dist(stale),
                "symbols_under_1_msg_per_60s": sum(1 for r in rates if r < 1 / 60.0),
            }
        return out

    def realized_volatility(self, sampling_seconds: int = RV_SAMPLING_SECONDS
                            ) -> Dict[str, Any]:
        """Per-symbol realized volatility from last-price in fixed buckets.

        Reports the number of buckets each estimate rests on, because a
        realized-variance figure from three buckets is not a volatility
        measurement and must not be presented beside one from seventy.
        """
        results = {}
        for sym, series in self._ltp.items():
            if len(series) < 4:
                continue
            buckets: Dict[int, float] = {}
            for ts, px in sorted(series):
                buckets[int(ts // sampling_seconds)] = px   # last in bucket
            keys = sorted(buckets)
            if len(keys) < 3:
                continue
            rets, missing = [], 0
            for prev, cur in zip(keys, keys[1:]):
                if cur - prev != 1:
                    # A gap means the bucket had no trade. Chaining across it
                    # would attribute a multi-bucket move to one interval.
                    missing += 1
                    continue
                a, b = buckets[prev], buckets[cur]
                if a > 0 and b > 0:
                    rets.append(math.log(b / a))
            if len(rets) < 2:
                continue
            rv = math.sqrt(sum(r * r for r in rets))
            results[sym] = {
                "realized_vol_over_window": rv,
                "buckets_used": len(rets) + 1,
                "buckets_skipped_for_gaps": missing,
                "sampling_seconds": sampling_seconds,
                "class": self._class_of.get(sym),
                "largest_abs_log_return": max(abs(r) for r in rets),
            }
        by_class = defaultdict(list)
        for sym, r in results.items():
            by_class[r["class"]].append(r["realized_vol_over_window"])
        return {
            "per_symbol": results,
            "by_class": {c: _dist(v) for c, v in by_class.items()},
            "sampling_seconds": sampling_seconds,
            "sampling_basis": (
                "5-minute sampling is the practical standard (Andersen & "
                "Bollerslev); finer sampling on this venue would measure "
                "microstructure noise rather than volatility"),
            "limit": ("computed from last-traded price, so it is a property of "
                      "trades that occurred, not of the quoted market"),
        }

    def liquidity(self) -> Dict[str, Any]:
        """Spread, quoted size, and how often a two-sided quote exists at all.

        The last part is the one that constrains Bujji most: a strategy needs a
        price it could transact at, and a one-sided book does not offer one.
        """
        out = {}
        for cls in set(list(self._spreads) + list(self._one_sided)
                       + list(self._two_sided) + list(self._class_of.values())):
            two, one = self._two_sided[cls], self._one_sided[cls]
            total = two + one + self._crossed[cls]
            sizes = self._bid_sizes.get(cls, [])
            out[cls] = {
                "relative_spread": _dist(self._spreads.get(cls, [])),
                "two_sided_quotes": two,
                "one_sided_quotes": one,
                "crossed_quotes": self._crossed[cls],
                "two_sided_fraction": (two / total) if total else None,
                "quote_observations": total,
                "bid_size": _dist(sizes),
                "zero_bid_size_count": sum(1 for x in sizes if x == 0),
                "spread_basis": "(ask - bid) / midpoint, two-sided quotes only",
            }
        return out

    def intraday_profile(self, bucket_seconds: int = 900) -> Dict[str, Any]:
        """Activity by time of day. Seasonality is a confound, not a signal.

        Open and close are structurally different from midday. A result that
        does not separate them may be measuring the time of day it happened to
        trade at.
        """
        buckets = defaultdict(lambda: defaultdict(int))
        for sym, ts in self._ts.items():
            cls = self._class_of.get(sym, CLASS_OTHER)
            for t in ts:
                buckets[cls][int((t % 86400) // bucket_seconds)] += 1
        return {
            "bucket_seconds": bucket_seconds,
            "counts_by_class_and_bucket": {c: dict(b) for c, b in buckets.items()},
            "limit": ("buckets are receiver wall-clock, not exchange time, and "
                      "one session cannot establish a seasonal profile"),
        }

    def field_availability(self) -> Dict[str, Any]:
        """Which fields actually arrive, PER CLASS -- never blended.

        Options, futures and index frames carry different field sets. One
        combined availability percentage would describe no instrument that
        exists.
        """
        out = {}
        for cls, fields in self._field_present.items():
            total = self._class_records[cls]
            out[cls] = {
                "records": total,
                "fields": {k: {"present": v,
                               "fraction": (v / total) if total else None}
                           for k, v in sorted(fields.items())},
            }
        return out

    def method_feasibility(self) -> Dict[str, Any]:
        """What the measured data can and cannot support. Not a recommendation.

        Each entry states a requirement and whether this corpus meets it. It
        never says a method is profitable, only whether it is TESTABLE here.
        """
        rates = self.update_rates().get(CLASS_OPTION, {})
        liq = self.liquidity().get(CLASS_OPTION, {})
        med_rate = (rates.get("msgs_per_second") or {}).get("median")
        med_spread = (liq.get("relative_spread") or {}).get("median")
        two_sided = liq.get("two_sided_fraction")
        cov = self.coverage()
        span_h = (cov["span_seconds"] / 3600.0) if cov["span_seconds"] else 0.0

        def verdict(ok, detail):
            return {"testable_on_this_corpus": ok, "detail": detail}

        return {
            "measured_inputs": {
                "median_option_msgs_per_second": med_rate,
                "median_relative_spread": med_spread,
                "two_sided_quote_fraction": two_sided,
                "span_hours": span_h,
                "sessions": 1,
            },
            "assessments": {
                "intraday_market_making": verdict(
                    False,
                    "requires depth beyond top-of-book, queue position and "
                    "sub-second updates; none are present"),
                "high_frequency_signals": verdict(
                    False,
                    f"median option update rate {med_rate} msg/s cannot support "
                    f"decisions at second resolution"),
                "realized_vs_implied_volatility": verdict(
                    None,
                    "realized volatility is computable at 5-minute sampling, "
                    "but implied volatility is not carried in the feed and one "
                    "session cannot establish a premium"),
                "open_interest_positioning": verdict(
                    None,
                    "OI is a REST-chain fact with its own timestamp; it may be "
                    "described but never treated as synchronous with ticks"),
                "daily_or_multi_day_structure": verdict(
                    False,
                    "one session provides no cross-session variation"),
            },
            "note": ("testable is not promising and not profitable. A True here "
                     "means only that the data does not immediately preclude "
                     "an honest test."),
        }

    def report(self) -> Dict[str, Any]:
        if not self._scanned:
            self.scan()
        return {
            "dataset_fingerprint": self.dataset.manifest.fingerprint(),
            "session_date": self.dataset.manifest.session_date,
            "included_classifications":
                list(self.dataset.manifest.included_classifications),
            "coverage": self.coverage(),
            "update_rates": self.update_rates(),
            "liquidity": self.liquidity(),
            "realized_volatility": self.realized_volatility(),
            "intraday_profile": self.intraday_profile(),
            "field_availability": self.field_availability(),
            "method_feasibility": self.method_feasibility(),
            "authorizes": ("NOTHING. This is a description of a dataset. It is "
                           "not a signal, a ranking, or permission to trade."),
        }

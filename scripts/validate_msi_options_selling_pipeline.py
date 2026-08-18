"""Phase 20.28 -- MSI Options Selling Pipeline Validation.

Read-only, offline, deterministic. No live broker, no systemd, no
contact with today's Cycle-1 shadow campaign. Drives
IntelligenceCycleRecorder.record_cycle() with synthetic-but-real
MarketSnapshot sequences and a stub broker, exactly the pattern
tests/test_intelligence_cycle_recorder*.py already use.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/bujji/app")

from bujji.core.models import Candle
from bujji.market_perception.models import (
    HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot,
    OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.market_state.intelligence_cycle_recorder import IntelligenceCycleRecorder


def make_snapshot(spot, ts, ce_bid, ce_ask, pe_bid, pe_ask, ce_oi, pe_oi, atm_strike, vix=13.0):
    legs = (
        OptionLeg(symbol="CE", strike=atm_strike, option_type="CE", ltp=None, bid=ce_bid, ask=ce_ask,
                  spread=round(ce_ask - ce_bid, 2), volume=None, open_interest=ce_oi, iv=None,
                  delta=None, gamma=None, theta=None, vega=None),
        OptionLeg(symbol="PE", strike=atm_strike, option_type="PE", ltp=None, bid=pe_bid, ask=pe_ask,
                  spread=round(pe_ask - pe_bid, 2), volume=None, open_interest=pe_oi, iv=None,
                  delta=None, gamma=None, theta=None, vega=None),
    )
    chain = OptionChainSnapshot(underlying="NIFTY", expiry="2026-09-25", atm_strike=atm_strike,
                                 config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs)
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="synthetic_validation", latency_ms=1.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot), vix=VixSnapshot(value=vix),
        futures=None, option_chain=chain,
    )


class StubBroker:
    """Never actually called: record_cycle() only touches broker when
    candles=None, and every call below supplies real candles directly."""
    async def get_recent_candles(self, underlying, minutes, count):
        raise AssertionError("StubBroker.get_recent_candles should never be called -- candles are always supplied directly")


def make_candles(closes, base_ts):
    out = []
    for i, c in enumerate(closes):
        ts = base_ts.replace(minute=(base_ts.minute + i * 5) % 60,
                              hour=base_ts.hour + (base_ts.minute + i * 5) // 60)
        out.append(Candle(
            timestamp=ts, open=c - 1, high=c + 2, low=c - 2, close=c, volume=1000,
        ))
    return out


async def run_scenario(name, snapshots_and_candles, atm_strike):
    recorder = IntelligenceCycleRecorder()
    broker = StubBroker()
    records = []
    for snapshot, candles, clock_dt in snapshots_and_candles:
        clock = lambda dt=clock_dt: dt
        record = await recorder.record_cycle(snapshot, broker, clock, candles=candles)
        records.append(record)
    return records


def summarize(records, family_focus=("NEUTRAL_PREMIUM_SELLING", "IRON_CONDOR", "IRON_FLY", "LONG_DIRECTIONAL", "SHORT_DIRECTIONAL")):
    last = records[-1]
    print(f"  market_direction: {json.dumps(last['market_direction'], default=str)}")
    print(f"  market_structure: {json.dumps({k: last['market_structure'].get(k) for k in ('structure_state', 'structure_location', 'confidence')} if last['market_structure'] else None)}")
    print(f"  volatility_structure: {json.dumps({k: last['volatility_structure'].get(k) for k in ('iv_state', 'expansion_state', 'compression_state', 'volatility_regime')} if last['volatility_structure'] else None)}")
    print(f"  consensus: {json.dumps({k: last['consensus'].get(k) for k in ('consensus_level', 'evidence_sufficiency')} if last['consensus'] else None)}")
    print(f"  liquidity: {json.dumps({k: last['liquidity'].get(k) for k in ('tightness', 'data_quality')} if last['liquidity'] else None)}")
    print(f"  strategy_suitability (focus families):")
    for row in (last['strategy_suitability'] or []):
        if row['strategy_family'] in family_focus:
            print(f"    {row['strategy_family']}: {row['suitability']} (confidence={row['confidence']})")
            if row['suitability'] != 'SUITABLE':
                for r in row['rejecting_reasons'][:3]:
                    print(f"      REJECT: {r}")
            else:
                for r in row['supporting_reasons'][:3]:
                    print(f"      SUPPORT: {r}")
    sel = last['strategy_selection']
    print(f"  strategy_selection: selected={sel['selected_strategy_family'] if sel else None}, confidence={sel['confidence'] if sel else None}")
    if sel and sel.get('explanation'):
        for r in sel['explanation'].get('why_this_strategy', []):
            print(f"    WHY: {r}")


async def main():
    print("=" * 70)
    print("SCENARIO A -- Range Premium Selling Environment")
    print("=" * 70)
    # Spot oscillates in a tight ~40pt band around 24500 -- range-bound,
    # not trending. Premiums stay elevated relative to a low realized
    # move (rich IV), option chain unchanged (no expansion).
    base_ts_a = datetime(2026, 8, 17, 9, 15, tzinfo=timezone.utc)
    spots_a = [24500, 24515, 24490, 24505, 24495, 24510]
    cycles_a = []
    for i, spot in enumerate(spots_a):
        ts = f"2026-08-17T{9+((15+i*5)//60):02d}:{(15+i*5)%60:02d}:00+05:30"
        snapshot = make_snapshot(spot, ts, ce_bid=449.0, ce_ask=451.0, pe_bid=449.0, pe_ask=451.0,
                                  ce_oi=1_000_000, pe_oi=1_000_000, atm_strike=24500, vix=13.0)
        candles = make_candles([24500 + (j % 3) * 5 - 5 for j in range(20)], base_ts_a)
        cycles_a.append((snapshot, candles, base_ts_a))
    records_a = await run_scenario("A", cycles_a, 24500)
    summarize(records_a)
    print()

    print("=" * 70)
    print("SCENARIO B -- Unfavorable Volatility Environment")
    print("=" * 70)
    base_ts_b = datetime(2026, 8, 17, 9, 15, tzinfo=timezone.utc)
    # Spot whipsaws violently (large candle-to-candle swings both ways)
    # -- expanding realized vol, unstable/irregular.
    spots_b = [24500, 24650, 24400, 24680, 24380, 24620]
    cycles_b = []
    for i, spot in enumerate(spots_b):
        ts = f"2026-08-17T{9+((15+i*5)//60):02d}:{(15+i*5)%60:02d}:00+05:30"
        snapshot = make_snapshot(spot, ts, ce_bid=350.0, ce_ask=550.0, pe_bid=350.0, pe_ask=550.0,
                                  ce_oi=300_000, pe_oi=300_000, atm_strike=24500, vix=28.0)
        candles = make_candles([24300 + (j * 25 if j % 2 == 0 else -j * 20) for j in range(20)], base_ts_b)
        cycles_b.append((snapshot, candles, base_ts_b))
    records_b = await run_scenario("B", cycles_b, 24500)
    summarize(records_b)
    print()

    print("=" * 70)
    print("SCENARIO C -- Strong Directional Environment")
    print("=" * 70)
    base_ts_c = datetime(2026, 8, 17, 9, 15, tzinfo=timezone.utc)
    # Spot trends strongly and monotonically upward.
    spots_c = [24500, 24580, 24660, 24740, 24820, 24900]
    cycles_c = []
    for i, spot in enumerate(spots_c):
        ts = f"2026-08-17T{9+((15+i*5)//60):02d}:{(15+i*5)%60:02d}:00+05:30"
        snapshot = make_snapshot(spot, ts, ce_bid=449.0, ce_ask=451.0, pe_bid=449.0, pe_ask=451.0,
                                  ce_oi=1_000_000, pe_oi=1_000_000, atm_strike=24500 + i * 50, vix=13.0)
        candles = make_candles([24300 + j * 20 for j in range(20)], base_ts_c)
        cycles_c.append((snapshot, candles, base_ts_c))
    records_c = await run_scenario("C", cycles_c, 24500)
    summarize(records_c)


if __name__ == "__main__":
    asyncio.run(main())

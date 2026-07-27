"""Lightweight read-only local dashboard.

Serves a single auto-refreshing HTML page plus a JSON status endpoint using
only the Python standard library (no framework dependency). It observes the
shared :class:`RuntimeStatus` and the :class:`TradeJournal`; it never mutates
trading state.

Freshness math (staleness + candle age) is computed SERVER-SIDE in the request
handler, so it is correct regardless of the browser's timezone — the client
only displays the numbers, never subtracts timestamps itself.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from ..core.clock import now_ist
from ..core.runtime_status import RuntimeStatus
from ..journal.journal import TradeJournal

_PAGE = """<!doctype html><html><head><meta charset='utf-8'>
<title>Bujji VWAP Premium Straddle Seller</title>
<meta http-equiv='refresh' content='{refresh}'>
<style>
body{{font-family:system-ui,Arial;margin:24px;background:#0f1116;color:#e6e6e6}}
h1{{font-size:20px}} h3{{margin-top:22px}} .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.card{{background:#1a1d26;border:1px solid #2a2f3a;border-radius:10px;padding:14px}}
.k{{color:#8b93a7;font-size:12px}} .v{{font-size:20px;margin-top:4px}}
.pos{{color:#4ade80}} .neg{{color:#f87171}} .warn{{color:#fbbf24}}
table{{width:100%;border-collapse:collapse;margin-top:10px}}
td,th{{border-bottom:1px solid #2a2f3a;padding:6px;font-size:12px;text-align:left}}
pre{{background:#12151c;padding:10px;border-radius:8px;max-height:240px;overflow:auto;font-size:11px}}
.state{{display:inline-block;padding:4px 10px;border-radius:6px;background:#2563eb}}
.stalebar{{padding:12px;border-radius:10px;margin:10px 0;font-size:15px}}
</style></head><body>
<h1>Bujji VWAP Premium Straddle Seller &nbsp; <span class='state' id='state'></span></h1>
<div id='staleness' class='stalebar'></div>
<div class='grid' id='cards'></div>
<h3>Risk / MTM</h3>
<div class='grid' id='risk'></div>
<h3>System &amp; Auth</h3>
<div class='grid' id='sysauth'></div>
<h3>Tick / WebSocket &amp; Candle Health</h3>
<div class='grid' id='tick_health'></div>
<h3>Capital Management</h3>
<div id='capital_banner'></div>
<div class='grid' id='capital'></div>
<h3>Premium VWAP Health</h3>
<div id='mdh_banner'></div>
<div class='grid' id='mdh'></div>
<details><summary>Premium VWAP audit history</summary><div id='mdh_history'></div></details>
<h3>Operations &nbsp;<span class='k'>(health/alerts -- observation only, never influences trading)</span></h3>
<div id='ops_banner'></div>
<div class='grid' id='ops'></div>
<h3>Market Intelligence Core &nbsp;<span class='k'>(read-only observation layer -- never influences trading)</span></h3>
<div class='grid' id='mic'></div>
<h3>Today's Logs</h3><pre id='logs'></pre>
<h3>Trade History</h3><div id='trades'></div>
<script>
const STALE_AFTER={stale_after};
const STOP_LOSS={stop_loss};
const PROFIT_TARGET={profit_target};
function inr(n){{return n==null?'-':'₹ '+Number(n).toLocaleString('en-IN');}}
function card(k,v,cls){{return `<div class='card'><div class='k'>${{k}}</div><div class='v ${{cls||''}}'>${{v}}</div></div>`;}}
// Market Intelligence Core -- read-only. MIC_BRAINS defines, per brain,
// which fields to surface and (when the brain's key is entirely absent
// from s.intelligence -- e.g. no open position for Volatility/Premium/
// Greeks, or no live data feed wired yet for Liquidity/Structure/Event's
// VIX half) what to tell a human about why.
const MIC_BRAINS=[
 ['regime','Regime',['regime','confidence','data_quality'],'no spot candle history yet'],
 ['volatility','Volatility',['richness','iv_average','realized_vol','data_quality'],'no open position'],
 ['premium','Premium',['behavior','behavior_ratio','premium_captured_pct','data_quality'],'no open position'],
 ['greeks','Greeks',['exposure','position_delta','position_theta_per_day','data_quality'],'no open position'],
 ['liquidity','Liquidity',['tightness','combined_spread_pct','data_quality'],'bid/ask not yet wired into production'],
 ['structure','Structure',['proximity','resistance_strike','support_strike','data_quality'],'option-chain OI not yet wired into production'],
 ['event','Event',['expiry_proximity','vix_regime','data_quality'],'VIX quote not yet wired into production'],
 ['behaviour','Behaviour',['streak_signal','win_rate','total_trades','data_quality'],'fewer real trades on file than the required minimum'],
];
function micCard(label,fields,reading,unavailableHint){{
 if(!reading){{
  return `<div class='card'><div class='k'>${{label}}</div><div class='v warn'>NOT AVAILABLE</div>`+
   `<div class='k'>${{unavailableHint}}</div></div>`;
 }}
 const dq=reading.data_quality;
 const rows=fields.map(f=>`<div class='k'>${{f}}: <span style='color:#e6e6e6'>${{reading[f]==null?'-':reading[f]}}</span></div>`).join('');
 return `<div class='card'><div class='k'>${{label}} ${{dq==='SUFFICIENT'?'':'<span class="warn">('+dq+')</span>'}}</div>`+
  `<div class='v' style='font-size:13px'>${{rows}}</div></div>`;
}}
function renderOps(ops){{
 ops=ops||{{}};
 const state=ops.health_state||'-';
 const color={{HEALTHY:'#16351f',WARNING:'#3a2f16',DEGRADED:'#3a2f16',CRITICAL:'#3a1616',OFFLINE:'#3a1616'}}[state]||'#12151c';
 document.getElementById('ops_banner').innerHTML=
  `<div class='card' style='background:${{color}}'><div class='v'>${{state}}</div>`+
  `<div class='k'>${{(ops.reasons||[]).join(', ')||'no active signals'}} — as of ${{ops.as_of||'-'}}</div></div>`;
 document.getElementById('ops').innerHTML=[
  ['Uptime (s)', ops.uptime_seconds],
  ['Restarts/hr', ops.restart_count_last_hour, ops.restart_count_last_hour>=3?'warn':''],
  ['Auth Expired', ops.auth_expired, ops.auth_expired?'neg':''],
  ['Auth Expired Since', ops.auth_expired_since||'-'],
  ['Auth Expired Duration (s)', ops.auth_expired_duration_seconds==null?'-':Math.round(ops.auth_expired_duration_seconds)],
  ['Candle Age (s)', ops.candle_age_seconds==null?'-':Math.round(ops.candle_age_seconds)],
  ['WS Connected', ops.ws_connected, ops.ws_connected?'':'warn'],
  ['Memory (KB)', ops.memory_rss_kb, ops.memory_rss_kb>=500000?'warn':''],
  ['Disk Free %', ops.disk_free_pct, ops.disk_free_pct<=15?'warn':''],
  ['Exceptions/hr', ops.exception_count_last_hour, ops.exception_count_last_hour>=5?'warn':''],
  ['Journal Write OK', ops.journal_write_ok, ops.journal_write_ok?'':'neg'],
  ['Decision Journal OK', ops.decision_journal_write_ok, ops.decision_journal_write_ok?'':'neg'],
  ['Latest Decision ID', ops.latest_decision_id||'-'],
  ['Latest Trade ID', ops.latest_trade_id||'-'],
 ].map(([k,v,cls])=>card(k,v,cls)).join('');
}}
function renderMic(intel){{
 intel=intel||{{}};
 document.getElementById('mic').innerHTML=MIC_BRAINS.map(([key,label,fields,hint])=>
  micCard(label,fields,intel[key],hint)).join('');
}}
async function tick(){{
 const s=await (await fetch('/api/status')).json();
 document.getElementById('state').textContent=s.state+' | '+(s.healthy?'HEALTHY':'UNHEALTHY');

 // --- Priority 1: staleness indicator (server-computed age) ---
 const age=s.status_age_seconds;
 const stale=age!=null && age>STALE_AFTER;
 const sb=document.getElementById('staleness');
 if(stale){{
  sb.style.background='#3a1616';
  sb.innerHTML=`<b class='neg'>⚠ DASHBOARD STALE</b> — no update for ${{age}}s (threshold ${{STALE_AFTER}}s). `+
   `Last update ${{s.updated_at}}. The loop may be hung or the feed frozen — check journalctl.`;
 }}else{{
  sb.style.background='#12151c';
  sb.innerHTML=`Last update: ${{s.updated_at}} &nbsp;|&nbsp; ${{age==null?'-':age+'s'}} ago `+
   `<span class='k'>(stale after ${{STALE_AFTER}}s)</span>`;
 }}

 const mtm=s.mtm==null?'-':s.mtm;
 const cls=(s.mtm||0)>=0?'pos':'neg';
 document.getElementById('cards').innerHTML=[
  ['Spot',s.spot],['VWAP',s.vwap],['ORB High',s.orb_high],['ORB Low',s.orb_low],
  ['Direction',s.direction||'-'],['Position',s.position_symbol||'-'],
  ['Entry',s.entry_premium||'-'],['LTP',s.current_premium||'-'],
  ['Decision',s.last_decision||'-'],['Reason',s.last_reason||'-'],
  ['Health',s.health_detail]
 ].map(([k,v])=>card(k,v)).join('')
 +card('MTM',mtm,cls);

 // --- Priority 3: MTM vs configured limits ---
 const curMtm=(s.tick_mtm!=null)?s.tick_mtm:s.mtm;   // prefer live tick MTM
 const stop=-Math.abs(STOP_LOSS);
 let risk=[card('Current MTM',inr(curMtm),(curMtm||0)>=0?'pos':'neg'),
           card('Stop Loss',inr(stop),'neg')];
 if(curMtm!=null){{
  const remaining=curMtm-stop;   // room (₹) before the stop triggers
  risk.push(card('Remaining to stop',inr(remaining),remaining>0?'pos':'neg'));
 }}
 if(PROFIT_TARGET!=null){{
  risk.push(card('Profit target',inr(PROFIT_TARGET),'pos'));
  if(curMtm!=null) risk.push(card('To target',inr(PROFIT_TARGET-curMtm)));
 }}
 document.getElementById('risk').innerHTML=risk.join('');

 // --- Priority 2: expose already-present-but-hidden fields ---
 document.getElementById('sysauth').innerHTML=[
  card('Auth', s.auth_expired?'TOKEN EXPIRED':'ok', s.auth_expired?'neg':''),
  card('Clock trusted', s.clock_trusted?'yes':'NO', s.clock_trusted?'':'neg'),
  card('Clock drift', s.clock_drift_detail||'-', s.clock_drift_detail?'warn':''),
  card('Dup candles ignored', s.duplicate_candles_ignored, s.duplicate_candles_ignored?'warn':''),
  card('Last candle gap (s)', s.last_candle_gap_seconds==null?'-':s.last_candle_gap_seconds,
       s.last_candle_gap_seconds?'warn':''),
 ].join('');

 // Tick/WebSocket health + Priority 4: candle freshness.
 const tickCls=(s.tick_mtm||0)>=0?'pos':'neg';
 document.getElementById('tick_health').innerHTML=[
  card('WS Connected', s.ws_connected?'yes':'no', s.ws_connected?'':'neg'),
  card('Reconnect count', s.ws_connect_count),
  card('Last tick age (s)', s.ws_last_tick_age_seconds==null?'-':s.ws_last_tick_age_seconds),
  card('Tick MTM', s.tick_mtm==null?'-':s.tick_mtm, tickCls),
  card('Tick decision', s.tick_last_decision||'-'),
  card('Last candle', s.last_candle_ts||'-'),
  card('Candle age (s)', s.candle_age_seconds==null?'-':s.candle_age_seconds,
       (s.candle_age_seconds!=null && s.candle_age_seconds>STALE_AFTER)?'warn':''),
 ].join('');

 // Capital Management Engine section.
 const cap=s.capital_health;
 if(cap){{
  const color=cap.status==='SAFE'?'#16351f':(cap.status==='WARNING'?'#3a2f16':'#3a1616');
  document.getElementById('capital_banner').innerHTML=
   `<div class='card' style='background:${{color}}'><div class='v'>${{cap.status}}</div>`+
   `<div class='k'>${{cap.reason}} — as of ${{cap.timestamp}}</div></div>`;
  const snap=cap.snapshot||{{}}; const marg=cap.margin||{{}};
  document.getElementById('capital').innerHTML=[
   ['Account Equity', inr(snap.account_equity)],['Available Margin', inr(snap.available_margin)],
   ['Margin Required/lot', inr(marg.margin_per_lot)],['Margin Verified?', marg.verified],
   ['Safety Buffer', (cap.safety_buffer*100).toFixed(0)+'%'],
   ['Maximum Safe Lots', cap.maximum_safe_lots],['Configured Max Lots', cap.configured_max_lots],
   ['Approved Lots', cap.approved_lots],
   ['Capital Utilization', cap.capital_utilization==null?'-':(cap.capital_utilization*100).toFixed(1)+'%'],
   ['Remaining Margin', inr(cap.remaining_margin)],
  ].map(([k,v])=>card(k,v)).join('');
 }}

 // Premium VWAP Health section — this strategy's actual live indicator
 // (equal-weight combined-premium VWAP), not the unused spot-index VWAP.
 const mdh=s.market_data_health;
 if(mdh){{const q=mdh.quality||{{}};
  const ready=q.ready;
  const color=ready?'#16351f':'#12151c';
  const label=ready?'PREMIUM VWAP TRACKING':'AWAITING ENTRY (09:20)';
  document.getElementById('mdh_banner').innerHTML=
   `<div class='card' style='background:${{color}}'><div class='v'>${{label}}</div>`+
   `<div class='k'>as of ${{mdh.timestamp}}</div></div>`;
  document.getElementById('mdh').innerHTML=[
   ['Premium VWAP',q.value],['Candles folded in',q.candles_used],['Ready',q.ready],
   ['Strategy state',mdh.strategy_state],['Trade state',mdh.trade_state],['Decision',mdh.decision]
  ].map(([k,v])=>card(k,v)).join('');
  const hist=s.vwap_audit_history||[];
  if(hist.length){{document.getElementById('mdh_history').innerHTML='<table><tr>'+
   ['time','state','trade','decision','vwap','candles','ready'].map(h=>`<th>${{h}}</th>`).join('')+'</tr>'+
   hist.slice(-40).reverse().map(r=>{{const q=r.quality;return '<tr>'+
    [r.timestamp,r.strategy_state,r.trade_state,r.decision,q.value,q.candles_used,q.ready]
    .map(v=>`<td>${{v}}</td>`).join('')+'</tr>';}}).join('')+'</table>';}}
 }}
 renderOps(s.ops);
 renderMic(s.intelligence);
 document.getElementById('logs').textContent=(s.recent_logs||[]).slice(-60).reverse().join('\\n');
 const t=await (await fetch('/api/trades')).json();
 if(t.length){{const cols=Object.keys(t[0]);
  document.getElementById('trades').innerHTML='<table><tr>'+cols.map(c=>`<th>${{c}}</th>`).join('')+
  '</tr>'+t.map(r=>'<tr>'+cols.map(c=>`<td>${{r[c]}}</td>`).join('')+'</tr>').join('')+'</table>';}}
}}
tick();
</script></body></html>"""


class DashboardServer:
    """Runs the dashboard HTTP server on a daemon thread."""

    def __init__(self, status: RuntimeStatus, journal: TradeJournal,
                 host: str, port: int, refresh: int, logger: logging.Logger,
                 stale_after: int = 420, stop_loss: float = 0.0,
                 profit_target: Optional[float] = None):
        self._status = status
        self._journal = journal
        self._host = host
        self._port = port
        self._refresh = refresh
        self._log = logger
        self._stale_after = stale_after
        self._stop_loss = stop_loss
        self._profit_target = profit_target
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _status_payload(self) -> dict:
        """Status dict plus SERVER-computed freshness ages (tz-robust)."""
        d = dict(self._status.__dict__)  # shallow copy; never mutate the source
        # Status age: updated_at is naive-local (set via datetime.now()), so
        # compare against a naive-local now — correct delta on any server TZ.
        try:
            ua = datetime.fromisoformat(str(d.get("updated_at")))
            d["status_age_seconds"] = round((datetime.now() - ua).total_seconds(), 1)
        except (TypeError, ValueError):
            d["status_age_seconds"] = None
        # Candle age: last_candle_ts is tz-aware IST — compare against IST now.
        lct = d.get("last_candle_ts")
        if lct:
            try:
                d["candle_age_seconds"] = round(
                    (now_ist() - datetime.fromisoformat(str(lct))).total_seconds(), 1)
            except (TypeError, ValueError):
                d["candle_age_seconds"] = None
        else:
            d["candle_age_seconds"] = None
        return d

    def start(self) -> None:
        server = self
        journal = self._journal
        page = _PAGE.format(
            refresh=self._refresh,
            stale_after=self._stale_after,
            stop_loss=self._stop_loss,
            profit_target=("null" if self._profit_target is None
                           else self._profit_target),
        )

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # Silence default stderr noise.
                return

            def do_GET(self):  # noqa: N802
                if self.path == "/api/status":
                    self._json(server._status_payload())  # noqa: SLF001
                elif self.path == "/api/trades":
                    self._json(journal.all_trades())
                else:
                    body = page.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(body)

            def _json(self, payload):
                data = json.dumps(payload, default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._log.info("Dashboard at http://%s:%d", self._host, self._port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()

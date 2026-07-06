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
<title>Bujji ORB-VWAP ATM Seller</title>
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
<h1>Bujji ORB-VWAP ATM Seller &nbsp; <span class='state' id='state'></span></h1>
<div id='staleness' class='stalebar'></div>
<div class='grid' id='cards'></div>
<h3>Risk / MTM</h3>
<div class='grid' id='risk'></div>
<h3>System &amp; Auth</h3>
<div class='grid' id='sysauth'></div>
<h3>Tick / WebSocket &amp; Candle Health</h3>
<div class='grid' id='tick_health'></div>
<h3>Market Data Health</h3>
<div id='mdh_banner'></div>
<div class='grid' id='mdh'></div>
<details><summary>VWAP audit history</summary><div id='mdh_history'></div></details>
<h3>Today's Logs</h3><pre id='logs'></pre>
<h3>Trade History</h3><div id='trades'></div>
<script>
const STALE_AFTER={stale_after};
const STOP_LOSS={stop_loss};
const PROFIT_TARGET={profit_target};
function inr(n){{return n==null?'-':'₹ '+Number(n).toLocaleString('en-IN');}}
function card(k,v,cls){{return `<div class='card'><div class='k'>${{k}}</div><div class='v ${{cls||''}}'>${{v}}</div></div>`;}}
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

 // Market Data Health section.
 const mdh=s.market_data_health;
 if(mdh){{const q=mdh.quality||{{}};
  const ok=q.is_real;const warn=q.using_fallback;
  const color=ok?'#16351f':(warn?'#3a2f16':'#3a1616');
  const label=ok?'REAL VOLUME VWAP':(warn?'FALLBACK (approx) — '+q.fallback_reason:'VWAP UNRELIABLE — '+q.fallback_reason);
  const perm=q.trading_permitted?'trading permitted':'TRADING DISABLED';
  document.getElementById('mdh_banner').innerHTML=
   `<div class='card' style='background:${{color}}'><div class='v'>${{label}}</div>`+
   `<div class='k'>${{perm}} — as of ${{mdh.timestamp}}</div></div>`;
  document.getElementById('mdh').innerHTML=[
   ['VWAP',q.value],['Candles used',q.candles_used],['Cumulative volume',q.cumulative_volume],
   ['Real volume?',q.is_real],['Using fallback?',q.using_fallback],
   ['Fallback reason',q.fallback_reason||'-'],['Trading permitted',q.trading_permitted],
   ['Strategy state',mdh.strategy_state],['Trade state',mdh.trade_state],['Decision',mdh.decision]
  ].map(([k,v])=>card(k,v)).join('');
  const hist=s.vwap_audit_history||[];
  if(hist.length){{document.getElementById('mdh_history').innerHTML='<table><tr>'+
   ['time','state','trade','decision','vwap','candles','cum_vol','real','fallback'].map(h=>`<th>${{h}}</th>`).join('')+'</tr>'+
   hist.slice(-40).reverse().map(r=>{{const q=r.quality;return '<tr>'+
    [r.timestamp,r.strategy_state,r.trade_state,r.decision,q.value,q.candles_used,q.cumulative_volume,q.is_real,q.fallback_reason||'-']
    .map(v=>`<td>${{v}}</td>`).join('')+'</tr>';}}).join('')+'</table>';}}
 }}
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

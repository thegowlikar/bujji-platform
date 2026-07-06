"""Lightweight read-only local dashboard.

Serves a single auto-refreshing HTML page plus a JSON status endpoint using
only the Python standard library (no framework dependency). It observes the
shared :class:`RuntimeStatus` and the :class:`TradeJournal`; it never mutates
trading state.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..core.runtime_status import RuntimeStatus
from ..journal.journal import TradeJournal

_PAGE = """<!doctype html><html><head><meta charset='utf-8'>
<title>Bujji ORB-VWAP ATM Seller</title>
<meta http-equiv='refresh' content='{refresh}'>
<style>
body{{font-family:system-ui,Arial;margin:24px;background:#0f1116;color:#e6e6e6}}
h1{{font-size:20px}} .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.card{{background:#1a1d26;border:1px solid #2a2f3a;border-radius:10px;padding:14px}}
.k{{color:#8b93a7;font-size:12px}} .v{{font-size:20px;margin-top:4px}}
.pos{{color:#4ade80}} .neg{{color:#f87171}} table{{width:100%;border-collapse:collapse;margin-top:10px}}
td,th{{border-bottom:1px solid #2a2f3a;padding:6px;font-size:12px;text-align:left}}
pre{{background:#12151c;padding:10px;border-radius:8px;max-height:240px;overflow:auto;font-size:11px}}
.state{{display:inline-block;padding:4px 10px;border-radius:6px;background:#2563eb}}
</style></head><body>
<h1>Bujji ORB-VWAP ATM Seller &nbsp; <span class='state' id='state'></span></h1>
<div class='grid' id='cards'></div>
<h3>Tick / WebSocket Health</h3>
<div class='grid' id='tick_health'></div>
<h3>Market Data Health</h3>
<div id='mdh_banner'></div>
<div class='grid' id='mdh'></div>
<details><summary>VWAP audit history</summary><div id='mdh_history'></div></details>
<h3>Today's Logs</h3><pre id='logs'></pre>
<h3>Trade History</h3><div id='trades'></div>
<script>
async function tick(){{
 const s=await (await fetch('/api/status')).json();
 document.getElementById('state').textContent=s.state+' | '+(s.healthy?'HEALTHY':'UNHEALTHY');
 const mtm=s.mtm==null?'-':s.mtm;
 const cls=(s.mtm||0)>=0?'pos':'neg';
 document.getElementById('cards').innerHTML=[
  ['Spot',s.spot],['VWAP',s.vwap],['ORB High',s.orb_high],['ORB Low',s.orb_low],
  ['Direction',s.direction||'-'],['Position',s.position_symbol||'-'],
  ['Entry',s.entry_premium||'-'],['LTP',s.current_premium||'-'],
  ['Decision',s.last_decision||'-'],['Reason',s.last_reason||'-'],
  ['Health',s.health_detail]
 ].map(([k,v])=>`<div class='card'><div class='k'>${{k}}</div><div class='v'>${{v}}</div></div>`).join('')
 +`<div class='card'><div class='k'>MTM</div><div class='v ${{cls}}'>${{mtm}}</div></div>`;
 // Tick/Health Engine — independent of the candle-driven fields above.
 const tickCls=(s.tick_mtm||0)>=0?'pos':'neg';
 document.getElementById('tick_health').innerHTML=[
  ['WS Connected',s.ws_connected?'yes':'no'],
  ['Reconnect count',s.ws_connect_count],
  ['Last tick age (s)',s.ws_last_tick_age_seconds==null?'-':s.ws_last_tick_age_seconds],
  ['Tick decision',s.tick_last_decision||'-'],
 ].map(([k,v])=>`<div class='card'><div class='k'>${{k}}</div><div class='v'>${{v}}</div></div>`).join('')
 +`<div class='card'><div class='k'>Tick MTM</div><div class='v ${{tickCls}}'>${{s.tick_mtm==null?'-':s.tick_mtm}}</div></div>`;
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
  ].map(([k,v])=>`<div class='card'><div class='k'>${{k}}</div><div class='v'>${{v}}</div></div>`).join('');
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
                 host: str, port: int, refresh: int, logger: logging.Logger):
        self._status = status
        self._journal = journal
        self._host = host
        self._port = port
        self._refresh = refresh
        self._log = logger
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        status, journal, refresh = self._status, self._journal, self._refresh

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # Silence default stderr noise.
                return

            def do_GET(self):  # noqa: N802
                if self.path == "/api/status":
                    self._json(status.__dict__)
                elif self.path == "/api/trades":
                    self._json(journal.all_trades())
                else:
                    body = _PAGE.format(refresh=refresh).encode()
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

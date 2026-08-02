# Bujji System Ownership

This repository contains **two unrelated trading systems** plus one
abandoned intermediate generation. This document is the canonical map of
which files belong to which system, and — critically — which files are
**shared infrastructure that must never be deleted or modified** when
cleaning up either system.

Verified by direct import/dependency audit (grep + manual call-path tracing)
against commit `a4a6220` on branch `v1.0-shadow`. Re-verify this document if
either system's structure changes materially.

## The three generations

1. **Legacy ORB-VWAP ATM Seller** — the original, previously-deployed
   strategy bot. **DEPRECATED.** Systemd unit: `bujji-orb-vwap-legacy.service`
   (renamed from the ambiguous `bujji.service`; disabled by default, not
   started automatically).
2. **"Options OS v3" Series 31–54** — an earlier, already-abandoned attempt
   at the current product. Expects an external MIC v2 process
   (`/opt/bujji-mic-v2/`, a separate repo/venv) that is not part of this
   process. Not connected to generation 3. No disposition decision has been
   made on this generation yet — do not assume it is safe to delete without
   a separate audit (`qualification/historical_runner.py` still references
   it as of this writing).
3. **Bujji Options OS** (the current product) — regime-aware, defined-risk
   options-selling architecture: Trading Brain, Risk Governor (D.1–D.6),
   MSI Trade Construction, Position Lifecycle, Trade Lifecycle Execution,
   Shadow Session Controller, Trading Session Governor, Shadow Observatory.
   No automated entrypoint exists yet — reserved future systemd unit name:
   **`bujji-options-os.service`** (not yet created; create only once a real
   automated driver/entrypoint exists — do not stand up an empty unit).

## Legacy ORB-VWAP ownership

| Concern | Files |
|---|---|
| Entrypoint | `bujji/app.py` |
| Systemd unit | `bujji-orb-vwap-legacy.service` (disabled by default) |
| Deployment template | `deploy/bujji-orb-vwap-legacy.service`, `deploy/README.md` (renamed from `deploy/bujji.service` — the original template documented `cp deploy/bujji.service /etc/systemd/system/bujji.service` + `systemctl enable --now bujji.service`, which would have recreated the exact ambiguous, auto-starting unit name; found and fixed during the pre-commit operational safety check) |
| Configuration | `config/config.yaml`, `bujji/core/config.py::AppConfig` |
| State handling | `bujji/core/orchestrator.py`, `bujji/core/session_state.py` |
| Strategy logic | `bujji/signal/engine.py`, `bujji/signal/indicators.py`, `bujji/core/thesis.py` |
| Execution logic | `bujji/execution/engine.py` |
| Position tracking | `bujji/trade/manager.py` |
| Capital layer | `bujji/capital/engine.py`, `bujji/capital/policy.py` |
| Journal | `bujji/journal/journal.py` (`TradeJournal`) |
| Tick/health | `bujji/tick/engine.py`, `bujji/tick/health.py` |
| Dashboard | `bujji/dashboard/server.py` |
| Broker glue | `bujji/broker/factory.py`, `bujji/broker/fyers_ws.py` |
| Tests | 31 files under `tests/` referencing ORB/VWAP (e.g. `test_vwap.py`, `test_trade_manager.py`, `test_signal_engine.py`) |

## Bujji Options OS ownership

| Concern | Files |
|---|---|
| Entrypoint | **None yet** — no automated driver exists. Reserved unit name: `bujji-options-os.service`. |
| Runtime controller | `bujji/production_runtime/trading_brain_runtime.py`, `shadow_session_controller.py`, `trading_session_governor/session_governor.py` |
| Strategy selection | `bujji/msi_trade_construction/engine.py` + `taxonomy.py`, `trading_session_governor/strategy_selector.py` |
| Risk pipeline | `bujji/trading_brain/risk_governor/*` (D.1–D.6) |
| Execution layer | `bujji/production_runtime/trade_lifecycle_executor.py`, `lifecycle_order_builder.py` |
| Lifecycle management | `bujji/production_runtime/position_lifecycle_runtime.py`, `position_reality_registry.py` |
| Observability | `bujji/shadow_observatory/*` |
| Tests | `tests/production_runtime/*`, `tests/test_trade_lifecycle_executor.py`, `tests/test_trading_brain_runtime.py`, and the rest of the F.0–V1.1 test suite |

## Shared infrastructure — DO NOT DELETE, DO NOT MOVE, DO NOT MODIFY as part of ORB-VWAP cleanup

Both systems import these directly. Verified: deleting or relocating any of
these breaks Bujji Options OS immediately.

| File | What's shared | Proof |
|---|---|---|
| `bujji/broker/paper.py` (`PaperBroker`) | Sole execution broker for both systems | ORB-VWAP: `broker/factory.py` constructs it. Options OS: constructed directly throughout `production_runtime/` and every F.1–F.5 test. |
| `bujji/broker/base.py` (`Broker` ABC) | Abstract broker interface both `PaperBroker` and `FyersBroker` implement | Options OS's own F.1 docstring calls this out explicitly as the shared ABC. |
| `bujji/core/event_bus.py` (`EventBus`, `Event`, `EventType`) | The event bus and its 7-member `EventType` enum | 9+ files under `production_runtime/` import it directly (`trade_lifecycle_executor.py`, `trading_brain_runtime.py`, `session_governor.py`, etc.); ORB-VWAP's `bujji/app.py` imports it directly too. Options OS's "zero new EventType members" discipline throughout this project was reusing THIS legacy enum, not a fresh one. |
| `bujji/core/enums.py` (`Side`, `OptionType`, `OrderStatus`) | Broker-facing vocabulary both systems trade options with | Imported throughout both systems' order/contract code. |
| `bujji/core/models.py` — **specifically** `OptionContract`, `OrderRequest`, `OrderResult` | Order/contract data shapes | Confirmed imports from Options OS: `trade_lifecycle_executor.py`, `trading_brain_runtime.py`, `lifecycle_order_builder.py`, `msi_entry_bridge.py`, `margin_calibration_runner.py`, `broker_margin_reality_adapter.py` (7 call sites). |

**Important nuance on `core/models.py`**: this single file also contains
ORB-VWAP-only classes (`Signal`, `OpeningRange`, `Position`, `TradeIntention`)
living alongside the shared classes above. If this file is ever split or
moved, `OptionContract`/`OrderRequest`/`OrderResult` must go wherever Options
OS can still reach them — do not treat the whole file as "ORB-VWAP's" just
because it also contains ORB-VWAP-specific classes.

## Dependency proof (grep-verified, re-run anytime to re-confirm)

```bash
# Confirm zero references from Options OS into ORB-VWAP-only modules:
grep -rln 'core\.orchestrator\|signal\.engine\|trade\.manager\|execution\.engine\|capital\.engine\|capital\.policy\|journal\.journal\b\|AppConfig' \
  bujji/production_runtime/ bujji/trading_brain/ bujji/msi_trade_construction/ bujji/shadow_observatory/ \
  --include='*.py' | grep -v __pycache__
# Expected: no output (confirmed at audit time).

# Confirm zero references from ORB-VWAP into Options OS:
grep -rln 'PositionGroupJournal\|msi_trade_construction\|production_runtime\|trading_brain\.' \
  bujji/core/ bujji/trade/ bujji/signal/ bujji/capital/ bujji/tick/ bujji/execution/ bujji/dashboard/ \
  --include='*.py' | grep -v __pycache__
# Expected: no output (confirmed at audit time; one prior grep hit was a
# docstring mention in core/process_lock.py, not a real import).
```

## Regression discipline

Any change to the shared infrastructure table above must be followed by a
full run of `tests/production_runtime/*` and `tests/test_trade_lifecycle_executor.py`
(these exercise every shared dependency directly) before being considered safe.

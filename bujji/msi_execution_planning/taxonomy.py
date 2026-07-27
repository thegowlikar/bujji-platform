"""Execution Planning Engine taxonomy — Series 98. Plain string
constants (house convention, never enum.Enum)."""
from __future__ import annotations

MSI_EXECUTION_PLANNING_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Execution mode -- only one is supported, and disclosed as such;
# production's own real ExecutionEngine already places CE/PE legs
# SEQUENTIALLY, never simultaneously (confirmed by reading
# bujji/core/orchestrator.py directly) -- this package generalizes
# that same real, confirmed discipline to N-leg constructions. --------
EXECUTION_MODE_SEQUENTIAL_STAGED = "SEQUENTIAL_STAGED"

# --- Deliverable 4: failure types -----------------------------------------
FAILURE_PARTIAL_FILL = "PARTIAL_FILL"
FAILURE_REJECTED_ORDER = "REJECTED_ORDER"
FAILURE_TIMEOUT = "TIMEOUT"
FAILURE_EXCHANGE_HALT = "EXCHANGE_HALT"
FAILURE_BROKER_DISCONNECT = "BROKER_DISCONNECT"
FAILURE_STALE_QUOTES = "STALE_QUOTES"
FAILURE_PRICE_DRIFT = "PRICE_DRIFT"
FAILURE_CANCELLED_ORDER = "CANCELLED_ORDER"

ALL_FAILURE_TYPES = (
    FAILURE_PARTIAL_FILL, FAILURE_REJECTED_ORDER, FAILURE_TIMEOUT, FAILURE_EXCHANGE_HALT,
    FAILURE_BROKER_DISCONNECT, FAILURE_STALE_QUOTES, FAILURE_PRICE_DRIFT, FAILURE_CANCELLED_ORDER,
)

# --- Deliverable 5: validation gates --------------------------------------
GATE_MARKET_OPEN = "MARKET_OPEN"
GATE_POSITION_STILL_VALID = "POSITION_STILL_VALID"
GATE_THESIS_STILL_VALID = "THESIS_STILL_VALID"
GATE_MARGIN_STILL_SUFFICIENT = "MARGIN_STILL_SUFFICIENT"
GATE_EXECUTION_WINDOW = "EXECUTION_WINDOW"
GATE_DUPLICATE_PROTECTION = "DUPLICATE_PROTECTION"

ALL_VALIDATION_GATES = (
    GATE_MARKET_OPEN, GATE_POSITION_STILL_VALID, GATE_THESIS_STILL_VALID,
    GATE_MARGIN_STILL_SUFFICIENT, GATE_EXECUTION_WINDOW, GATE_DUPLICATE_PROTECTION,
)

# --- Estimated latency (qualitative, structural label -- no real network
# latency data exists anywhere in this replay arc) -------------------------
LATENCY_LOW = "LOW"
LATENCY_MODERATE = "MODERATE"
LATENCY_HIGH = "HIGH"

ALL_LATENCY_LEVELS = (LATENCY_LOW, LATENCY_MODERATE, LATENCY_HIGH)

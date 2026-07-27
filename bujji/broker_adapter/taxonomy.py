"""Broker Adapter vocabulary — BUJJI Options OS v3, Engineering Series
40, Sprint 1 (FYERS v1).

This package lives OUTSIDE the Trading Brain (`bujji/broker_adapter/`,
not `bujji/trading_brain/...`) -- it is the first production-facing
integration layer, translating a broker-neutral
`ExecutionInstructionSet` into a broker-specific request description.
It never connects to a broker, never authenticates, never submits an
order -- see docs/BROKER_ADAPTER_ARCHITECTURE.md for the full
runtime-separation rationale.

Three finite vocabularies belong to this module: `execution_status`
(the overall translation verdict), `translation_status` (per-action),
and `failure_reason`. Nothing here is a broker credential, a REST
payload, or an order field.
"""
from __future__ import annotations

BROKER_ADAPTER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Supported Brokers -- closed registry. Only FYERS is wired this
# sprint ("FYERS v1"); ZERODHA is named here purely so a future
# adapter registration uses the identical constant, never a
# freshly-typed string prone to typos -- it has no entry in
# engine.py::ADAPTER_REGISTRY yet and is therefore unreachable through
# translate() until a future series adds one.
# ---------------------------------------------------------------------------
BROKER_FYERS = "FYERS"
BROKER_ZERODHA = "ZERODHA"

ALL_SUPPORTED_BROKERS = (BROKER_FYERS,)
ALL_DECLARED_BROKERS = (BROKER_FYERS, BROKER_ZERODHA)

# ---------------------------------------------------------------------------
# Execution Status -- the overall translation verdict for one request.
# ---------------------------------------------------------------------------
EXECUTION_STATUS_VERSION = "1.0.0"

EXECUTION_STATUS_UNKNOWN = "UNKNOWN"
EXECUTION_STATUS_TRANSLATED = "TRANSLATED"
EXECUTION_STATUS_PARTIALLY_TRANSLATED = "PARTIALLY_TRANSLATED"
EXECUTION_STATUS_BLOCKED = "BLOCKED"
EXECUTION_STATUS_FAILED = "FAILED"

ALL_EXECUTION_STATUSES = (
    EXECUTION_STATUS_UNKNOWN,
    EXECUTION_STATUS_TRANSLATED,
    EXECUTION_STATUS_PARTIALLY_TRANSLATED,
    EXECUTION_STATUS_BLOCKED,
    EXECUTION_STATUS_FAILED,
)

EXECUTION_STATUS_DESCRIPTIONS = {
    EXECUTION_STATUS_UNKNOWN: "Translation status could not be determined.",
    EXECUTION_STATUS_TRANSLATED: "Every abstract action was successfully translated into a broker operation description.",
    EXECUTION_STATUS_PARTIALLY_TRANSLATED: "At least one abstract action translated successfully, and at least one did not.",
    EXECUTION_STATUS_BLOCKED: "The instruction set itself was BLOCKED upstream; the adapter honestly relays that, translating nothing.",
    EXECUTION_STATUS_FAILED: "No abstract action could be translated.",
}

# ---------------------------------------------------------------------------
# Translation Status -- per-action verdict.
# ---------------------------------------------------------------------------
TRANSLATION_STATUS_VERSION = "1.0.0"

TRANSLATION_STATUS_TRANSLATED = "TRANSLATED"
TRANSLATION_STATUS_PENDING = "PENDING"
TRANSLATION_STATUS_FAILED = "FAILED"

ALL_TRANSLATION_STATUSES = (
    TRANSLATION_STATUS_TRANSLATED,
    TRANSLATION_STATUS_PENDING,
    TRANSLATION_STATUS_FAILED,
)

TRANSLATION_STATUS_DESCRIPTIONS = {
    TRANSLATION_STATUS_TRANSLATED: "This action was mapped to a named broker operation.",
    TRANSLATION_STATUS_PENDING: "This action makes no broker call by design -- it hands off to a future Runtime Execution Service and waits.",
    TRANSLATION_STATUS_FAILED: "This action could not be mapped to any known broker operation.",
}

# ---------------------------------------------------------------------------
# Failure Reason -- finite, closed. Never free text.
# ---------------------------------------------------------------------------
FAILURE_REASON_VERSION = "1.0.0"

FAILURE_REASON_UNKNOWN_ACTION = "UNKNOWN_ACTION"
FAILURE_REASON_UNSUPPORTED_BROKER = "UNSUPPORTED_BROKER"
FAILURE_REASON_INVALID_REQUEST = "INVALID_REQUEST"
FAILURE_REASON_MISSING_FIELD = "MISSING_FIELD"
FAILURE_REASON_ADAPTER_CONFIGURATION_ERROR = "ADAPTER_CONFIGURATION_ERROR"

ALL_FAILURE_REASONS = (
    FAILURE_REASON_UNKNOWN_ACTION,
    FAILURE_REASON_UNSUPPORTED_BROKER,
    FAILURE_REASON_INVALID_REQUEST,
    FAILURE_REASON_MISSING_FIELD,
    FAILURE_REASON_ADAPTER_CONFIGURATION_ERROR,
)

FAILURE_REASON_DESCRIPTIONS = {
    FAILURE_REASON_UNKNOWN_ACTION: "The instruction set named an abstract action this adapter's translation table does not recognize.",
    FAILURE_REASON_UNSUPPORTED_BROKER: "The requested broker is not registered with this adapter.",
    FAILURE_REASON_INVALID_REQUEST: "The supplied ExecutionInstructionSet is itself unusable (missing, or its own status is UNKNOWN).",
    FAILURE_REASON_MISSING_FIELD: "A field this adapter requires to identify the request (e.g. plan_id) was absent.",
    FAILURE_REASON_ADAPTER_CONFIGURATION_ERROR: "Declared for taxonomy completeness -- this adapter has no external configuration to misconfigure this sprint (see config.py); reserved for a future series that adds one.",
}

# `ADAPTER_CONFIGURATION_ERROR` is declared but never produced by this
# sprint's engine.py -- there is no broker credential, endpoint, or
# environment setting for this pure-translation module to misconfigure
# yet. It exists so a future series that adds real adapter
# configuration (e.g. environment selection) never needs to invent a
# new failure vocabulary member.
UNREACHABLE_FAILURE_REASONS_THIS_SPRINT = (FAILURE_REASON_ADAPTER_CONFIGURATION_ERROR,)

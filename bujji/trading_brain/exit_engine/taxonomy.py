"""Exit Engine taxonomy — Exit Engine v1 sprint. Plain string
constants (house convention, never enum.Enum)."""
from __future__ import annotations

EXIT_REASON_MAXIMUM_LOSS = "MAXIMUM_LOSS"
EXIT_REASON_PROFIT_TARGET = "PROFIT_TARGET"
EXIT_REASON_HARD_TIME_EXIT = "HARD_TIME_EXIT"
EXIT_REASON_STRATEGY_EXIT = "STRATEGY_EXIT"
EXIT_REASON_NO_EXIT = "NO_EXIT"

ALL_EXIT_REASONS = (
    EXIT_REASON_MAXIMUM_LOSS,
    EXIT_REASON_PROFIT_TARGET,
    EXIT_REASON_HARD_TIME_EXIT,
    EXIT_REASON_STRATEGY_EXIT,
    EXIT_REASON_NO_EXIT,
)

# Rule evaluation priority (Part 3): capital protection first, then
# profit-taking, then a scheduled backstop, then the least specific
# rule last. Fixed and disclosed, never reordered silently -- if two
# rules would both trigger on the same valuation, this is the order
# that decides which reason is reported.
RULE_PRIORITY = (
    EXIT_REASON_MAXIMUM_LOSS,
    EXIT_REASON_PROFIT_TARGET,
    EXIT_REASON_HARD_TIME_EXIT,
    EXIT_REASON_STRATEGY_EXIT,
)

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"
ALL_CONFIDENCE_LEVELS = (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)

EXIT_ENGINE_VERSION = "1.0.0"

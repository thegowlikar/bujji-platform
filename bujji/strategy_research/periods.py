"""Phase 20.4 -- period split for train/validation/out-of-sample
stability checking. No existing equivalent found in this codebase
(audited directly). Deliberately trivial: a fixed, disclosed date
range per period, decided BEFORE looking at any result -- never fit
to the data. No threshold here is a strategy parameter; changing
these ranges would require a fresh audit disclosure, not a silent
edit.
"""
from __future__ import annotations

TRAIN = "TRAIN"
VALIDATION = "VALIDATION"
OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
UNASSIGNED = "UNASSIGNED"

TRAIN_START, TRAIN_END = "2018-01-01", "2022-12-31"
VALIDATION_START, VALIDATION_END = "2023-01-01", "2024-12-31"
OUT_OF_SAMPLE_START, OUT_OF_SAMPLE_END = "2025-01-01", "2026-12-31"


def period_for_date(date: str) -> str:
    """`date`: 'YYYY-MM-DD'. Pure string comparison -- ISO date strings
    sort lexicographically identically to chronological order, so no
    date parsing is needed or performed."""
    if TRAIN_START <= date <= TRAIN_END:
        return TRAIN
    if VALIDATION_START <= date <= VALIDATION_END:
        return VALIDATION
    if OUT_OF_SAMPLE_START <= date <= OUT_OF_SAMPLE_END:
        return OUT_OF_SAMPLE
    return UNASSIGNED

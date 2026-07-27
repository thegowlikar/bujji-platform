"""Performance Analytics & Edge Validation config — Series 101.

The ONE structural constant this package declares -- a disclosed,
standard statistical rule-of-thumb threshold, never fit to make any
particular result look better or worse. This is NOT a trading
parameter (it tunes nothing about strategy behaviour); it only decides
when a descriptive statistic is presented as reliable versus flagged
honestly as too small a sample to trust.
"""
from __future__ import annotations

MIN_RELIABLE_SAMPLE_SIZE = 30

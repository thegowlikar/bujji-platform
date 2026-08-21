"""Volatility Intelligence — BUJJI Options OS v3, Trading Brain
Intelligence Upgrade, Phase 2.

Composes `msi_volatility_structure` (VSB) and `mic_v0.
volatility_classifier` into one assessment answering "what is
volatility telling us?" -- never "should we sell premium?". See
engine.py for the full design rationale and field-by-field provenance.
"""
from .engine import assess
from .models import VolatilityIntelligenceAssessment
from . import taxonomy

__all__ = ["assess", "VolatilityIntelligenceAssessment", "taxonomy"]

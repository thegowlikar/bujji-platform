"""bujji.microstructure_intelligence.explain — Phase 20.27.

Human-readable rendering. No new logic -- reads fields already
computed by `classifier.py`, same convention as
`bujji.mic_context_bridge.explain.explain_market_understanding_context`.
"""
from __future__ import annotations

from .models import MicrostructureReading


def explain_microstructure_reading(reading: MicrostructureReading) -> str:
    lines = [f"MICROSTRUCTURE: {reading.state.value}."]

    if reading.reasons:
        lines.extend(reading.reasons)

    metrics = reading.supporting_metrics
    density_ratio = metrics.get("density_ratio")
    range_ratio = metrics.get("range_ratio")
    if density_ratio is not None:
        pct = (density_ratio - 1.0) * 100.0
        lines.append(f"Tick activity {'increased' if pct >= 0 else 'decreased'} {abs(pct):.0f}%.")
    if range_ratio is not None:
        direction = "expanded" if range_ratio > 1.0 else "contracted" if range_ratio < 1.0 else "held steady"
        lines.append(f"Price range {direction}.")

    lines.append(f"Confidence: {reading.confidence:.0%}.")
    lines.append(f"Data quality: {reading.data_quality.value}.")
    lines.append(f"Observations used: {reading.observations_used}.")

    return " ".join(lines)

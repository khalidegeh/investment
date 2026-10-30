"""Deterministic fundamentals advisor.

Fetch fundamentals through a provider chain, score them against fixed
thresholds, and emit buy / hold / sell. No model-generated numbers anywhere
in the pipeline: a provider returns None when it cannot serve a symbol, and
the scorecard refuses to guess below minimum coverage.
"""

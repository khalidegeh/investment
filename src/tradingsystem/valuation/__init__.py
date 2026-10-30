"""Deterministic fair value ranges.

Three independent methods (DCF, P/E vs own history, analyst consensus) each
return a range with its assumptions attached. Nothing here fetches data or
produces a single "true price".
"""
from .fair_value import FairValue, combine
from .methods import MethodRange, consensus_range, dcf_range, pe_history_range

__all__ = [
    "FairValue",
    "MethodRange",
    "combine",
    "consensus_range",
    "dcf_range",
    "pe_history_range",
]

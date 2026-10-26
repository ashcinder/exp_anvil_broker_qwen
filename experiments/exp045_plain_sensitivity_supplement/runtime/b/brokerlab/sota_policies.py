"""Public baseline functions. Native rules and explicit adaptations: sota_native.py.

See experiments/exp033_sota_comparison/ALGORITHMS_CN.md for reproduction scope.
"""
from .tdr_policy import Transfer, compute_target, compute_transfers, percent_of
from .sota_native import (REVISION, _greedy_pair, rescue_plans, autoloop_requests,
                         autoloop_plans, loopmax_requests, loopmax_plans,
                         overpass_target, pnps_plan)

def uniform_level(actual):
    return sum(actual) // len(actual) if actual else 0

def uniform_target(actual, q_min=0.0, cap=0.0):
    return compute_target(list(actual), [1] * len(actual), q_min=q_min, cap=cap)

def level_plans(actual, level):
    return _greedy_pair([[i, level-v] for i,v in enumerate(actual) if v < level],
                        [[i, v-level] for i,v in enumerate(actual) if v > level])

def flexichain_plans(actual, pending=None):
    return rescue_plans(actual, pending or {})

def ucrb_plans(actual, pending):
    return rescue_plans(actual, pending)

def spider_plans(actual):
    return compute_transfers(actual, uniform_target(actual), 0., "excess_only")

def pnps_target(actual, demand, q_min=0.0):
    raise ValueError("PnP must be planned on disjoint training data before funding; use pnps_plan")

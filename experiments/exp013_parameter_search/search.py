"""Pure search space, strategy encoding and method-neutral Pareto selection."""
import hashlib
import json
import math
from dataclasses import replace
from itertools import product

METHODS = ("plain", "valve", "proportional", "topup", "ewma")


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


def candidates(exp, coarse=False):
    eps = exp["coarse_epsilons" if coarse else "epsilons"]
    windows = exp["coarse_windows" if coarse else "windows"]
    halves = exp["coarse_half_lives" if coarse else "half_lives"]
    out = [{"method": "plain"}]
    out += [{"method": "valve", "threshold": float(v)} for v in exp["valve_thresholds"]]
    for method in ("proportional", "topup", "ewma"):
        out += [{"method": method, "epsilon": float(e), "memory": int(w)}
                for e, w in product(eps, halves if method == "ewma" else windows)]
    return out


def arm(candidate, balance):
    method, balance = candidate["method"], float(balance)
    if method == "plain":
        return f"plain@{balance}", f"plain@{balance}"
    if method == "valve":
        token = f"valve@{balance}@{float(candidate['threshold'])}"
        return token, token
    eps = float(candidate["epsilon"])
    if method == "ewma":
        h = float(candidate["memory"])
        return f"topup@{balance}@@{eps}@@{h}", f"topup@{balance}@{eps}@{h}"
    prefix = "tdr" if method == "proportional" else "topup"
    return f"{prefix}@{balance}@@{eps}", f"{prefix}@{balance}@{eps}"


def trial_config(cfg, candidate, balance, workload, checksum, route_seed):
    token, _ = arm(candidate, balance)
    e = {**cfg.exp, "arms": token, "balances_eth": float(balance), "route_seed": route_seed,
         "prepared_workload_path": str(workload), "prepared_workload_sha256": checksum,
         "tdr_ewma_half_life": 0.0}
    # Each trial is a fresh exp003 process, so hard-window size is unambiguous
    # even though the legacy arm tag does not contain the window.
    if candidate["method"] in ("proportional", "topup"):
        e["tdr_window_blocks"] = int(candidate["memory"])
    if "epsilon" in candidate:
        e["tdr_epsilon"] = candidate["epsilon"]
        e["tdr_surplus_epsilon"] = candidate["epsilon"]
    return replace(cfg, exp=e, broker=replace(cfg.broker, initial_balance_eth=float(balance)))


def objectives(record):
    m = record["metrics"]
    return (m["relay_fraction"], m["tdr_legs_per_ctx"], -m["throughput_ctx_per_s"], m["e2e_p95_s"])


def pareto_layers(records):
    """All four metrics matter; never rank against EWMA's relative advantage."""
    remaining = sorted(records, key=lambda r: identity(r["candidate"]))
    layers = []
    while remaining:
        front = []
        for r in remaining:
            a = objectives(r)
            dominated = any(all(x <= y for x, y in zip(objectives(o), a))
                            and any(x < y for x, y in zip(objectives(o), a))
                            for o in remaining if o is not r)
            if not dominated:
                front.append(r)
        layers.append(front)
        ids = {identity(r["candidate"]) for r in front}
        remaining = [r for r in remaining if identity(r["candidate"]) not in ids]
    return layers


def shortlist(records, k):
    """Pareto rank, then normalized crowding distance for trade-off diversity."""
    chosen = []
    for front in pareto_layers(records):
        scores = {identity(r["candidate"]): 0.0 for r in front}
        for d in range(4):
            ordered = sorted(front, key=lambda r: (objectives(r)[d], identity(r["candidate"])))
            low, high = objectives(ordered[0])[d], objectives(ordered[-1])[d]
            if high == low:
                continue
            scores[identity(ordered[0]["candidate"])] = math.inf
            scores[identity(ordered[-1]["candidate"])] = math.inf
            for i in range(1, len(ordered) - 1):
                scores[identity(ordered[i]["candidate"])] += (
                    objectives(ordered[i + 1])[d] - objectives(ordered[i - 1])[d]) / (high - low)
        ordered = sorted(front, key=lambda r: (-scores[identity(r["candidate"])], identity(r["candidate"])))
        chosen.extend(ordered[:k - len(chosen)])
        if len(chosen) == k:
            break
    return chosen


def neighbors(exp, candidate):
    if candidate["method"] not in ("proportional", "topup", "ewma"):
        return []  # Valve's entire threshold grid already ran during coarse.
    eps = exp["epsilons"]
    memory = exp["half_lives" if candidate["method"] == "ewma" else "windows"]
    ei, wi = eps.index(candidate["epsilon"]), memory.index(candidate["memory"])
    return [{"method": candidate["method"], "epsilon": float(e), "memory": int(w)}
            for e, w in product(eps[max(0, ei - 1):ei + 2], memory[max(0, wi - 1):wi + 2])]


def coverage_probes(exp, method):
    """Exercise every marginal grid level even when it is far from a winner.

    This is not a Cartesian sweep. Fixed diagonal probes protect e.g. epsilon
    .25 and a one-block memory from being unreachable from coarse neighbors.
    """
    key = "half_lives" if method == "ewma" else "windows"
    eps = [e for e in exp["epsilons"] if e not in exp["coarse_epsilons"]]
    memory = [w for w in exp[key] if w not in exp["coarse_" + key]]
    n = max(len(eps), len(memory))
    if not n:
        return []
    eps = eps or exp["coarse_epsilons"]
    memory = memory or exp["coarse_" + key]
    return [{"method": method, "epsilon": float(eps[i % len(eps)]), "memory": int(memory[i % len(memory)])}
            for i in range(n)]

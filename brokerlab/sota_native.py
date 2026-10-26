"""Literature decisions; closed-pool execution is an explicit platform adaptation.

Amounts are wei. These functions do not implement PCN routing, swaps, or MPC.
"""
import math
from fractions import Fraction
from decimal import Decimal, localcontext, ROUND_CEILING
def _greedy_pair(deficit, surplus):
    ds = sorted([list(x) for x in deficit if x[1] > 0], key=lambda x: (-x[1], x[0]))
    ss = sorted([list(x) for x in surplus if x[1] > 0], key=lambda x: (-x[1], x[0]))
    plans = []
    i = j = 0
    while i < len(ds) and j < len(ss):
        amount = min(ds[i][1], ss[j][1])
        plans.append((ss[j][0], ds[i][0], amount))
        ds[i][1] -= amount
        ss[j][1] -= amount
        if ds[i][1] == 0:
            i += 1
        if ss[j][1] == 0:
            j += 1
    return plans

REVISION = "exp033-faithful-v2"


def rescue_plans(actual, pending):
    deficits = [[int(s), int(v)] for s, v in pending.items()
                if 0 <= int(s) < len(actual) and int(v) > 0]
    claimed = {s for s, _ in deficits}
    sources = [[i, int(v)] for i, v in enumerate(actual)
               if i not in claimed and v > 0]
    if sum(v for _, v in sources) < sum(v for _, v in deficits):
        return []  # do not burn a partial repair that cannot rescue the payment
    return _greedy_pair(deficits, sources)


def autoloop_requests(actual, capacity, low=.3, high=.7):
    if capacity <= 0 or not 0 <= low < high <= 1:
        raise ValueError("invalid Autoloop capacity/band")
    middle = int((low + high) / 2 * capacity)
    return [middle - int(b) if b < low * capacity or b > high * capacity
            else 0 for b in actual]


def autoloop_plans(actual, capacity, low=.3, high=.7):
    requests = autoloop_requests(actual, capacity, low, high)
    return _greedy_pair([[i, v] for i, v in enumerate(requests) if v > 0],
                        [[i, -v] for i, v in enumerate(requests) if v < 0])


def loopmax_requests(actual, net_rates, capacities, horizon, safety):
    if len(actual) != len(net_rates) or len(actual) != len(capacities):
        raise ValueError("Loopmax vector dimensions differ")
    if horizon <= 0 or safety < 0 or any(c <= 0 for c in capacities):
        raise ValueError("invalid Loopmax capacity/horizon")
    result = []
    for balance, rate, capacity in zip(actual, net_rates, capacities):
        remote = max(0, int(capacity) - int(balance))
        reserve = math.ceil(abs(rate) * safety)
        if rate < 0 and balance / -rate < horizon:
            result.append(max(0, remote - reserve))
        elif rate > 0 and remote / rate < horizon:
            result.append(-max(0, int(balance) - reserve))
        else:
            result.append(0)
    return result


def loopmax_plans(actual, outflow, inflow, q_min, effective_window_blocks,
                  horizon_blocks, safety_blocks, capacities=None):
    if capacities is None:
        raise ValueError("Loopmax requires explicit reference channel capacities")
    window = float(effective_window_blocks)
    if window <= 0 or len(outflow) != len(actual) or len(inflow) != len(actual):
        raise ValueError("invalid Loopmax demand/window")
    rates = [(i - o) / window for i, o in zip(inflow, outflow)]
    req = loopmax_requests(actual, rates, capacities, horizon_blocks, safety_blocks)
    recipients = [[i, v] for i, v in enumerate(req) if v > 0]
    # External swap reservoir is absent here: only non-draining internal accounts
    # donate; keep safety_blocks of gross outflow. Native requests stay inspectable.
    sources = [[i, min(-v if v < 0 else int(actual[i]),
                       max(0, int(actual[i]) - math.ceil(outflow[i] / window * safety_blocks)))]
               for i, v in enumerate(req) if v <= 0 and rates[i] >= 0]
    if not recipients and any(v < 0 for v in req):
        recipients = [[i, max(0, int(capacities[i]) - int(actual[i]))]
                      for i, v in enumerate(req) if v == 0]
        sources = [[i, -v] for i, v in enumerate(req) if v < 0]
    return _greedy_pair(recipients, sources)


def overpass_target(actual, demand, theta="uniform", q_min=0.0,
                    capacities=None, weights=None):
    """Periodic Alg.3: bounded weighted quadratic projection (KKT multiplier).

    q_min is deliberately unused: the original QP has no TDR reserve floor.
    """
    n, total = len(actual), sum(actual)
    if not n or total == 0:
        return list(actual)
    if any(v < 0 for v in actual) or len(demand) != n or theta not in ("uniform", "demand"):
        raise ValueError("invalid QP input")
    caps = [total] * n if capacities is None else list(capacities)
    weights = [1.] * n if weights is None else list(weights)
    if len(caps) != n or len(weights) != n or any(c < 0 for c in caps) or sum(caps) < total:
        raise ValueError("infeasible QP capacities")
    if any(w <= 0 or not math.isfinite(w) for w in weights):
        raise ValueError("QP weights must be finite and positive")
    if any(d < 0 for d in demand):
        raise ValueError("negative demand")
    target = ([Fraction(d * total, sum(demand)) for d in demand]
              if theta == "demand" and sum(demand) else [1 / n] * n)
    if theta == "uniform" or not sum(demand):
        target = [Fraction(total, n)] * n
    weights = [Fraction(str(w)) for w in weights]
    bound = [int(c) for c in caps]
    lo = min(2 * w * (t - c) for w, t, c in zip(weights, target, bound)) - 1
    hi = max(2 * w * t for w, t in zip(weights, target)) + 1
    for _ in range(int(total).bit_length() + 80):
        lam = (lo + hi) / 2
        x = [min(c, max(Fraction(0), t - lam / (2 * w)))
             for c, t, w in zip(bound, target, weights)]
        if sum(x) > total:
            lo = lam
        else:
            hi = lam
    result = [min(int(c), int(v)) for v, c in zip(x, caps)]
    residual = total - sum(result)
    # Rational arithmetic prevents loss of wei at 10**18 scale. Rounding is
    # the exact discrete convex marginal-cost allocation, at most n extra wei.
    if not 0 <= residual <= n:
        raise ArithmeticError("QP reconciliation residual outside rounding bound")
    while residual:
        eligible = [i for i in range(n) if result[i] < caps[i]]
        i = min(eligible, key=lambda i: (weights[i] * (2 * (result[i] - target[i]) + 1), i))
        result[i] += 1
        residual -= 1
    return result


def pnps_plan(samples, budget, outage=.05):
    """Separable one-hop uniBP Bernstein bound, then fixed-budget extension.

    Historical support bounds are an estimation assumption, not a guarantee for
    unseen regime changes. No biBP, multihop, routing optimization or fee model.
    """
    if not samples or not samples[0] or not 0 < outage < 1:
        raise ValueError("PnP requires nonempty independent training windows")
    n = len(samples[0])
    if any(len(row) != n or any(v < 0 for v in row) for row in samples):
        raise ValueError("invalid PnP training samples")
    minimum = []
    for i in range(n):
        values = [row[i] for row in samples]
        # Eq.(12): beta=(max-min)/2, eta+=normalized mean, sigma=1
        # is the Hoeffding MGF bound for a variable in [-1,1]. A sample
        # standard deviation is NOT a valid substitute for the MGF parameter.
        with localcontext() as context:
            context.prec = 80
            mean = Decimal(sum(values)) / len(values)
            beta = Decimal(max(values) - min(values)) / 2
            margin = (2 * (1 / Decimal(str(outage))).ln()).sqrt()
            reserve = int((mean + beta * margin).to_integral_value(rounding=ROUND_CEILING))
        minimum.append(min(max(values), reserve))
    if sum(minimum) > budget:
        raise ValueError(f"PnP infeasible: minimum {sum(minimum)} exceeds budget {budget}")
    extra, remainder = divmod(int(budget) - sum(minimum), n)
    target = [v + extra + (i < remainder) for i, v in enumerate(minimum)]
    return {"target": target, "minimum": minimum, "minimum_capital": sum(minimum),
            "budget": int(budget), "outage": outage, "windows": len(samples),
            "model": "uniBP-one-hop-Bernstein-fixed-budget",
            "status": "optimal-separable-conservative-uniBP-with-fixed-budget-extension",
            "support_assumption": "historical bounded demand; no guarantee under shift"}

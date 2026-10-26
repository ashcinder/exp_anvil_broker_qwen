"""Explicit wallet-local adaptations; none reproduces a complete PCN protocol."""
from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal, localcontext
from time import perf_counter_ns

from brokerlab.tdr_engine import TdrAgent

METHODS = {
    "brokerchain": "BrokerChain-core / No-Rebalance",
    "horcrux": "Horcrux flow-neutrality inspired (delayed wallet transfers)",
    "shaduf": "Shaduf AB-binding allocation adapted (without ++ cryptography)",
    "lai": "LAI(1), ICML 2024 Algorithm 2 (wallet-demand adapter)",
    "tdr": "TDR Top-up + EWMA (exp025/026 corrected execution)",
}


def lai_coefficients(curvature, gamma, horizon):
    """Exact scalar-A specialization of ICML 2024 Algorithm 2, PDF p.6.

    gamma=0 is Algorithm 1; gamma=1 is a stationary fixed point, for any T.
    Decimal precision protects the later integer-wei allocation.
    """
    with localcontext() as ctx:
        ctx.prec = 50
        a, g = Decimal(str(curvature)), Decimal(str(gamma))
        if a <= 0 or not 0 <= g <= 1 or horizon < 1:
            raise ValueError("Invalid LAI inputs")
        terminal = 1/(1+a+a/2*((1+4/a)**(g/2)-1))
        result = [terminal]
        for _ in range(horizon-1):
            result.append(1/(2+a-result[-1]))
        return list(reversed(result))


def lai_step(previous, demand, coefficient):
    """Original continuous action x_t = C_t x_(t-1) + (I-C_t) v_t."""
    with localcontext() as ctx:
        ctx.prec = 50
        c = Decimal(coefficient)
        return [c*Decimal(x)+(1-c)*Decimal(v) for x,v in zip(previous,demand)]


def integer_allocation(shares, total):
    """Adapter: largest remainder, preserving all integer wei exactly."""
    with localcontext() as ctx:
        ctx.prec = 50
        total = int(total)
        scale = sum(shares)
        if total < 0 or scale <= 0 or any(v < 0 for v in shares):
            raise ValueError("Invalid allocation")
        raw = [Decimal(total)*v/scale for v in shares]
        target = [int(v) for v in raw]
        for i in sorted(range(len(target)),key=lambda i: (-(raw[i]-target[i]),i))[:total-sum(target)]:
            target[i] += 1
        assert sum(target) == total
        return target


def allocation_plan(available, target):
    """Transport confirmed surplus to deficit; at most n-1 transfers."""
    if sum(available) != sum(target):
        raise ValueError("Allocation does not conserve capital")
    donors = [[i,a-t] for i,(a,t) in enumerate(zip(available,target)) if a>t]
    needs = [[i,t-a] for i,(a,t) in enumerate(zip(available,target)) if t>a]
    donors.sort(key=lambda q: (-q[1],q[0])); needs.sort(key=lambda q: (-q[1],q[0]))
    plans=[]; i=0
    for dst,deficit in needs:
        while deficit:
            src,excess=donors[i]
            amount=min(deficit,excess)
            plans.append((src,dst,amount)); deficit-=amount; donors[i][1]-=amount
            if donors[i][1]==0:i+=1
    return plans


class ComparisonAgent(TdrAgent):
    def __init__(self, num_shards, *, method, initial_wei, lai_curvature="1.0", lai_uniform_floor="0.05", **kw):
        super().__init__(num_shards, **kw)
        if method not in METHODS:
            raise ValueError(f"Unknown method: {method}")
        self.method = method
        self.initial_wei = int(initial_wei)
        self.lai_coefficient = lai_coefficients(lai_curvature,1,1)[0]
        self.lai_floor = Decimal(str(lai_uniform_floor))
        if not 0 <= self.lai_floor <= 1:
            raise ValueError("lai_uniform_floor must be in [0,1]")
        self.lai_action = [Decimal(1)/num_shards]*num_shards
        self.compensation = defaultdict(int)
        self.requests = deque()
        # AB: one binding for every unordered pair; total outgoing binding
        # capacity per shard is at most its initial allocation. Never reset.
        limit = initial_wei // max(1, num_shards-1)
        self.bindings = {(s, d): limit for s in range(num_shards)
                         for d in range(num_shards) if s != d}
        self.unlinked_ctx = []
        self.decisions = []
        self.event_ctx_links = []
        self.observed_ids = set()
        self.decision_ns = 0
        self.decision_calls = 0
        self._last_eval_block = None

    def note_ctx(self, cid, block, src, dst, value):
        if cid in self.observed_ids:
            raise ValueError("Duplicate CTX observation")
        self.observed_ids.add(cid)
        self.unlinked_ctx.append((str(cid), int(block)))
        self.requests.append((int(block), int(dst), int(value)))

    def note_settled(self, src, dst, value):
        if self.method == "horcrux":
            # Opposite confirmed payments cancel corresponding obligations.
            reverse = min(value, self.compensation[(dst, src)])
            self.compensation[(dst, src)] -= reverse
            self.compensation[(src, dst)] += value-reverse

    def _shaduf_plan(self, block, available):
        # Source shard clocks can differ: observation blocks need not arrive in
        # sorted order. Filter the entire history, not just its left endpoint.
        self.requests = deque(r for r in self.requests
                              if r[0] >= block-self.window.window_blocks+1)
        required = defaultdict(int)
        for observed, dst, value in self.requests:
            if observed <= block:
                required[dst] = max(required[dst], value)
        av = list(available)
        plans = []
        for dst in sorted(required, key=lambda s: (-max(0, required[s]-av[s]), s)):
            deficit = max(0, required[dst]-av[dst])
            for src in sorted((s for s in range(self.num_shards) if s != dst),
                              key=lambda s: (-min(av[s], self.bindings[(s, dst)]), s)):
                amount = min(deficit, av[src], self.bindings[(src, dst)])
                if amount:
                    plans.append((src, dst, amount))
                    av[src] -= amount
                    # Planned incoming is NOT available to any donor in this
                    # event. Only confirmed assets may finance outgoing burns.
                    deficit -= amount
                if not deficit or len(plans) >= self.num_shards-1:
                    break
            if len(plans) >= self.num_shards-1:
                break
        return plans

    def _lai_plan(self, block, available):
        self.requests = deque(r for r in self.requests
                              if r[0] >= block-self.window.window_blocks+1)
        demand = [0]*self.num_shards
        for observed,dst,value in self.requests:
            if observed <= block:demand[dst] += value
        if not sum(demand) or not sum(available):
            return []
        with localcontext() as ctx:
            ctx.prec = 50
            v = [(1-self.lai_floor)*Decimal(d)/sum(demand)+self.lai_floor/self.num_shards
                 for d in demand]
            self.lai_action = lai_step(self.lai_action,v,self.lai_coefficient)
            self.last_target = integer_allocation(self.lai_action,sum(available))
        return allocation_plan(available,self.last_target)

    def tick(self, block, available):
        # One decision per logical block for every method, with common exclusion
        # and cooldown. Execution uses confirmed minus reserved assets only.
        if self._last_eval_block == block:
            return []
        self._last_eval_block = block
        started = perf_counter_ns()
        self.decision_calls += 1
        try:
            if self.method == "tdr":
                plans = super().tick(block, available)
            else:
                if self._start_block is None:
                    self._start_block = block
                if (self.state != self.ST_IDLE or block < self._start_block+self.offset_blocks
                        or (self.last_event_block is not None
                            and block < self.last_event_block+self.chi_blocks)):
                    return []
                av = list(map(int, available))
                if self.method == "brokerchain":
                    plans = []
                elif self.method == "horcrux":
                    plans = []
                    # Aggregate confirmed service offsets and execute as delayed
                    # transfers. No virtual channel and no instantaneous rescue.
                    for (src, dst), debt in sorted(self.compensation.items(),
                                                  key=lambda item: (-item[1], item[0])):
                        amount = min(debt, av[src])
                        if amount > 0:
                            plans.append((src, dst, amount))
                            av[src] -= amount
                        if len(plans) >= self.num_shards-1:
                            break
                elif self.method == "shaduf":
                    plans = self._shaduf_plan(block, av)
                else:
                    plans = self._lai_plan(block, av)
                if plans:
                    self.state = self.ST_ACTIVE
                    self.events_opened += 1
                    self.last_event_block = block
                    self.last_plan = list(plans)
                    self.active = [dict(src=s, dst=d, amount=a, stage="pending",
                                        submitted_block=None, deadline_block=None)
                                   for s, d, a in plans]
            if plans:
                outgoing = defaultdict(int)
                if len(plans) > self.num_shards-1:
                    raise ValueError("Per-event transfer budget exceeded")
                for src, dst, amount in plans:
                    if src == dst or amount <= 0 or not (0 <= src < self.num_shards and 0 <= dst < self.num_shards):
                        raise ValueError("Invalid transfer")
                    outgoing[src] += amount
                if any(value > int(available[src]) for src, value in outgoing.items()):
                    raise ValueError("Plans consume unconfirmed or reserved assets")
                event = self.events_opened
                links = [(cid, h) for cid, h in self.unlinked_ctx if h <= block]
                self.unlinked_ctx = [(cid, h) for cid, h in self.unlinked_ctx if h > block]
                self.event_ctx_links.extend(dict(event_id=event, ctx_id=cid,
                                                 observed_block=h, decision_block=block)
                                            for cid, h in links)
                self.decisions.append(dict(event_id=event, decision_block=block,
                                           reference_ctx_count=len(links), transfer_count=len(plans),
                                           amount_wei=sum(a for _, _, a in plans),
                                           available_wei=list(map(int, available))))
            return plans
        finally:
            self.decision_ns += perf_counter_ns()-started

    def mark_second_confirmed(self, idx):
        tr = self._get(idx)
        if tr is not None and tr["stage"] != "done":
            src, dst, amount = tr["src"], tr["dst"], tr["amount"]
            if self.method == "horcrux":
                self.compensation[(src, dst)] = max(0, self.compensation[(src, dst)]-amount)
            elif self.method == "shaduf":
                self.bindings[(src, dst)] -= amount
                self.bindings[(dst, src)] += amount
            super().mark_second_confirmed(idx)

    def snapshot(self):
        result = super().snapshot()
        result.update(method=self.method, method_label=METHODS[self.method],
                      fidelity="wallet_local_adaptation" if self.method != "tdr" else "existing_tdr",
                      observed_ctx_count=len(self.observed_ids),
                      trigger_reference_ctx_count=len(self.event_ctx_links),
                      decisions=self.decisions, event_ctx_links=self.event_ctx_links,
                      decision_ns=self.decision_ns, decision_calls=self.decision_calls)
        return result

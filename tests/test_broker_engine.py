"""broker_engine 纯函数 + FakeTx 引擎单测（无 anvil，秒级）。"""
import pickle
import random

import pytest

from brokerlab.broker_engine import (
    BrokerEngine, EngineSpec, assign_sender_groups, funding_plan, group_key)


def _ctx(i, sender, src, dst, receiver, amt):
    return {"ctx_id": f"c{i:04d}", "sender_idx": sender, "receiver_idx": receiver,
            "src_shard": src, "dst_shard": dst, "amount_wei": amt,
            "orig_from": "", "orig_to": ""}


# ---------------------------------------------------------------------------
# 纯函数：分组与装箱
# ---------------------------------------------------------------------------

def test_group_key_is_collision_unit():
    a = _ctx(1, 200, 0, 1, 210, 5)
    b = _ctx(2, 200, 1, 0, 211, 5)      # 同账户不同源分片 ⇒ 不同冲突域
    assert group_key(a) != group_key(b)
    assert group_key(a) == (200, 0)


def test_partition_is_exhaustive_and_exclusive():
    rng = random.Random(1)
    ctxs = [_ctx(i, 200 + i % 40, i % 2, (i + 1) % 2, 240 + i % 30,
                 rng.randrange(10**16, 5 * 10**18)) for i in range(300)]
    bins = assign_sender_groups(ctxs, num_brokers=50, seed=42)
    flat = [c for b in bins.values() for c in b]
    assert len(flat) == len(ctxs)
    assert sorted(c["ctx_id"] for c in flat) == sorted(c["ctx_id"] for c in ctxs)
    # 每个 (sender,src) 组完整落在唯一一箱 ⇒ nonce 单写者结构性成立
    where = {}
    for b, cs in bins.items():
        for c in cs:
            k = group_key(c)
            assert where.setdefault(k, b) == b, f"group {k} split across bins"


def test_deterministic_same_seed_invariants_any_seed():
    ctxs = [_ctx(i, 200 + i % 37, i % 2, (i + 1) % 2, 240 + i % 20, 10**17 + i)
            for i in range(150)]
    a1 = assign_sender_groups(ctxs, 50, seed=42)
    a2 = assign_sender_groups(ctxs, 50, seed=42)
    assert {b: [c["ctx_id"] for c in cs] for b, cs in a1.items()} == \
           {b: [c["ctx_id"] for c in cs] for b, cs in a2.items()}
    b1 = assign_sender_groups(ctxs, 50, seed=7)
    assert sum(len(v) for v in b1.values()) == 150     # 换种子仍是完备划分


def test_lpt_balances_counts():
    # 100 个组、大小 1–9 ⇒ LPT 后最重箱与最轻箱笔数差应远小于随机装箱
    ctxs = []
    i = 0
    for g in range(100):
        for _ in range(1 + g % 9):
            ctxs.append(_ctx(i, 200 + g, 0, 1, 240 + i % 50, 10**17)); i += 1
    bins = assign_sender_groups(ctxs, 50, seed=42)
    counts = sorted(len(v) for v in bins.values())
    assert counts[0] > 0 and counts[-1] - counts[0] <= 9   # 一组之内的粒度


def test_funding_plan_covers_outflow():
    ctxs = [_ctx(0, 200, 0, 1, 240, 7), _ctx(1, 201, 0, 1, 241, 5),
            _ctx(2, 200, 0, 1, 242, 3)]
    bins = assign_sender_groups(ctxs, num_brokers=2, seed=42)
    fp = funding_plan(bins, num_shards=2, buffer_eth=1, broker_buffer_eth=2)
    for b, cs in bins.items():
        need = sum(c["amount_wei"] for c in cs if c["dst_shard"] == 1)
        assert fp.broker_init_wei[b][1] >= need
    assert fp.total_wei == 15
    assert fp.sender_out_wei[(200, 0)] == 10


# ---------------------------------------------------------------------------
# FakeTx：脚本化链
# ---------------------------------------------------------------------------

class FakeUsers:
    def address(self, idx):
        return f"0x{idx:040x}"


class FakeTx:
    """send_transfer 记锚；probe_receipt 按"再探 confirm_after 次即成功"演化。

    时钟由测试手动推进（engine 注入 clock=lambda: fake.now）。"""

    def __init__(self, confirm_after=1, status=1):
        self.now = 0.0
        self.n = 0
        self.confirm_after = confirm_after
        self.status = status
        self.anchor = {}
        self.submitted = []
        self.nonces_used = []
        self._meta = {}

    def send_transfer(self, from_idx, to_addr, amount_wei, shard, nonce=None):
        self.n += 1
        h = f"0x{self.n:064x}"
        self.anchor[h] = 100 + self.n
        self._meta[h] = {"submits": 0}
        self.submitted.append((from_idx, to_addr, amount_wei, shard))
        self.nonces_used.append((from_idx, nonce))      # 记录外部 nonce 是否透传
        return h

    def submit_block(self, h):
        return self.anchor[h]

    def tx_bytes(self, h):
        return 108

    def probe_receipt(self, h, shard):
        m = self._meta[h]
        m["submits"] += 1
        if m["submits"] > self.confirm_after:
            return {"status": self.status, "block": self.anchor[h] + 1,
                    "receipt_bytes": 800}
        return None


def _engine(tx, *, ctxs, init=None, max_inflight=16, broker=0):
    spec = EngineSpec(broker_idx=broker, ctxs=tuple(ctxs),
                      init_wei_by_shard=init or {0: 10**24, 1: 10**24})
    return BrokerEngine(tx, FakeUsers(), spec, broker_base_index=100,
                        max_inflight=max_inflight, probe_first_s=0.0, probe_s=0.0,
                        poll_s=0.0, timeout_s=1e9, clock=lambda: tx.now)


def test_pipeline_happy_path_two_legs_settled():
    tx = FakeTx(confirm_after=1)
    e = _engine(tx, ctxs=[_ctx(0, 200, 0, 1, 240, 7), _ctx(1, 201, 0, 1, 241, 5)])
    for _ in range(10):
        tx.now += 1.0
        e.tick_once()
        if e.done():
            break
    assert e.done()
    rows = e.envelope()["rows"]
    assert len(rows) == 2 and all(r["ok"] for r in rows)
    assert all(r["fail_reason"] is None for r in rows)
    assert all(r["hops_total"] == 2 for r in rows)
    assert rows[0]["t1_secs"] is not None and rows[0]["t2_secs"] is not None
    # Θ2 只在各自 Θ1 确认后出现：提交序列 = Θ1,Θ1,Θ2,Θ2（流水线，非逐笔串行）
    kinds = [(s[0], s[1]) for s in tx.submitted]
    assert kinds[0][0] == 200 and kinds[1][0] == 201
    assert kinds[2][0] == 100 and kinds[3][0] == 100
    assert kinds[2][1] == f"0x{240:040x}"


def test_max_inflight_one_degenerates_to_serial():
    tx = FakeTx(confirm_after=1)
    e = _engine(tx, ctxs=[_ctx(i, 200 + i, 0, 1, 240 + i, 4) for i in range(3)],
                max_inflight=1)
    tx.now += 1.0
    e.tick_once()
    assert len(tx.submitted) == 1          # 第一笔未确认前不签第二笔
    while not e.done():
        tx.now += 1.0
        e.tick_once()
    assert len(tx.submitted) == 6          # 3×(Θ1+Θ2)


def test_reserve_head_blocks_and_counts_once():
    """dst 侧钱只出不进（Θ1 进的是 src 侧）⇒ 预留挡下的队首在单批次内不再放行。
    引擎行为正确；本测试锁三件事：节流发生、每笔只计一次、第一笔仍正常落定。"""
    v = 10**18
    tx = FakeTx(confirm_after=1)
    e = _engine(tx, ctxs=[_ctx(0, 200, 0, 1, 240, v), _ctx(1, 201, 0, 1, 241, v)],
                init={0: 0, 1: v})            # dst 只够一笔
    tx.now += 1.0
    e.tick_once()
    assert len(tx.submitted) == 1              # 第二笔被 dst 预留节流
    assert e.reserve_blocked == 1
    while len(e.envelope()["rows"]) < 1:       # 第一笔两段落定
        tx.now += 1.0
        e.tick_once()
    assert e.envelope()["rows"][0]["ok"] is True
    for _ in range(5):                          # 第二笔永远不够：不重复计数、不偷发
        tx.now += 1.0
        e.tick_once()
    assert e.reserve_blocked == 1
    assert len(tx.submitted) == 2               # 只有第一笔的 Θ1+Θ2
    assert not e.done()
    assert e._ledger.confirmed[1] == 0 and e._ledger.reserved[1] == 0


def test_revert_t1_releases_reservation_and_records_reason():
    tx = FakeTx(confirm_after=1, status=0)
    e = _engine(tx, ctxs=[_ctx(0, 200, 0, 1, 240, 7)])
    while not e.done():
        tx.now += 1.0
        e.tick_once()
    row = e.envelope()["rows"][0]
    assert row["ok"] is False and row["fail_reason"] == "revert_t1"
    assert e._ledger.reserved[1] == 0
    assert e._ledger.confirmed[1] == 10**24  # 链上没发生，账本分毫未动


def test_timeout_t1_fail_reason_and_reservation_release():
    tx = FakeTx(confirm_after=10**9)
    e = _engine(tx, ctxs=[_ctx(0, 200, 0, 1, 240, 7)])
    for _ in range(3):
        tx.now += 1.0
        e.tick_once()
    assert not e.done()
    tx.now += 1e9 + 1
    e.tick_once()
    assert e.done()
    assert e.envelope()["rows"][0]["fail_reason"] == "timeout_t1"
    assert e._ledger.reserved[1] == 0


def test_spec_and_envelope_picklable():
    tx = FakeTx(confirm_after=0)
    e = _engine(tx, ctxs=[_ctx(0, 200, 0, 1, 240, 7)])
    while not e.done():
        tx.now += 1.0
        e.tick_once()
    env = pickle.loads(pickle.dumps(e.envelope()))
    assert env["rows"][0]["ok"] is True
    spec = EngineSpec(1, ({"ctx_id": "x"},), {0: 1})
    assert pickle.loads(pickle.dumps(spec)) == spec


def test_release_gate_defers_pump_and_counts():
    """到达门：release() 未覆盖到的 ctx 不许泵出；计数 arrival_gated 是饱和信号。"""
    tx = FakeTx(confirm_after=1)
    ctxs = []
    for i in range(5):
        c = _ctx(i, 200 + i, 0, 1, 240 + i, 5)
        c["arrival_pos"] = i
        ctxs.append(c)
    state = {"released": 2}
    spec = EngineSpec(0, tuple(ctxs), {0: 10**24, 1: 10**24})
    e = BrokerEngine(tx, FakeUsers(), spec, broker_base_index=100,
                     probe_first_s=0.0, probe_s=0.0, poll_s=0.0,
                     timeout_s=1e9, clock=lambda: tx.now,
                     release=lambda: state["released"])
    tx.now += 1.0
    e.tick_once()
    assert len(tx.submitted) == 2            # 只有已到达的两笔被泵出
    assert e.arrival_gated >= 1
    state["released"] = 5                    # 逻辑块推进 ⇒ 后续到达放行
    for _ in range(20):
        tx.now += 1.0
        e.tick_once()
    assert e.done()
    env = e.envelope()
    assert len(env["rows"]) == 5 and all(r["ok"] for r in env["rows"])
    assert [r["arrival_pos"] for r in env["rows"]] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# relay 兜底（stage 4 + credit 队列）—— exp006 用，dst 耗尽时改走 burn-and-mint
# ---------------------------------------------------------------------------

class _Q:
    """极简队列替身：list + get_nowait 语义（空则抛 queue.Empty）。"""
    def __init__(self):
        from collections import deque
        self.d = deque()

    def put(self, x):
        self.d.append(x)

    def get_nowait(self):
        from queue import Empty
        if not self.d:
            raise Empty
        return self.d.popleft()


def test_relay_fallback_completes_via_credit_ack():
    """dst 侧不够 ⇒ 走 relay：burn 段自己签，mint 段等 credit_in 回铸造回执。"""
    from brokerlab.brokerchain import BURN_ADDRESS
    v = 10**18
    tx = FakeTx(confirm_after=1)                 # 每段第二次探针命中
    spec = EngineSpec(0, (_ctx(0, 200, 0, 1, 240, v),), {0: 0, 1: 0})   # dst 无钱
    req_q, ack_q = _Q(), _Q()
    e = BrokerEngine(tx, FakeUsers(), spec, broker_base_index=100,
                     probe_first_s=0.0, probe_s=0.0, poll_s=0.0,
                     timeout_s=1e9, clock=lambda: tx.now,
                     credit_sink=req_q, credit_in=ack_q)
    tx.now += 1.0
    e.tick_once()                                # pump：burn 段提交 + 发代铸请求
    assert tx.submitted[0][1] == BURN_ADDRESS and tx.submitted[0][2] == v
    assert e.relay_fallback == 1
    req = req_q.d[0]
    assert req["receiver_idx"] == 240 and req["amount_wei"] == v
    tx.now += 1.0
    e.tick_once()                                # poll：burn 段第一次探针 miss
    tx.now += 1.0
    e.tick_once()                                # burn 段确认；stage 仍等回执
    assert not e.done()
    ack_q.put((req["ref"], 1, 500))              # coordinator 铸币成功，落块 500
    tx.now += 1.0
    e.tick_once()
    assert e.done()
    row = e.envelope()["rows"][0]
    assert row["route"] == "relay" and row["ok"] is True
    assert row["t2_block"] == 500 and row["t2_secs"] is None   # relay 段无本引擎锚点


def test_relay_timeout_recorded_when_no_ack():
    v = 10**18
    tx = FakeTx(confirm_after=1)
    spec = EngineSpec(0, (_ctx(0, 200, 0, 1, 240, v),), {0: 0, 1: 0})
    req_q, ack_q = _Q(), _Q()
    e = BrokerEngine(tx, FakeUsers(), spec, broker_base_index=100,
                     probe_first_s=0.0, probe_s=0.0, poll_s=0.0,
                     timeout_s=5.0, clock=lambda: tx.now,
                     credit_sink=req_q, credit_in=ack_q)
    for _ in range(50):
        tx.now += 1.0
        e.tick_once()
        if e.done():
            break
    assert e.done()
    row = e.envelope()["rows"][0]
    assert row["ok"] is False and row["fail_reason"] == "timeout_relay"


# ---------------------------------------------------------------------------
# DynamicBrokerEngine（exp007）：队列投递、终审改判、rep 回报、nonce 透传
# ---------------------------------------------------------------------------

def test_dynamic_engine_local_judgment_relay_and_reports():
    """第一笔 broker 服务（dst 刚好够），第二笔终审改判 relay —— 全部经队列。"""
    from brokerlab.broker_engine import DynamicBrokerEngine, END
    v = 10**18
    tx = FakeTx(confirm_after=1)
    ctx_q, rep_q, req_q, ack_q = _Q(), _Q(), _Q(), _Q()
    e = DynamicBrokerEngine(tx, FakeUsers(), broker_idx=0, broker_base_index=100,
                            ctx_q=ctx_q, rep_q=rep_q,
                            init_wei_by_shard={0: 0, 1: v},
                            credit_sink=req_q, credit_in=ack_q,
                            probe_first_s=0.0, probe_s=0.0, poll_s=0.0,
                            timeout_s=1e9, clock=lambda: tx.now)
    c1 = _ctx(0, 200, 0, 1, 240, v); c1["nonce"] = 5
    c2 = _ctx(1, 201, 0, 1, 241, v); c2["nonce"] = 6
    ctx_q.put(c1); ctx_q.put(c2); ctx_q.put(END)
    tx.now += 1.0
    e.tick_once()                    # pump：两笔全部收到，c2 立即终审改判
    assert e.n_rcvd == 2 and e.relay_fallback == 1
    assert req_q.d[0]["ref"] == "c0001" and req_q.d[0]["amount_wei"] == v
    # nonce 透传：Θ1 与 burn 用的是下发的 5/6；broker 自己的 Θ2 走本地计数(None)
    assert tx.nonces_used[0] == (200, 5) and tx.nonces_used[1] == (201, 6)
    ack_q.put(("c0001", 1, 500))
    for _ in range(10):
        tx.now += 1.0
        e.tick_once()
        if e.done():
            break
    assert e.done()
    env = e.envelope()
    by_id = {r["ctx_id"]: r for r in env["rows"]}
    assert by_id["c0000"]["route"] == "broker" and by_id["c0000"]["ok"]
    assert by_id["c0001"]["route"] == "relay" and by_id["c0001"]["ok"]
    assert by_id["c0001"]["t2_block"] == 500
    reps = list(rep_q.d)
    assert len(reps) == 2 and {r[1] for r in reps} == {"c0000", "c0001"}
    assert env["n_assigned"] == 2

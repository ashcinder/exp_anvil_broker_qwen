"""TdrDynamicEngine 闭环单测（FakeTx）：需求观察→触发→burn→代铸→dst 补液。"""
from collections import deque

from brokerlab.broker_engine import END
from brokerlab.brokerchain import BURN_ADDRESS
from brokerlab.tdr_engine import TdrAgent
from brokerlab.tdr_hook import TdrDynamicEngine

GWEI = 10 ** 9


class FakeUsers:
    def address(self, idx):
        return f"0x{idx:040x}"


class FakeQ:
    def __init__(self):
        self.d = deque()

    def put(self, x):
        self.d.append(x)
    put_nowait = put

    def get_nowait(self):
        from queue import Empty
        if not self.d:
            raise Empty
        return self.d.popleft()


class FakeTx:
    """可手动推块高的假链：confirm_after=1（每段第二次探针命中）。"""

    def __init__(self):
        self.now = 0.0
        self.h = 100
        self.n = 0
        self.submitted = []
        self._meta = {}

    def send_transfer(self, from_idx, to_addr, amount_wei, shard, nonce=None):
        self.n += 1
        h = f"0x{self.n:064x}"
        self._meta[h] = {"submits": 0, "block": self.h + 1}
        self.submitted.append((from_idx, to_addr, int(amount_wei), shard))
        return h

    def submit_block(self, h):
        return self.h                        # 提交即当前块

    def tx_bytes(self, h):
        return 108

    def block_number(self, shard):
        return self.h

    def probe_receipt(self, h, shard):
        m = self._meta[h]
        m["submits"] += 1
        if m["submits"] > 1:
            return {"status": 1, "block": m["block"], "receipt_bytes": 800}
        return None


def _ctx(i, sender, src, dst, receiver, amt):
    return {"ctx_id": f"c{i:04d}", "sender_idx": sender, "receiver_idx": receiver,
            "src_shard": src, "dst_shard": dst, "amount_wei": amt,
            "arrival_pos": i, "orig_from": "", "orig_to": ""}


def test_tdr_replenishes_drained_dst_shard():
    """场景：dst(shard0) 已被抽干、src(shard1) 堆满 → 一笔 relay 需求进窗口
    → TDR 触发 (1→0,200) → burn 落定 → T-ref 代铸 → shard0 补液 200。"""
    tx = FakeTx()
    ctx_q, rep_q, req_q, ack_q = FakeQ(), FakeQ(), FakeQ(), FakeQ()
    agent = TdrAgent(2, window_blocks=5, epsilon=0.1, q_min=0.0,
                     chi_blocks=0, timeout_blocks=9999)
    e = TdrDynamicEngine(tx, FakeUsers(), broker_idx=0, broker_base_index=100,
                         ctx_q=ctx_q, rep_q=rep_q,
                         init_wei_by_shard={0: 0, 1: 200 * GWEI},
                         credit_sink=req_q, credit_in=ack_q,
                         probe_first_s=0.0, probe_s=0.0, poll_s=0.0,
                         timeout_s=1e9, clock=lambda: tx.now,
                         tdr_agent=agent, block_poll_s=0.0)
    # 一笔 dst0 的 CTX：shard0 没钱 → relay 改判（同时喂进 TDR 的需求信号）
    ctx_q.put(_ctx(0, 200, 1, 0, 240, 10 * GWEI))
    tx.now += 1
    e.tick_once()                            # pump：改判 relay，burn 提交
    assert len(req_q.d) == 1                 # relay 代铸请求
    assert agent.demand_observed_total == 1  # 提交成功即观测，不等待 relay 完成
    tx.h = 104                               # 恰好满窗：observe 锚点 100 仍在窗口
    for _ in range(4):                       # 驱动 relay 两段落定 → _emit → observe
        tx.now += 1
        e.tick_once()
    ack_q.put(("c0000", 1, tx.h))
    for _ in range(3):
        tx.now += 1
        e.tick_once()
    assert agent.demand_observed_total == 1  # 被 relay 的 CTX 也计需求（F3）
    # TDR 应已触发：burn 段(100→BURN, 200@shard1) 出现在提交序列
    t_req = [q for q in req_q.d if str(q["ref"]).startswith("T")]
    assert t_req and t_req[0]["receiver_idx"] == 100      # 铸给自己的 dst 子账户
    assert t_req[0]["dst_shard"] == 0 and t_req[0]["amount_wei"] == 200 * GWEI
    # 回 T-ack → mint 落账，shard0 补液
    ack_q.put((t_req[0]["ref"], 1, tx.h))
    for _ in range(4):
        tx.now += 1
        e.tick_once()
    assert e._ledger.confirmed == {0: 200 * GWEI, 1: 0}   # 钱从 shard1 搬到 shard0
    ctx_q.put(END)                                       # 收尾信号（真实系统在全部落定后）
    tx.now += 1
    e.tick_once()
    assert e.done()
    env = e.envelope()
    assert env["tdr"]["events_opened"] == 1
    assert env["tdr"]["transfers_done"] == 1
    assert env["tdr"]["lost_in_transit_wei"] == 0
    assert env["tdr"]["move_events"] == [{
        "src_shard": 1, "dst_shard": 0, "amount_wei": 200 * GWEI,
        "burn_block": 105, "mint_block": 104,
    }]
    snapshots = env["tdr"]["balance_snapshots"]
    assert snapshots
    assert snapshots[-1]["shard_0_balance"] == 200 * GWEI
    assert snapshots[-1]["tdr_self_transfer_confirmed"] == 1
    # Σburn≡Σmint：链上 burn 提交 = relay 10 + tdr 200
    burns = [c for c in tx.submitted if c[1] == BURN_ADDRESS]
    assert sum(c[2] for c in burns) == 210 * GWEI

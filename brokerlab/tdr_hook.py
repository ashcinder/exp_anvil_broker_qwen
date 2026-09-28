"""TdrDynamicEngine —— 把 TDR 挂进 exp007 动态引擎（M4 接入层）。

设计（PLAN 锁定"再平衡 = 真实两半交易"）：
  一笔转移 (src→dst, amt)：
    第一段：broker@src → BURN —— broker 账户自签（本地 nonce，一箱一主不变），
            提交时 ledger.reserve(src, amt)，落定 confirm_debit，失败 release。
    第二段：CreditRequest{receiver_idx = broker 自己的账户索引} → coordinator
            代铸到 broker@dst —— 与 relay 兜底共用同一条代铸通道，
            请求格式完全一致，coordinator 无需任何改动；ref 用 "T" 前缀命名空间。
  效果：src 子账户的钱经 BURN/铸币"搬"到 dst，Σburn≡Σmint 恒等，
        全程无 setBalance（审计 F2 红线）。

需求统计（审计 F3）：每笔被泵出的 CTX 在【提交块高】回看记入 DemandWindow——
无论它之后成交、revert 还是改判 relay，都计需求（"路由时刻"语义）。

块高来源：引擎没有全局时钟；每 block_poll_s 轮询两个分片块高取 min 再单调化，
作为 h 估计（χ、deadline、滑窗都以块计，估计漂移 ≤1 块可接受）。
"""
import queue
import time
import typing as t

from .broker_engine import DynamicBrokerEngine, END
from .brokerchain import BURN_ADDRESS
from .tdr_engine import TdrAgent
from .tdr_policy import percent_of


class TdrDynamicEngine(DynamicBrokerEngine):
    """动态引擎 + 每块 TDR tick。其余行为与父类一致（tdr_agent=None 即退化）。"""

    def __init__(self, *a: t.Any, tdr_agent: t.Optional[TdrAgent] = None,
                 block_poll_s: float = 1.0, **kw: t.Any) -> None:
        super().__init__(*a, **kw)
        self.tdr = tdr_agent
        self._block_poll_s = float(block_poll_s)
        self._h_est = 0                       # 块高估计（单调，min(两分片) 推进）
        self._last_blk_poll = 0.0
        self._tdr_seq = 0                     # 事件编号（ref 命名空间）
        self._tdr_items: t.List[t.Dict[str, t.Any]] = []   # 当前事件的转账执行态
        self._tdr_moves: t.List[t.Tuple[int, int, int]] = []  # 已完成 (src,dst,amt) 台账
        self._tdr_move_events: t.List[t.Dict[str, int]] = []  # 带 burn/mint 块高的绘图台账
        self._balance_snapshots: t.List[t.Dict[str, t.Any]] = []
        self._await_ack: t.Set[str] = set()   # 未收到代铸回执的 mint 请求 ref

    def _report_liquidity_delta(self, shard: int, delta_wei: int) -> None:
        """把 TDR 对本地 available 的变化实时同步给动态路由 coordinator。"""
        self._rep_q.put(("liquidity", self._spec.broker_idx,
                         int(shard), int(delta_wei)))

    # ------------------------------------------------------------------
    # 需求观测：Θ1 成功提交后立刻按真实提交块高记入，不等待 settle/fail。
    def _after_ctx_submitted(self, f) -> None:
        super()._after_ctx_submitted(f)
        if self.tdr is not None:
            self.tdr.observe(f.b1_anchor, int(f.ctx["dst_shard"]),
                             int(f.ctx["amount_wei"]))

    # ------------------------------------------------------------------
    def tick_once(self) -> None:
        super().tick_once()
        if self.tdr is None:
            return
        now = self._clock()
        if now - self._last_blk_poll >= self._block_poll_s:
            self._last_blk_poll = now
            try:
                hs = [self._tx.block_number(s)
                      for s in range(self.tdr.num_shards)]
                self._h_est = max(self._h_est, min(hs))
            except Exception:
                pass                          # 链瞬时不可达：下轮再试
        self._tdr_poll()                      # 先推进已提交的段（confirm/ack/timeout）
        self._tdr_plan()                      # 再决定是否开新事件
        self._record_balance_snapshot()

    def _record_balance_snapshot(self) -> None:
        """记录原 Fig5b 所需的真实时序：已确认余额、τ、上界和 TDR 计数。"""
        target = self.tdr.last_target
        row: t.Dict[str, t.Any] = {
            "block": int(self._h_est),
            "normal_tx_confirmed": len(self._rows),
            "tdr_self_transfer_confirmed": int(self.tdr.transfers_done),
        }
        for shard in sorted(self._ledger.confirmed):
            row[f"shard_{shard}_balance"] = int(self._ledger.confirmed[shard])
            tau = (int(target[shard])
                   if target is not None and shard < len(target) else None)
            row[f"shard_{shard}_tau"] = tau
            row[f"shard_{shard}_upper_bound"] = (
                tau + percent_of(tau, self.tdr.epsilon) if tau is not None else None
            )
        if self._balance_snapshots and self._balance_snapshots[-1]["block"] == row["block"]:
            self._balance_snapshots[-1] = row
        else:
            self._balance_snapshots.append(row)

    # ------------------------------------------------------------------
    def _tdr_plan(self) -> None:
        if self._ended:                      # 收到 END：系统收尾，不再开新事件
            return
        if self._tdr_items:
            return                           # 执行缓存未清空，禁止覆盖迟到回执
        if self.tdr.state != TdrAgent.ST_IDLE:
            return
        snap = self._ledger.snapshot_available()
        plans = self.tdr.tick(self._h_est, [snap.get(s, 0) for s in
                                           range(len(snap))])
        if not plans:
            return
        self._tdr_seq += 1
        self._tdr_items = [{"src": s, "dst": d, "amount": a, "stage": "pending"}
                           for (s, d, a) in plans]
        for i, tr in enumerate(self._tdr_items):
            self._submit_tdr_first(i)

    def _submit_tdr_first(self, i: int) -> None:
        tr = self._tdr_items[i]
        src, amt = tr["src"], tr["amount"]
        self._ledger.reserve(src, amt)        # 先预留再签：available 立刻收缩
        self._report_liquidity_delta(src, -amt)
        try:
            h = self._tx.send_transfer(self._broker_account, BURN_ADDRESS, amt, src)
        except Exception:
            self._ledger.release(src, amt)
            self._report_liquidity_delta(src, amt)
            tr["stage"] = "failed"
            self.tdr.fail(i, "first_submit_failed")
            return
        tr["stage"] = "first_submitted"
        tr["h1"] = h
        tr["blk"] = self._tx.submit_block(h)
        self.tdr.mark_first_submitted(i, tr["blk"])
        tr["deadline_block"] = tr["blk"] + self.tdr.timeout_blocks

    def _tdr_poll(self) -> None:
        if not self._tdr_items:
            return
        now = self._clock()
        # confirm-before-fail：判超时前最后核一次回执——慢落定的 burn 不许被误判为
        # 未发生（误判会让链上 BURN 与本地台账永久失联）。
        h = self.tdr and self._h_est
        for i, tr in enumerate(self._tdr_items):
            if (tr["stage"] == "first_submitted" and h is not None
                    and tr.get("deadline_block") is not None
                    and h > tr["deadline_block"]):
                r = self._tx.probe_receipt(tr["h1"], tr["src"])
                if r is not None and r["status"] == 1:
                    tr["__late"] = r          # 走下面的正常 confirm 路径
                    tr["stage"] = "first_submitted"  # 保持 stage，下面统一处理
        for i, tr in enumerate(self._tdr_items):
            if tr["stage"] == "first_submitted":
                r = tr.pop("__late", None)
                if r is None:
                    r = self._tx.probe_receipt(tr["h1"], tr["src"]) if now >= (
                        tr.get("next_probe", 0.0)) else None
                tr["next_probe"] = now + self._probe_s
                if r is not None:
                    if r["status"] != 1:
                        self._ledger.release(tr["src"], tr["amount"])
                        self._report_liquidity_delta(tr["src"], tr["amount"])
                        tr["stage"] = "failed"
                        self.tdr.fail(i, "first_failed")
                        continue
                    self._ledger.confirm_debit(tr["src"], tr["amount"])
                    tr["burn_block"] = int(r["block"])
                    self.tdr.mark_first_confirmed(i)
                    tr["first_confirmed"] = True
                    # 第二段：请求代铸到自己的 dst 子账户（共用 CreditRequest 通道）
                    ref = f"T{self._tdr_seq}.{i}"
                    tr["ref"] = ref
                    tr["stage"] = "second_requested"
                    self._credit_sink.put({"ref": ref, "engine": self._spec.broker_idx,
                                           "dst_shard": tr["dst"],
                                           "amount_wei": tr["amount"],
                                           "receiver_idx": self._broker_account})
                    self._await_ack.add(ref)
                    self.tdr.mark_second_submitted(i)
            elif tr["stage"] == "second_requested":
                ref = tr["ref"]
                if ref in self._acks:
                    status, _blk = self._acks.pop(ref)
                    self._await_ack.discard(ref)
                    if status == 1:
                        self._ledger.confirm_credit(tr["dst"], tr["amount"])
                        self._report_liquidity_delta(tr["dst"], tr["amount"])
                        self.tdr.mark_second_confirmed(i)
                        tr["stage"] = "done"
                        self._tdr_moves.append((tr["src"], tr["dst"], tr["amount"]))
                        self._tdr_move_events.append({
                            "src_shard": int(tr["src"]),
                            "dst_shard": int(tr["dst"]),
                            "amount_wei": int(tr["amount"]),
                            "burn_block": int(tr["burn_block"]),
                            "mint_block": int(_blk),
                        })
                    else:
                        tr["stage"] = "failed"
                        self.tdr.fail(i, "second_failed")   # burn 已落 → 记损耗
            elif (tr["stage"] == "failed" and tr.get("ref")
                    and tr.get("first_confirmed")):
                # 超时判负 ≠ 链上失败：迟到的 mint 回执照样落账（账本追链，不追丢钱）
                if tr["ref"] in self._acks:
                    status, _blk = self._acks.pop(tr["ref"])
                    self._await_ack.discard(tr["ref"])
                    if status == 1:
                        self._ledger.confirm_credit(tr["dst"], tr["amount"])
                        self._report_liquidity_delta(tr["dst"], tr["amount"])
                        tr["stage"] = "done"
                        self._tdr_moves.append((tr["src"], tr["dst"], tr["amount"]))
                        self._tdr_move_events.append({
                            "src_shard": int(tr["src"]),
                            "dst_shard": int(tr["dst"]),
                            "amount_wei": int(tr["amount"]),
                            "burn_block": int(tr["burn_block"]),
                            "mint_block": int(_blk),
                        })
                        self.tdr.recover_lost(tr["src"], tr["dst"], tr["amount"])
        # pending 交易没有可靠的链上取消语义；"超过若干块仍无 receipt"不能证明
        # burn 未发生。继续追踪到明确 receipt，避免释放预留后迟到 burn 造成双花。
        if (self._tdr_items and all(tr["stage"] in ("done", "failed")
                                    for tr in self._tdr_items)
                and not self._await_ack):
            self._tdr_items = []

    # ------------------------------------------------------------------
    def done(self) -> bool:
        """终局以状态机为权威：agent 回到 IDLE 即事件全部终结
        （items 表只是执行缓存，与 agent.active 不同步时不得卡死收尾）。"""
        base = DynamicBrokerEngine.done(self)
        if self.tdr is None:
            return base
        return (base and self.tdr.state == TdrAgent.ST_IDLE
                and not self._await_ack and not self._tdr_items)

    def envelope(self) -> t.Dict[str, t.Any]:
        env = super().envelope()
        if self.tdr is not None:
            env["tdr"] = self.tdr.snapshot()
            env["tdr"]["moves"] = list(self._tdr_moves)   # 对账明细：链上应==ledger 终态
            env["tdr"]["move_events"] = list(self._tdr_move_events)
            env["tdr"]["balance_snapshots"] = list(self._balance_snapshots)
            env["tdr"]["lost_moves"] = list(self.tdr.lost_moves)
        return env


def tdr_engine_from_payload(payload: t.Mapping[str, t.Any],
                            ctx_q: t.Any, rep_q: t.Any,
                            credit_sink: t.Any,
                            credit_in: t.Any) -> TdrDynamicEngine:
    """TDR 引擎的 spawn 构造入口（tdr_params 在 payload["tdr_params"]）。"""
    from .broker_engine import build_services
    from .tdr_engine import TdrAgent
    tx, users, scale = build_services(payload["cfg"])
    p = payload["params"]
    tp = payload["tdr_params"]
    agent = TdrAgent(int(tp["num_shards"]),
                     window_blocks=int(tp["window_blocks"]),
                     epsilon=float(tp["epsilon"]), q_min=float(tp["q_min"]),
                     chi_blocks=int(tp["chi_blocks"]),
                     timeout_blocks=int(tp["timeout_blocks"]),
                     trigger_mode=str(tp.get("trigger_mode", "excess_only")),
                     offset_blocks=int(tp.get("offset_blocks", 0)),
                     policy=str(tp.get("policy", "proportional")),
                     valve_init_wei=int(tp.get("valve_init_wei", 0)),
                     valve_cap_mult=float(tp.get("valve_cap_mult", 2.0)),
                     base_lead_blocks=float(tp.get("base_lead_blocks", 6.0)),
                     base_safety=float(tp.get("base_safety", 2.0)),
                     base_floor_frac=float(tp.get("base_floor_frac", 0.05)),
                     demand_ewma_half_life=float(
                         tp.get("demand_ewma_half_life", 0.0)),
                     demand_ewma_min_blocks=int(
                         tp.get("demand_ewma_min_blocks", 5)),
                     target_cap=float(tp.get("target_cap", 0.0)),
                      min_reserve_wei=int(tp.get("min_reserve_wei", 0)),
                      surplus_epsilon=float(tp.get("surplus_epsilon",
                                                   tp["epsilon"])))
    return TdrDynamicEngine(
        tx, users,
        broker_idx=int(payload["broker_idx"]),
        broker_base_index=scale.broker_base_index,
        ctx_q=ctx_q, rep_q=rep_q,
        init_wei_by_shard={int(k): int(v) for k, v
                           in payload["init_wei_by_shard"].items()},
        credit_sink=credit_sink, credit_in=credit_in,
        max_inflight=int(p["max_inflight"]), poll_s=float(p["poll_s"]),
        probe_s=float(p["probe_s"]), probe_first_s=float(p["probe_first_s"]),
        timeout_s=float(p["timeout_s"] if "timeout_s" in p
                        else p["engine_timeout_s"]),
        tdr_agent=agent, block_poll_s=float(tp.get("block_poll_s", 1.0)),
        fee_broker_wei=int(p.get("fee_broker_wei", 0)),
        fee_burn_wei=int(p.get("fee_burn_wei", 0)))

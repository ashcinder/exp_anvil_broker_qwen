"""流水线 broker 引擎 —— M3 broker 块循环的可运行雏形。

exp005 引入（基底基准），exp006 复用（注入速率扫描），exp007 扩展出
DynamicBrokerEngine（coordinator 动态路由 + 队列投递）。

协议约束只有两条，本引擎两条都守：
  ① Θ2 必须等【它自己】的 Θ1 确认（两阶段承诺，逐笔独立）；
  ② 发 Θ1 前 dst 侧预留流动性（BrokerLedger：available = confirmed − reserved）。
下一笔的 Θ1 不等上一笔——这是它与 BrokerChain.execute（阻塞式，exp001/002 基线）
的全部差别；每段仍走 TxService 的同一套原语（签名/发送/实测字节），守恒口径不变。

两代引擎的分工（docstring 即合同）：
  BrokerEngine        ：静态批次（启动期装好的 ctx 列表 + 到达门回调）。
                        单写者 = 一个 (账户,分片) 一个签名者（assign_sender_groups
                        保证 (sender,src) 组全局只落一箱，broker 账户一箱一主）。
  DynamicBrokerEngine ：ctx 由 coordinator 逐块经队列送来（含预分配 nonce），
                        本地终审后要么服务、要么自走 relay。
                        单写者松弛为"一个 (账户,分片,nonce) 一个签名者"——
                        nonce 由 coordinator 串行分配，签名由持有者完成。
两者的 pump/poll/结算/度量完全同源；TDR 的 tick 与再平衡段在 M4 叠加。
"""
import os
import random
import time
import typing as t
from dataclasses import dataclass

from .brokerchain import BURN_ADDRESS
from .chain import Connections
from .config import ETH, ChainCfg, ScaleCfg
from .identity import UserManager
from .ledger import BrokerLedger
from .tx import TxService

END = "END"   # ctx_queue 的哨兵：coordinator 宣告"不会再有你的活了"


# ---------------------------------------------------------------------------
# 纯函数：工作负载指派与充值计划（无 I/O，单测覆盖）
# ---------------------------------------------------------------------------


def group_key(ctx: t.Mapping[str, t.Any]) -> t.Tuple[int, int]:
    """nonce 冲突域 = (sender_idx, src_shard)。

    Θ1 由发送者账户在 src 分片签名；同一 (账户,分片) 被两个引擎并发签
    = nonce 竞争。分组必须按这个键，而不是单独的 sender_idx
    （一般 num_shards/num_users 组合下同一账户可落多个分片）。"""
    return (int(ctx["sender_idx"]), int(ctx["src_shard"]))


def assign_sender_groups(ctx_dicts: t.Sequence[t.Mapping[str, t.Any]],
                         num_brokers: int,
                         seed: int) -> t.Dict[int, t.List[t.Dict[str, t.Any]]]:
    """把发送者组 LPT 装箱到各 broker；返回 {broker_idx: [ctx dict…]}。

    LPT（最大处理时间优先）：大组先装，每次进当前笔数最少的箱
    （同笔数看金额，再看箱号）⇒ 各方案墙钟由最慢箱决定，箱必须均衡。
    先 seed shuffle 再按 (−笔数, −金额, 最小组内 ctx_id) 排序：
    shuffle 只打散同键组的先后（CSV 到达偏置），不变量与 seed 无关。"""
    groups: t.Dict[t.Tuple[int, int], t.List[t.Dict[str, t.Any]]] = {}
    for c in ctx_dicts:
        groups.setdefault(group_key(c), []).append(dict(c))
    glist = list(groups.values())
    rng = random.Random(seed)
    rng.shuffle(glist)
    glist.sort(key=lambda g: (-len(g),
                              -sum(x["amount_wei"] for x in g),
                              min(x["ctx_id"] for x in g)))
    bins: t.Dict[int, t.List[t.Dict[str, t.Any]]] = {b: [] for b in range(num_brokers)}
    cnt = [0] * num_brokers
    wei = [0] * num_brokers
    for g in glist:
        b = min(range(num_brokers), key=lambda i: (cnt[i], wei[i], i))
        bins[b].extend(g)
        cnt[b] += len(g)
        wei[b] += sum(x["amount_wei"] for x in g)
    return bins


@dataclass(frozen=True)
class FundingPlan:
    broker_init_wei: t.Dict[int, t.Dict[int, int]]     # broker → 分片 → 初始 wei
    sender_out_wei: t.Dict[t.Tuple[int, int], int]     # (sender_idx, src_shard) → Σv
    total_wei: int


def funding_plan(bins: t.Mapping[int, t.Sequence[t.Mapping[str, t.Any]]],
                 num_shards: int, buffer_eth: float,
                 broker_buffer_eth: float) -> FundingPlan:
    """垫资恰好覆盖需求 + buffer（exp001 的"充足即止"纪律）。

    broker 每分片初始 = 该箱在该分片作 dst 的 Σv + broker_buffer —— src 侧
    同额垫背让账本对账对称；sender 每 (idx,src) = Σv + buffer_eth。"""
    broker_init: t.Dict[int, t.Dict[int, int]] = {}
    total = 0
    for b, cs in bins.items():
        dst_out = {s: 0 for s in range(num_shards)}
        for c in cs:
            dst_out[int(c["dst_shard"])] += int(c["amount_wei"])
            total += int(c["amount_wei"])
        buf = int(broker_buffer_eth * ETH)
        broker_init[int(b)] = {s: v + buf for s, v in dst_out.items()}
    sender_out: t.Dict[t.Tuple[int, int], int] = {}
    for cs in bins.values():
        for c in cs:
            k = (int(c["sender_idx"]), int(c["src_shard"]))
            sender_out[k] = sender_out.get(k, 0) + int(c["amount_wei"])
    return FundingPlan(broker_init, sender_out, total)


# ---------------------------------------------------------------------------
# 引擎：pump → poll 的 tick 循环
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineSpec:
    """一个 broker 的静态批次（全 plain 类型 ⇒ 进程间 pickle 安全）。"""
    broker_idx: int
    ctxs: t.Tuple[t.Dict[str, t.Any], ...]
    init_wei_by_shard: t.Dict[int, int]


@dataclass
class _Inflight:
    ctx: t.Dict[str, t.Any]
    stage: int                       # 1=等 Θ1 回执，2=等 broker 的 Θ2 回执，4=等 coordinator 代铸回执
    route: str = "broker"
    h1: str = ""
    b1_anchor: int = 0
    tb1: int = 0
    t1_submit: float = 0.0
    t1_confirm: float = 0.0
    b1_block: t.Optional[int] = None
    st1: t.Optional[int] = None
    rb1: int = 0
    h2: str = ""
    b2_anchor: int = 0
    tb2: int = 0
    t2_submit: float = 0.0
    t2_confirm: float = 0.0
    b2_block: t.Optional[int] = None
    st2: t.Optional[int] = None
    # B2E Θ1b 腿（stage=3 等回执）；fee=0 时该腿不存在
    hb: str = ""
    b_burn_anchor: int = 0
    t_burn_submit: float = 0.0
    next_probe: float = 0.0


def build_services(cfg_plain: t.Mapping[str, t.Any]) -> t.Tuple[TxService, UserManager, ScaleCfg]:
    """从 plain dict 重建全套链上服务（spawn 安全：不继承任何 socket/句柄）。

    每个引擎各自调用一次 ⇒ 各自的 NonceManager 与各自的 Web3 连接
    （requests.Session 非线程安全，线程模式同样必须一人一份）。"""
    chain = ChainCfg(**dict(cfg_plain["chain"]))
    scale = ScaleCfg(**dict(cfg_plain["scale"]))
    users = UserManager(chain.mnemonic)
    conns = Connections(chain)
    tx = TxService(conns, users, chain)
    return tx, users, scale


class BrokerEngine:
    """单 broker、静态批次的流水线执行体（exp005 基底 / exp006 注入速率扫描共用）。

    用法（三种容器共用）：
      serial    ：外层循环反复调 tick_once()，直到 done()。
      线程/进程 ：直接调 run_blocking(gate, t0)，内部自旋 tick。

    路由：本引擎只看自己账本的 dst 可用额。dst 够 ⇒ broker 路径；
    dst 不足 ⇒ 若配了 credit 队列则 relay 兜底（burn 段自签 + 请求代铸），
    否则队首停表（exp005 语义，无 coordinator 在场）。动态匹配见子类。
    """

    def __init__(self, tx: TxService, users: UserManager, spec: EngineSpec, *,
                 broker_base_index: int, max_inflight: int = 16,
                 poll_s: float = 0.05, probe_s: float = 0.2,
                 probe_first_s: float = 0.9, timeout_s: float = 90.0,
                 release: t.Optional[t.Callable[[], int]] = None,
                 credit_sink: t.Optional[t.Any] = None,
                 credit_in: t.Optional[t.Any] = None,
                 fee_broker_wei: int = 0, fee_burn_wei: int = 0,
                 clock: t.Callable[[], float] = time.monotonic) -> None:
        """release：到达调度钩子——返回"全局已释放的 CTX 数"（逻辑块 × ctx_per_block，
        由链上块高驱动，绝不用墙钟；审计 F1 的结构性防线）。None = 立即全部可用
        （exp005 的静态批次行为，逐字节不变）。bin 内 ctx 必须按 arrival_pos 升序。"""
        self._tx = tx
        self._users = users
        self._spec = spec
        self._broker_account = broker_base_index + spec.broker_idx
        self._broker_addr = users.address(self._broker_account)
        self._max_inflight = int(max_inflight)
        self._poll_s = float(poll_s)
        self._probe_s = float(probe_s)
        self._probe_first_s = float(probe_first_s)
        self._timeout_s = float(timeout_s)
        self._release = release
        self._credit_sink = credit_sink     # put(CreditRequest)：向 coordinator 求代铸
        self._credit_in = credit_in         # get_nowait()→(ref, status, block)：代铸回执
        self._relay_probe = None            # 诊断探针：list 则记录每次 relay 决策的账本状态
        # B2E 费用（PLAN_b2e v2；enabled=false ⇒ 全 0 ⇒ 行为与旧路径逐字节一致）。
        # broker 路径：Θ1a = v+F（sender 一笔签），Θ1b = (1−β)F 由 broker@src 签
        # 小额烧币（本地 nonce，避开 coordinator 预分配的 sender 域）——
        # 各账户终态与 brokerchain 串行版逐元一致（sender −v−F、broker +v+βF、BURN +(1−β)F）。
        self._fee_broker = int(fee_broker_wei)
        self._fee_burn = int(fee_burn_wei)
        self._fee_total = self._fee_broker + self._fee_burn
        self._clock = clock
        self._ctxs = list(spec.ctxs)
        self._cursor = 0
        self.arrival_gated = 0        # 泵因"到达还没释放"而停表的次数（饱和分析信号）
        self.relay_fallback = 0       # dst 流动性不足、改走 relay 的笔数
        self._acks: t.Dict[str, t.Tuple[int, int]] = {}   # ref→(status, block) 代铸回执
        self._open: t.Dict[str, _Inflight] = {}
        self._rows: t.List[t.Dict[str, t.Any]] = []
        # 三本账（ledger.py）：confirmed=已确认余额，reserved=在途承诺。
        # setup 垫资为 confirmed 初值；它只反映本引擎自己触发的交易段。
        self._ledger = BrokerLedger(spec.init_wei_by_shard)
        self.reserve_blocked = 0              # 被流动性节流的"笔数"（每笔至多计一次）
        self._blocked_counted: t.Set[str] = set()
        self.inflight_peak = 0
        self._t_first = 0.0                   # 首次 pump 提交时刻（actor 活跃窗口起点）
        self._t_last = 0.0                    # 末次落定时刻（窗口终点）
        self._t_start = 0.0
        self._t_end = 0.0

    # ------------------------------------------------------------------
    def done(self) -> bool:
        """批次完成 = 全部 ctx 都已从批次取出，且在途集合已清空。"""
        return self._cursor >= len(self._ctxs) and not self._open

    def tick_once(self) -> None:
        """一轮 pump + 一轮 poll，全程不 sleep（协作式）。"""
        self._pump()
        self._poll()

    # ------------------------------------------------------------------
    def _pump(self) -> None:
        now = self._clock()
        while self._cursor < len(self._ctxs) and len(self._open) < self._max_inflight:
            c = self._ctxs[self._cursor]
            # 到达门：release() 返回全局已释放笔数，未到队的 ctx 一律不许提前发
            if self._release is not None and int(c["arrival_pos"]) >= self._release():
                self.arrival_gated += 1       # 到达门停表，非流动性节流
                return
            v = int(c["amount_wei"])
            dst = int(c["dst_shard"])
            if self._ledger.available(dst) < v:
                # dst 不够：有 coordinator ⇒ relay 兜底；没有（exp005）⇒ 队首停表。
                if self._credit_sink is None:
                    if c["ctx_id"] not in self._blocked_counted:
                        self._blocked_counted.add(c["ctx_id"])
                        self.reserve_blocked += 1
                    return
                self._submit_relay(c, now)
                self._cursor += 1
                continue
            self._ledger.reserve(dst, v)
            self._submit_broker(c, now)
            self._cursor += 1

    def _submit_broker(self, c: t.Dict[str, t.Any], now: float) -> None:
        """broker 路径的 Θ1：sender → broker@src（金额 v；预留已由调用方登记）。
        nonce 若随 ctx 下发（动态路由，coordinator 预分配）则照用，否则本地计数。"""
        src = int(c["src_shard"])
        h1 = self._tx.send_transfer(int(c["sender_idx"]), self._broker_addr,
                                    int(c["amount_wei"]) + self._fee_total, src,
                                    nonce=c.get("nonce"))
        if self._t_first == 0.0:
            self._t_first = now
        self._open[c["ctx_id"]] = _Inflight(
            ctx=c, stage=1, h1=h1,
            b1_anchor=self._tx.submit_block(h1), tb1=self._tx.tx_bytes(h1),
            t1_submit=now, next_probe=now + self._probe_first_s)
        self._after_ctx_submitted(self._open[c["ctx_id"]])
        self.inflight_peak = max(self.inflight_peak, len(self._open))

    def _after_ctx_submitted(self, f: _Inflight) -> None:
        """成功提交 Θ1 后的扩展钩子；基类无动作。

        TDR 在这里按真实提交块记录需求，避免等到整笔交易完成后才回补。
        """

    def _poll(self) -> None:
        now = self._clock()
        if self._credit_in is not None:                # 先抽干代铸回执，按 ref 归档
            while True:
                try:
                    ref, st, blk = self._credit_in.get_nowait()
                except Exception:
                    break
                self._acks[ref] = (st, blk)
        for cid, f in list(self._open.items()):
            if now < f.next_probe:
                continue
            f.next_probe = now + self._probe_s
            if f.stage == 1:
                if now - f.t1_submit > self._timeout_s:
                    self._fail(cid, f, "timeout_t1");  continue
                r = self._tx.probe_receipt(f.h1, int(f.ctx["src_shard"]))
                if r is None:
                    continue
                f.t1_confirm, f.b1_block, f.st1, f.rb1 = now, r["block"], r["status"], r["receipt_bytes"]
                if r["status"] != 1:
                    self._fail(cid, f, "revert_t1");    continue
                # Θ1 落定：src 侧净进账 v+βF（Θ1a 全额到账、(1−β)F 随即被
                # Θ1b 烧掉；账本只记净值，烧币腿确认不再进账本 ⇒ 终态与链上一致）。
                self._ledger.confirm_credit(int(f.ctx["src_shard"]),
                                            int(f.ctx["amount_wei"]) + self._fee_broker)
                if self._fee_burn > 0:
                    # B2E Θ1b：broker@src → BURN（本地 nonce 域，单写者安全）
                    hb = self._tx.send_transfer(self._broker_account, BURN_ADDRESS,
                                                self._fee_burn,
                                                int(f.ctx["src_shard"]))
                    f.stage, f.hb = 3, hb
                    f.b_burn_anchor = self._tx.submit_block(hb)
                    f.t_burn_submit = now
                    continue
                self._submit_theta2(f, now)
            elif f.stage == 3:
                if now - f.t_burn_submit > self._timeout_s:
                    self._fail(cid, f, "timeout_fee_burn");  continue
                rb = self._tx.probe_receipt(f.hb, int(f.ctx["src_shard"]))
                if rb is None:
                    continue
                if rb["status"] != 1:
                    self._fail(cid, f, "revert_fee_burn");    continue
                self._submit_theta2(f, now)
            elif f.stage == 2:
                if now - f.t2_submit > self._timeout_s:
                    # Θ2 未落定而 Θ1 已确认：钱在 broker src 侧滞留。
                    # 不自动重试（gen-3 教训），滞留额由守恒门在链上暴露。
                    self._fail(cid, f, "timeout_t2");    continue
                r = self._tx.probe_receipt(f.h2, int(f.ctx["dst_shard"]))
                if r is None:
                    continue
                f.t2_confirm, f.b2_block, f.st2 = now, r["block"], r["status"]
                if r["status"] != 1:
                    self._fail(cid, f, "revert_t2");     continue
                self._ledger.confirm_debit(int(f.ctx["dst_shard"]),
                                           int(f.ctx["amount_wei"]))
                self._open.pop(cid)
                self._t_last = now
                self._emit(self._row(f, ok=True))
            else:  # stage 4：relay 兜底，burn 段已提交，等 coordinator 代铸的回执
                if now - f.t1_submit > self._timeout_s:
                    self._fail(cid, f, "timeout_relay");  continue
                # relay 的跨片通信口径是 burn 段的签名 RLP + 回执
                # JSON。旧逻辑只等 coordinator 的 mint ACK，没有探测 burn
                # 回执，导致 relay 行的 t1_receipt_bytes 为空，CSV 无法
                # 还原实测通信量。两段可并行确认，但只有两者都落定
                # 才输出该行。
                if f.b1_block is None:
                    r = self._tx.probe_receipt(f.h1, int(f.ctx["src_shard"]))
                    if r is None:
                        continue
                    f.t1_confirm, f.b1_block, f.st1, f.rb1 = (
                        now, r["block"], r["status"], r["receipt_bytes"])
                    if r["status"] != 1:
                        self._fail(cid, f, "revert_relay_burn");  continue
                if cid not in self._acks:
                    continue
                status, block = self._acks.pop(cid)
                f.t2_confirm, f.b2_block, f.st2 = now, block, status
                if status != 1:
                    self._fail(cid, f, "revert_credit");  continue
                self._open.pop(cid)
                self._t_last = now
                self._emit(self._row(f, ok=True))

    def _submit_theta2(self, f: _Inflight, now: float) -> None:
        """两阶段承诺的第二段：broker@dst → receiver（费用腿之后/或无费时紧随 Θ1）。"""
        h2 = self._tx.send_transfer(self._broker_account,
                                    self._addr_of(int(f.ctx["receiver_idx"])),
                                    int(f.ctx["amount_wei"]),
                                    int(f.ctx["dst_shard"]))
        f.stage, f.h2 = 2, h2
        f.b2_anchor = self._tx.submit_block(h2)
        f.tb2 = self._tx.tx_bytes(h2)
        f.t2_submit = now

    def _submit_relay(self, c: t.Dict[str, t.Any], now: float) -> None:
        """relay 兜底：burn 段由本引擎用 sender 账户签（静态：箱内私产；
        动态：nonce 是 coordinator 分给我的，同样只我一家签）；
        mint 段发 CreditRequest，回执经 credit_in 队列取回。"""
        cid, v = c["ctx_id"], int(c["amount_wei"])
        src, dst = int(c["src_shard"]), int(c["dst_shard"])
        # B2E：relay 回退时费用全额随 v 进 BURN（broker 未服务，不留费）；
        # mint 段恒为 v（代铸预算与销毁额一一对应）。
        h1 = self._tx.send_transfer(int(c["sender_idx"]), BURN_ADDRESS,
                                    v + self._fee_total, src,
                                    nonce=c.get("nonce"))
        if self._t_first == 0.0:
            self._t_first = now
        self.relay_fallback += 1
        self._open[cid] = _Inflight(
            ctx=c, stage=4, route="relay", h1=h1,
            b1_anchor=self._tx.submit_block(h1), tb1=self._tx.tx_bytes(h1),
            t1_submit=now, next_probe=now + self._probe_first_s)
        self._after_ctx_submitted(self._open[cid])
        self._credit_sink.put({"ref": cid, "engine": self._spec.broker_idx,
                               "dst_shard": dst, "amount_wei": v,
                               "receiver_idx": int(c["receiver_idx"])})
        self.inflight_peak = max(self.inflight_peak, len(self._open))

    def _addr_of(self, account_index: int) -> str:
        return self._users.address(account_index)

    def _fail(self, cid: str, f: _Inflight, reason: str) -> None:
        dst, v = int(f.ctx["dst_shard"]), int(f.ctx["amount_wei"])
        if f.stage in (1, 2, 3):
            # broker 路径：撤销 dst 在途预留（confirmed 不动；已进账的 src 由守恒门暴露）。
            self._ledger.release(dst, v)
        # stage 4（relay）从未占用 dst 预留，无需回滚；burn 段若已上链由守恒门计入。
        self._open.pop(cid)
        self._t_last = self._clock()
        self._emit(self._row(f, ok=False, reason=reason))

    def _emit(self, row: t.Dict[str, t.Any]) -> None:
        """行落账 + （动态模式）向 coordinator 回报一行摘要。基类只落账。"""
        self._rows.append(row)

    # ------------------------------------------------------------------
    def _row(self, f: _Inflight, ok: bool, reason: t.Optional[str] = None) -> t.Dict[str, t.Any]:
        c = f.ctx
        hops1 = (f.b1_block - f.b1_anchor) if f.b1_block is not None else None
        # relay 行的 mint 段块号来自 coordinator 回执，本引擎无提交锚点 ⇒ hops2/t2_* 留 None。
        hops2 = ((f.b2_block - f.b2_anchor)
                 if f.b2_block is not None and f.b2_anchor else None)
        row = {
            "ctx_id": c["ctx_id"], "route": f.route, "broker_idx": self._spec.broker_idx,
            "arrival_pos": c.get("arrival_pos"), "nonce": c.get("nonce"),
            "submit_block": f.b1_anchor,        # Θ1 提交块高（需求滑窗的记账坐标）
            "src": int(c["src_shard"]), "dst": int(c["dst_shard"]),
            "amount_wei": int(c["amount_wei"]), "ok": ok, "fail_reason": reason,
            "t1_block": f.b1_block, "t1_hops": hops1,
            "t1_secs": round(f.t1_confirm - f.t1_submit, 3) if f.b1_block is not None else None,
            "t1_tx_bytes": f.tb1,
            "t1_receipt_bytes": f.rb1 if f.b1_block is not None else None,
            "t2_block": f.b2_block, "t2_hops": hops2,
            "t2_secs": (round(f.t2_confirm - f.t2_submit, 3)
                        if f.b2_block is not None and f.t2_submit else None),
            "t2_tx_bytes": f.tb2 if f.b2_block is not None else None,
            "e2e_secs": round(f.t2_confirm - f.t1_submit, 3) if f.b2_block is not None else None,
            "hops_total": (hops1 + hops2) if hops1 is not None and hops2 is not None else None,
        }
        if self._fee_total > 0:
            # broker 行：βF 归 broker、(1−β)F 烧；relay 行：全额 (F) 随 v 烧、broker 无所得
            row["fee_broker_wei"] = (self._fee_broker if f.route == "broker" else 0)
            row["fee_burn_wei"] = (self._fee_burn if f.route == "broker"
                                   else self._fee_total)
        return row

    # ------------------------------------------------------------------
    def run_blocking(self, gate: t.Optional[t.Any] = None,
                     t0: t.Optional[float] = None) -> t.Dict[str, t.Any]:
        """线程/进程模式入口：等闸门 → 自旋 tick 到全部落定。"""
        if gate is not None:
            gate.wait()
        cpu0 = os.times()
        self._t_start = t0 if t0 is not None else self._clock()
        while not self.done():
            self.tick_once()
            if not self.done():
                time.sleep(self._poll_s)
        self._t_end = self._clock()
        cpu1 = os.times()
        env = self.envelope()
        env.update({"t_start_mono": round(self._t_start, 3),   # 闸门释放后的运行窗口
                    "t_end_mono": round(self._t_end, 3),
                    "run_window_s": round(self._t_end - self._t_start, 3),
                    "cpu_user_s": round(cpu1[0] - cpu0[0], 3),
                    "cpu_sys_s": round(cpu1[1] - cpu0[1], 3),
                    "pid": os.getpid()})
        return env

    def envelope(self) -> t.Dict[str, t.Any]:
        """结果信封（全 plain 类型 ⇒ 可 pickle 过 mp.Queue）。"""
        return {"broker_idx": self._spec.broker_idx,
                "rows": self._rows,
                "final_mirror": {str(s): v for s, v in self._ledger.confirmed.items()},
                "reserve_blocked": self.reserve_blocked,
                "arrival_gated": self.arrival_gated,
                "relay_fallback": self.relay_fallback,
                "inflight_peak": self.inflight_peak,
                "n_assigned": len(self._ctxs),
                "t_first_submit_mono": round(self._t_first, 3) or None,
                "t_last_settle_mono": round(self._t_last, 3) or None,
                "actor_wall_s": (round(self._t_last - self._t_first, 3)
                                 if self._t_first and self._t_last else None),
                "relay_probe": (self._relay_probe if self._relay_probe else None),
                "error": None}


# ---------------------------------------------------------------------------
# 动态路由引擎（exp007 / M3）
# ---------------------------------------------------------------------------


class DynamicBrokerEngine(BrokerEngine):
    """ctx 经 ctx_queue 从 coordinator 逐块流入的动态执行体。

    与父类的三处差异：
      ① 批次来源 = 队列（含 END 哨兵），不再要求静态 ctxs；
      ② dst 不足【不停表】——终审改判 relay（burn 用下发的 nonce），
         这就是"估计-终审双层防线"：coordinator 估计表错了只多一次回退，
         钱由终审的本地账本守住（审计 F5）。
      ③ 每段落定向 rep_q 回报一行摘要（coordinator 据此释放发送者窗口、
         修正估计表、记录 no_qualified_local）。
    单写者约定：nonce 是 coordinator 串行分配的整数，本引擎只用不造；
    broker 账户仍只由本引擎签（Θ2 走本地计数），一箱一主不变。"""

    def __init__(self, tx: TxService, users: UserManager, *,
                 broker_idx: int, broker_base_index: int,
                 ctx_q: t.Any, rep_q: t.Any, init_wei_by_shard: t.Mapping[int, int],
                 credit_sink: t.Any, credit_in: t.Any, **kw: t.Any) -> None:
        if credit_sink is None or credit_in is None:
            raise ValueError("动态引擎必须配 credit 队列（relay 终审兜底的通路）")
        spec = EngineSpec(broker_idx=broker_idx, ctxs=(),
                          init_wei_by_shard=dict(init_wei_by_shard))
        super().__init__(tx, users, spec, broker_base_index=broker_base_index,
                         credit_sink=credit_sink, credit_in=credit_in, **kw)
        self._ctx_q = ctx_q
        self._rep_q = rep_q
        self._ended = False
        self.n_rcvd = 0                 # 从队列收到的 ctx 数（含被终审改判的）

    def done(self) -> bool:
        """完成 = 收到 END 哨兵 且 在途清空（END 是队尾，之后不会再有货）。"""
        return self._ended and not self._open

    def _pump(self) -> None:
        now = self._clock()
        while len(self._open) < self._max_inflight and not self._ended:
            try:
                c = self._ctx_q.get_nowait()
            except Exception:
                return
            if c is END or c == END:
                self._ended = True
                return
            self.n_rcvd += 1
            v, dst = int(c["amount_wei"]), int(c["dst_shard"])
            if self._ledger.available(dst) < v:
                # —— relay 探针（诊断用）：记录决策瞬间的账本状态 ——
                if getattr(self, "_relay_probe", None) is not None:
                    self._relay_probe.append({
                        "broker": self._spec.broker_idx,
                        "dst": dst, "v": v,
                        "confirmed": self._ledger.confirmed.get(dst, 0),
                        "reserved": self._ledger.reserved.get(dst, 0),
                        "available": self._ledger.available(dst),
                        "total_conf": sum(self._ledger.confirmed.values()),
                        "all_conf": {str(s): self._ledger.confirmed.get(s, 0)
                                     for s in self._ledger.confirmed},
                    })
                self._submit_relay(c, now)      # 终审改判：不占本地预留
                continue
            self._ledger.reserve(dst, v)
            self._submit_broker(c, now)

    def _emit(self, row: t.Dict[str, t.Any]) -> None:
        super()._emit(row)
        self._rep_q.put((self._spec.broker_idx, row["ctx_id"], row["route"],
                         row["ok"], row["src"], row["dst"], row["amount_wei"]))

    def envelope(self) -> t.Dict[str, t.Any]:
        env = super().envelope()
        env["n_rcvd"] = self.n_rcvd
        env["n_assigned"] = self.n_rcvd        # 动态模式：到货数即任务数
        return env


def engine_from_payload(payload: t.Mapping[str, t.Any],
                        release: t.Optional[t.Callable[[], int]] = None,
                        credit_sink: t.Optional[t.Any] = None,
                        credit_in: t.Optional[t.Any] = None) -> BrokerEngine:
    """spawn worker 的构造入口：plain dict → 引擎（服务全本地新建）。
    release 回调由调用方注入（进程模式：mp.Value 计数器，父进程按块高更新）。
    credit_sink/credit_in：relay 兜底的请求/回执队列（进程模式各引擎独立）。"""
    tx, users, scale = build_services(payload["cfg"])
    spec = EngineSpec(broker_idx=int(payload["spec"]["broker_idx"]),
                      ctxs=tuple(payload["spec"]["ctxs"]),
                      init_wei_by_shard={int(k): int(v) for k, v
                                         in payload["spec"]["init_wei_by_shard"].items()})
    p = payload["params"]
    return BrokerEngine(tx, users, spec,
                        broker_base_index=scale.broker_base_index,
                        max_inflight=int(p["max_inflight"]),
                        poll_s=float(p["poll_s"]), probe_s=float(p["probe_s"]),
                        probe_first_s=float(p["probe_first_s"]),
                        timeout_s=float(p["timeout_s"] if "timeout_s" in p else p["engine_timeout_s"]),
                        release=release, credit_sink=credit_sink, credit_in=credit_in,
                        fee_broker_wei=int(p.get("fee_broker_wei", 0)),
                        fee_burn_wei=int(p.get("fee_burn_wei", 0)))


def dynamic_engine_from_payload(payload: t.Mapping[str, t.Any],
                                ctx_q: t.Any, rep_q: t.Any,
                                credit_sink: t.Any,
                                credit_in: t.Any) -> DynamicBrokerEngine:
    """动态引擎的 spawn 构造入口（队列参数由 worker 现场传入，不进 payload）。"""
    tx, users, scale = build_services(payload["cfg"])
    p = payload["params"]
    return DynamicBrokerEngine(
        tx, users,
        broker_idx=int(payload["broker_idx"]),
        broker_base_index=scale.broker_base_index,
        ctx_q=ctx_q, rep_q=rep_q,
        init_wei_by_shard={int(k): int(v) for k, v
                           in payload["init_wei_by_shard"].items()},
        credit_sink=credit_sink, credit_in=credit_in,
        max_inflight=int(p["max_inflight"]), poll_s=float(p["poll_s"]),
        probe_s=float(p["probe_s"]), probe_first_s=float(p["probe_first_s"]),
        timeout_s=float(p["timeout_s"] if "timeout_s" in p else p["engine_timeout_s"]),
        fee_broker_wei=int(p.get("fee_broker_wei", 0)),
        fee_burn_wei=int(p.get("fee_burn_wei", 0)))

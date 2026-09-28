"""BrokerChain 协议语义 —— 底座层（不含 TDR）。

一笔原始跨分片转账（S 在 src 分片，向 dst 分片的 R 转 v）由两种机制之一服务：

* broker 路径（设计的正路），拆成两段半笔交易：
    Θ1 @ src：S → B_s（v 进入 broker 在源分片的子账户）
    Θ2 @ dst：B_d → R（v 从 broker 在目的分片的子账户付出）
  价值的跨分片守恒由 broker 的流动性完成。

* relay 兜底路径（broker 无力服务时模拟器采取的策略），
  语义是 burn-and-mint（销毁与铸造）：
    Θ1 @ src：S → BURN（v 在源分片被销毁）
    Θ2 @ dst：C → R（v 在目的分片被铸出）

  "铸造"不可能由一笔 EVM 交易凭空造钱——协议造币是委员会的职责。在模拟器里
  coordinator 账户代位"目的分片委员会按协议铸币"：它的资金来源是【铸造预算】，
  只与销毁额一一对应（demo 断言不变量 Σ支出_C ≡ Σ销毁），而不是像 broker
  流动性那样会反过来约束服务能力。另一条路——在目的分片用 anvil_setBalance
  直改余额——被审计 F2 明令禁止：mint 段必须是真实入块交易，
  relay 在目的分片的区块容量开销才对 blockscan 可观测。

BrokerChain 论文 §II-B 定义了 broker 的【资格】，Θ2 由谁付钱留给部署选择；
本模拟让 coordinator 账户扮演这个角色（与旧三代 gen-3 相同）——但每一段
都是真实签名、真实挖矿的交易（审计 F2 的修复），容量开销因此可观测。

本模块刻意保持单进程顺序执行：块驱动的多进程流水线（M3）叠加在
这些相同的原语之上。
"""
import time
import typing as t
from dataclasses import dataclass

from .identity import UserManager
from .tx import TxService

BURN_ADDRESS = "0x000000000000000000000000000000000000dEaD"   # 死地址：转入即永久销毁

ROUTE_BROKER = "broker"   # 路由常量：走 broker 流动性路径
ROUTE_RELAY = "relay"     # 路由常量：走 burn-and-mint 兜底路径


@dataclass(frozen=True)
class CTX:
    """待服务的原始跨分片转账（一份"需求"，尚未拆成链上交易）。"""
    ctx_id: str          # 稳定标识（跨实验、跨跑可对照）
    sender_idx: int      # 发送者账户索引（资金在 src 分片归它所有）
    receiver_idx: int    # 接收者账户索引（款项落在 dst 分片归它所有）
    src_shard: int       # 源分片号
    dst_shard: int       # 目的分片号
    amount_wei: int      # 转账金额 v（单位 wei）
    origin: str = ""     # 出处溯源，例如 "eth_mainnet_trace:0x…"


@dataclass
class HalfResult:
    """一段链上交易（Θ1/Θ2/Θ1b 中任意一段）的执行记录与实测度量。"""
    shard: int                              # 该段发生在哪个分片
    tx_hash: str                            # 交易哈希
    status: t.Optional[int] = None          # 回执状态：1 成功 / 0 revert / None 超时
    block: t.Optional[int] = None           # 落块块高（None=尚未落定）
    # 延迟/通信度量用（relay-vs-broker 实验）：全部实测，不自造数字
    submit_block: int = 0            # 提交瞬间该分片链高（入块跳数之锚）
    submit_ts: float = 0.0           # 提交时刻（time.monotonic 秒；墙钟量只能这么取）
    confirm_ts: float = 0.0          # 观测到回执的时刻（同上）
    tx_bytes: int = 0                # 签名后原始 RLP 交易字节数
    receipt_bytes: int = 0           # 回执 JSON 序列化字节数（跨片证明代理）

    @property
    def ok(self) -> bool:
        """回执状态为 1 才算这一段成功。"""
        return self.status == 1

    @property
    def hops(self) -> t.Optional[int]:
        """该段等待入块的块数（confirm 块高 − submit 时链高）。"""
        if self.block is None:
            return None
        return self.block - self.submit_block


@dataclass
class CtxResult:
    """一笔 CTX 的完整执行记录：路径 + 各段的结果。"""
    ctx: CTX                          # 原始需求（回链）
    route: str                        # 实际所走路径（broker 或 relay）
    theta1: HalfResult                # 第一段（broker 路径即 Θ1a）
    theta2: t.Optional[HalfResult]    # 第二段（未发出时为 None）
    # B2E 附加（PLAN_b2e v2）：broker 路径的烧币段 Θ1b 与本笔费用拆分。
    # fee_burn_wei==0（未启用）时 theta1b 恒为 None，执行序列与旧路径逐字节一致。
    theta1b: t.Optional["HalfResult"] = None
    fee_broker_wei: int = 0
    fee_burn_wei: int = 0

    @property
    def ok(self) -> bool:
        """整笔成功 = 第一段成功 且 烧币段（若有）成功 且 第二段成功。"""
        return (self.theta1.ok
                and (self.theta1b is None or self.theta1b.ok)
                and self.theta2 is not None and self.theta2.ok)


class BrokerChain:
    """按 broker 或 relay 路径执行 CTX。这里没有任何匹配政策：
    哪笔 CTX 交给哪个 broker 完全由调用方决定
    （现在是各实验脚本 + matching.static_broker，将来是 M3 coordinator）。"""

    def __init__(self, tx: TxService, users: UserManager, *,
                 coordinator_index: int, broker_base_index: int) -> None:
        self._tx = tx
        self._users = users
        self._coordinator_index = coordinator_index
        self._broker_base_index = broker_base_index

    def broker_address(self, broker_idx: int) -> str:
        """broker 的"身份"是一个地址；其 |S| 个子账户 = 同一地址在各分片的
        独立余额与 nonce（见 chain.py 节点参数注释），与 BrokerChain 论文口径一致。"""
        return self._users.address(self._broker_base_index + broker_idx)

    # ------------------------------------------------------------------
    def execute(self, ctx: CTX, route: str, broker_idx: int = 0, *,
                fee_broker_wei: int = 0,
                fee_burn_wei: int = 0) -> CtxResult:
        """阻塞执行一笔 CTX 的端到端流程，直到所有段落定。

        费用如何拆分是调用方政策（查 config.b2e_fees），机制层只按参数搬价值：
        broker 路径 Θ1a = v + fee_broker，随后 Θ1b sender→BURN = fee_burn；
        relay 路径无额外一笔——Θ1 = v + fee_broker + fee_burn 全额随 v 销毁。
        两个 fee 参数默认 0 ⇒ 行为与 B2E 之前完全一致（回归不变式）。"""
        if ctx.src_shard == ctx.dst_shard:
            raise ValueError(f"{ctx.ctx_id}: intra-shard CTX passed to cross-shard service")
        if route == ROUTE_BROKER:
            return self._execute_broker(ctx, broker_idx,
                                        int(fee_broker_wei), int(fee_burn_wei))
        if route == ROUTE_RELAY:
            return self._execute_relay(ctx, int(fee_broker_wei), int(fee_burn_wei))
        raise ValueError(f"unknown route {route!r}")

    # ------------------------------------------------------------------
    def _run_half(self, from_idx: int, to_addr: str, amount_wei: int,
                  shard: int) -> HalfResult:
        """签→发→等回执，并沿途采集实测度量（字节数、提交块高、时间戳）。"""
        h = self._tx.send_transfer(from_idx, to_addr, amount_wei, shard)
        half = HalfResult(shard=shard, tx_hash=h,
                          submit_block=self._tx.submit_block(h),
                          submit_ts=time.monotonic(),
                          tx_bytes=self._tx.tx_bytes(h))
        r = self._tx.wait_receipt(h, shard)
        half.confirm_ts = time.monotonic()
        if r is not None:
            half.status, half.block = r["status"], r["block"]
            half.receipt_bytes = r.get("receipt_bytes", 0)
        return half

    def _execute_broker(self, ctx: CTX, broker_idx: int,
                        fee_broker: int, fee_burn: int) -> CtxResult:
        """两段式承诺：Θ2 只有在 Θ1（及其随行的 Θ1b）确认后才发出。
        Θ1 失败即早退（broker dst 侧资金分毫未动）；后续处置（降级 relay 等）
        由调用方策略决定——本模块只做机制，不做政策。
        Θ1b（(1−β)F 的显式烧币）与 Θ1a 同 sender 同分片，nonce 递增，各自等回执。"""
        t1 = self._run_half(ctx.sender_idx, self.broker_address(broker_idx),
                            ctx.amount_wei + fee_broker, ctx.src_shard)
        if not t1.ok:
            return CtxResult(ctx=ctx, route=ROUTE_BROKER, theta1=t1, theta2=None,
                             fee_broker_wei=fee_broker, fee_burn_wei=fee_burn)
        t1b: t.Optional[HalfResult] = None
        if fee_burn > 0:
            t1b = self._run_half(ctx.sender_idx, BURN_ADDRESS, fee_burn, ctx.src_shard)
            if not t1b.ok:
                return CtxResult(ctx=ctx, route=ROUTE_BROKER, theta1=t1, theta2=None,
                                 theta1b=t1b, fee_broker_wei=fee_broker,
                                 fee_burn_wei=fee_burn)
        t2 = self._run_half(self._broker_base_index + broker_idx,
                            self._users.address(ctx.receiver_idx),
                            ctx.amount_wei, ctx.dst_shard)
        return CtxResult(ctx=ctx, route=ROUTE_BROKER, theta1=t1, theta2=t2,
                         theta1b=t1b, fee_broker_wei=fee_broker,
                         fee_burn_wei=fee_burn)

    def _execute_relay(self, ctx: CTX, fee_broker: int, fee_burn: int) -> CtxResult:
        """burn-and-mint：Θ1 在源分片销毁 v+全额费用（F 本来就得和 v 一起进
        BURN，relay 路径不加笔）；Θ2 由签发账户（代表目的分片委员会）
        铸出——mint 段同样是真实入块交易，故 relay 的跨片消息负担（Θ1 证明的
        传输）在此以实测字节计入通信账单，而链上足迹与 broker 路径基本相等。"""
        t1 = self._run_half(ctx.sender_idx, BURN_ADDRESS,
                            ctx.amount_wei + fee_broker + fee_burn, ctx.src_shard)
        if not t1.ok:
            return CtxResult(ctx=ctx, route=ROUTE_RELAY, theta1=t1, theta2=None,
                             fee_broker_wei=fee_broker, fee_burn_wei=fee_burn)
        t2 = self._run_half(self._coordinator_index,
                            self._users.address(ctx.receiver_idx),
                            ctx.amount_wei, ctx.dst_shard)
        return CtxResult(ctx=ctx, route=ROUTE_RELAY, theta1=t1, theta2=t2,
                         fee_broker_wei=fee_broker, fee_burn_wei=fee_burn)

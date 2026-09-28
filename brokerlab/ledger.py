"""BrokerLedger —— broker 本地三本账（审计 F5"双花防火墙"的形式化）。

三个概念各管一段钱：
  confirmed：链上已确认的余额（setup 垫资 + 已落定进账 − 已落定付出）。
  reserved ：已承诺但尚未落定的付出（ITX2/Θ2 在途时占住，防止被二次承诺）。
  available：可服务额度 = confirmed − reserved —— 路由与终审只看这个数。
incoming（在途进账，Θ1 已发出未确认）【刻意不建模、不参与判定】：
  gen-3 的 get_effective_balance 把未到账 credit 当可用资金，TDR 据此搬钱，
  造成 ITX2 预留资金被偷（实测 ~1/800 失败，审计 F5）。本账本从接口上
  就没有这条路：进账只有 confirm_credit（链上确认之后）才会抬高 confirmed。

单写者约定（与 broker_engine 的注释同一合同）：
  静态模式：一个 (账户, 分片) 恰好一个签名者（箱内私产）。
  动态模式（exp007）：nonce 由 coordinator 串行预分配，一个
  (账户, 分片, nonce) 恰好一个签名者；本账本不感知 nonce，只管钱。
纯逻辑、无 I/O；全部状态变更都过断言（负余额 / 超额释放直接抛）。
"""
import typing as t


class LedgerError(RuntimeError):
    """账本不变量被破坏（调用方逻辑 bug，绝不静默吞掉）。"""


class BrokerLedger:
    """一个 broker 的全部子账户账本。分片号 → wei 的三个映射。"""

    def __init__(self, init_wei_by_shard: t.Mapping[int, int]) -> None:
        # setup 垫资即 confirmed 初值；reserved 全零。
        self.confirmed: t.Dict[int, int] = {int(s): int(v)
                                           for s, v in init_wei_by_shard.items()}
        self.reserved: t.Dict[int, int] = {s: 0 for s in self.confirmed}

    # ------------------------------------------------------------------
    def available(self, shard: int) -> int:
        """可服务额度 = confirmed − reserved（服务判定的唯一依据）。"""
        return self.confirmed[shard] - self.reserved[shard]

    # ------------------------------------------------------------------
    def reserve(self, shard: int, wei: int) -> None:
        """承诺一笔付出（发 Θ1 的同时登记 dst 侧义务）。"""
        if wei < 0:
            raise LedgerError(f"reserve 负数 {wei}")
        if wei > self.available(shard):
            raise LedgerError(f"shard {shard}: available={self.available(shard)} < {wei}")
        self.reserved[shard] += wei

    def release(self, shard: int, wei: int) -> None:
        """撤销承诺（Θ1 失败/本地回退）：reserved 减回，confirmed 不动。"""
        if wei < 0 or self.reserved[shard] < wei:
            raise LedgerError(f"shard {shard}: 释放 {wei} 超过已预留 {self.reserved[shard]}")
        self.reserved[shard] -= wei

    def confirm_debit(self, shard: int, wei: int) -> None:
        """付出落定（Θ2 已确认）：同一笔钱同时离开 reserved 与 confirmed。"""
        if wei < 0 or self.reserved[shard] < wei or self.confirmed[shard] < wei:
            raise LedgerError(f"shard {shard}: confirm_debit {wei} 无对应预留/余额")
        self.reserved[shard] -= wei
        self.confirmed[shard] -= wei

    def confirm_credit(self, shard: int, wei: int) -> None:
        """进账落定（Θ1 已确认）：confirmed 抬高。落定前它对本账本不存在。"""
        if wei < 0:
            raise LedgerError(f"confirm_credit 负数 {wei}")
        self.confirmed[shard] = self.confirmed.get(shard, 0) + wei
        self.reserved.setdefault(shard, 0)

    # ------------------------------------------------------------------
    def snapshot_available(self) -> t.Dict[int, int]:
        """各分片 available 快照（纯函数报表用）。"""
        return {s: self.confirmed[s] - self.reserved[s] for s in self.confirmed}

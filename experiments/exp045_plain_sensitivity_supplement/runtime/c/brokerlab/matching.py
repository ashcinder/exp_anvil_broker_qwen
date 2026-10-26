"""Broker matching policy — static_broker（论文 §VI 命名，主计划 §coordinator 块循环）。

规则只有两步：
  1. eligible 集合 = 目的分片可用余额 ≥ v 的 broker。
  2. 用调用方传入的种子 RNG 在 eligible 中均匀随机选一个。
无 eligible 时返回 None——兜底方式由调用方决定：
  顺序实验（exp001/002/004）直接走 relay 并计 no_qualified；
  M3 的 coordinator 则随机指派、由 broker 本地终审后回退。

纪律（为什么是"均匀随机"而不是"挑余额最大"）：
  - 均匀选择让各 broker 可交换：收入、drain、触发率同分布，统计干净。
  - 更聪明的策略会把半个 TDR 偷装进基线，污染论文"匹配 ⊥ 再平衡"的分层。
  - 策略可插拔：日后消融 liquidity-aware 匹配时换这个文件，实验不动。
"""
import random as _random
import typing as t


def select_broker(available_by_broker: t.Mapping[int, int],
                  amount_wei: int,
                  rng: _random.Random) -> t.Optional[int]:
    """从 eligible 集合中均匀随机选一个 broker；无 eligible 返回 None。

    available_by_broker: {broker_id: 该 broker 在目的分片的可用余额 wei}
    """
    eligible = [b for b, avail in available_by_broker.items()
                if avail >= amount_wei]
    if not eligible:
        return None
    return rng.choice(eligible)

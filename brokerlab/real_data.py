"""真实 ETH trace 加载器：主网交易行 → 跨分片 CTX 定义。

本模块承自旧三代 core/real_traffic.py，映射规则逐字节保持不动，
以便与历史分析衔接：
  分片号   = int(address, 16) mod num_shards（地址哈希决定落哪个分片）
  账户索引 = user_base_index + int(address, 16) mod num_users（真实地址映射到模拟账户）

只保留金额在 [value_floor, value_cap] 内的跨片纯转账；
合约创建/交互与出错行全部丢弃。源 CSV 有 328 MB / 130 万行，
因此流式逐行读，绝不整表载入内存。
"""
import csv
import typing as t
from dataclasses import dataclass


@dataclass(frozen=True)
class RealCtx:
    """一行真实交易映射出的跨分片转账需求（还没上链，只是定义）。"""
    ctx_id: str          # 稳定标识：real_ + 原交易哈希前缀（跨跑可对照）
    src_shard: int       # 发送者所在分片
    dst_shard: int       # 接收者所在分片
    amount_wei: int      # 转账金额（wei，原样来自主网）
    orig_from: str       # 原始主网发送地址（出处溯源用）
    orig_to: str         # 原始主网接收地址
    sender_idx: int      # 映射出的模拟账户索引（真正签名的账户）
    receiver_idx: int    # 映射出的模拟接收账户索引


def map_address(addr_hex: str, num_shards: int, num_users: int,
                user_base_index: int) -> t.Tuple[int, int]:
    """把一个十六进制地址映射成 (分片号, 模拟账户索引)。"""
    n = int(addr_hex, 16)
    return n % num_shards, user_base_index + (n % num_users)


def extract(csv_path: str, *, num_shards: int, num_users: int,
            user_base_index: int, value_floor_wei: int, value_cap_wei: int,
            limit: int) -> t.List[RealCtx]:
    """流式扫描 CSV，按过滤+映射规则取满 limit 笔 RealCtx 为止。"""
    out: t.List[RealCtx] = []
    with open(csv_path, "r", newline="") as f:
        for row in csv.DictReader(f):
            if len(out) >= limit:
                break
            try:
                from_addr, to_addr = row["from"], row["to"]
                value = int(row["value"])
            except (KeyError, ValueError):
                continue
            # 业务过滤：只留 成功执行的 纯EOA→EOA 价值转账（排除合约创建/交互、
            # 执行出错行）；金额须在 [floor, cap] 内——cap 同时挡掉主网清算巨鲸
            # 转账（>10 ETH 会一笔抽干 broker，与"流动性失衡"研究问题混淆）。
            if (row.get("toCreate") not in (None, "", "None")
                    or row.get("fromIsContract") == "1"
                    or row.get("toIsContract") == "1"
                    or row.get("isError") == "1"
                    or not to_addr or to_addr == "None"):
                continue
            if not (value_floor_wei <= value <= value_cap_wei):
                continue
            src, sender_idx = map_address(from_addr, num_shards, num_users, user_base_index)
            dst, receiver_idx = map_address(to_addr, num_shards, num_users, user_base_index)
            if src == dst or sender_idx == receiver_idx:
                continue
            out.append(RealCtx(
                ctx_id=f"real_{row['transactionHash'][:16]}",
                src_shard=src, dst_shard=dst, amount_wei=value,
                orig_from=from_addr, orig_to=to_addr,
                sender_idx=sender_idx, receiver_idx=receiver_idx,
            ))
    return out

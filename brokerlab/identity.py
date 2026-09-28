"""确定性 HD 账户派生 + 按 (地址, 分片) 管理的 nonce 计数器。

本模块承自旧三代 core/identity.py，修了两处：
- 派生结果带缓存（旧三代每次调用都从助记词重新派生一遍）；
- NonceManager 是普通实例，不再有全局单例状态。
"""
import typing as t

from eth_account import Account

Account.enable_unaudited_hdwallet_features()


class UserManager:
    """由一条助记词派生账户并按索引缓存；路径固定为 m/44'/60'/0'/0/{index}。"""

    def __init__(self, mnemonic: str) -> None:
        self._mnemonic = mnemonic
        self._cache: t.Dict[int, t.Any] = {}   # 账户索引 → 私钥对象（首次用到才派生）

    def account(self, index: int):
        """返回索引对应的账户对象；未缓存则派生后入缓存。"""
        acct = self._cache.get(index)
        if acct is None:
            acct = Account.from_mnemonic(
                self._mnemonic, account_path=f"m/44'/60'/0'/0/{index}"
            )
            self._cache[index] = acct
        return acct

    def address(self, index: int) -> str:
        """索引 → 地址字符串（内部仍走同一份缓存）。"""
        return self.account(index).address


class NonceManager:
    """Local monotonic nonce per (address, shard). The chain side is trusted
    only at sync time; see coordinator/broker loop for sync points.

    不变量（计划 §并发所有权表）：一个 (address, shard) 恰好一个写入者。
    本地计数器只增不回头，交易顺序即 nonce 顺序；跨线程/跨进程共享本实例
    就是 bug 的开始——所有权靠调用方结构保证，不靠这里加锁。
    """

    def __init__(self) -> None:
        self._next: t.Dict[t.Tuple[str, int], int] = {}   # (地址,分片) → 下一个要用的 nonce

    def next(self, address: str, shard: int) -> int:
        """取该 (地址,分片) 的下一个 nonce，同时把计数器 +1。只增不回头。"""
        key = (address, shard)
        n = self._next.get(key, 0)
        self._next[key] = n + 1
        return n

    def sync(self, address: str, shard: int, chain_nonce: int) -> None:
        """setup 期对齐：仅当链上值更大时抬高本地计数器（永不回退）。"""
        key = (address, shard)
        if chain_nonce > self._next.get(key, 0):
            self._next[key] = chain_nonce

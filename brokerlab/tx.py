"""真实签名的 ETH 转账服务：对着 anvil 集群签名、发送、等回执。

本模块承自旧三代 core/injector.py，为可行骨架做了简化（单进程；
批量提交等 M 系列接入流水线后再回来）。直改余额的能力只保留
`set_balance_setup` 一个名字：按设计红线，setup 充值之后任何路径都不得调用（审计 F2）。
"""
import time
import typing as t

from web3.exceptions import Web3RPCError

from .config import ChainCfg
from .identity import NonceManager, UserManager
from .chain import Connections

GAS_TRANSFER = 21000   # 一笔纯 ETH 转账的固定 gas 量（EVM 常数）


class TxService:
    """一整套"签名→发送→查回执→查余额"的链上动作；一个实例配一个进程。"""

    def __init__(self, conns: Connections, users: UserManager, chain_cfg: ChainCfg) -> None:
        self._conns = conns
        self._users = users
        self._cfg = chain_cfg
        self.nonces = NonceManager()
        # 实验度量用：每笔已发送交易的原始 RLP 字节数 / 每份回执的序列化字节数。
        # relay-vs-broker 通信量实验需要【实测】payload 尺寸，不拍常数（审计纪律）。
        self._tx_bytes: t.Dict[str, int] = {}
        self._receipt_bytes: t.Dict[str, int] = {}
        self._submit_block: t.Dict[str, int] = {}

    def tx_bytes(self, tx_hash: str) -> int:
        """该交易的签名后 RLP 字节数（发送时已记下）。"""
        return self._tx_bytes.get(tx_hash, 0)

    def receipt_bytes(self, tx_hash: str) -> int:
        """该交易回执 JSON 的序列化字节数（跨片证明的实测代理）。"""
        return self._receipt_bytes.get(tx_hash, 0)

    def submit_block(self, tx_hash: str) -> int:
        """链高（提交那一刻该分片的高度）——延迟的"入块跳数"以此为锚。"""
        return self._submit_block.get(tx_hash, 0)

    # ------------------------------------------------------------------
    def get_balance(self, address: str, shard: int) -> int:
        """读某地址在某分片的当前余额（wei）。"""
        # Anvil 在 interval mining 的出块边界偶发把同一次 ``latest`` 查询解析
        # 到相邻块，返回 BlockOutOfRangeError。这是只读、幂等调用，可以有限
        # 重试；其他 RPC 错误必须立即暴露，发送交易路径也绝不复用此逻辑。
        for attempt in range(5):
            try:
                return self._conns.web3(shard).eth.get_balance(address)
            except Web3RPCError as exc:
                if "BlockOutOfRangeError" not in str(exc) or attempt == 4:
                    raise
                time.sleep(0.05)
        raise AssertionError("unreachable")

    def block_number(self, shard: int) -> int:
        """读某分片当前块高（逻辑时钟的原始输入）。"""
        return self._conns.web3(shard).eth.block_number

    def sync_nonces(self, account_indexes: t.Iterable[int], shards: t.Iterable[int]) -> None:
        """只允许 setup 期调用：把本地 nonce 计数器对齐到链上 pending 计数。"""
        for idx in account_indexes:
            addr = self._users.address(idx)
            for shard in shards:
                # 与 get_balance 相同，Anvil 在 interval mining 出块边界也可能让
                # pending nonce 查询短暂命中相邻块。这里只重试 setup 期的只读、
                # 幂等查询；发送交易绝不重试，避免重复转账。
                for attempt in range(5):
                    try:
                        chain_nonce = self._conns.web3(shard).eth.get_transaction_count(
                            addr, "pending")
                        break
                    except Web3RPCError as exc:
                        if "BlockOutOfRangeError" not in str(exc) or attempt == 4:
                            raise
                        time.sleep(0.05)
                self.nonces.sync(addr, shard, chain_nonce)

    # ------------------------------------------------------------------
    def send_transfer(self, from_idx: int, to_address: str, amount_wei: int,
                      shard: int,
                      nonce: t.Optional[int] = None) -> str:
        """签并发出单笔 legacy ETH 转账，立刻返回哈希（不等回执）。

        为什么 gasPrice=0：与集群的零 base fee 配套，转账价值精确守恒——
        M1 的 wei 级差分校验全靠这一点。nonce 用本地计数器而不是每笔查链：
        所有权规则保证本 (账户,分片) 只有本进程在签，链上查询只是 setup
        阶段的一次性对齐（sync_nonces）。chainId 取自目标分片——签名绑定
        分片，跨片重放无效。

        nonce 显式给定（exp007 动态路由）：coordinator 串行预分配后随 CTX
        下发，签名者照用——"一个 (账户,分片,nonce) 一个签名者"。
        不给（None）则走本地计数器，与 exp001-006 的逐字节旧行为一致。
        """
        acct = self._users.account(from_idx)
        w3 = self._conns.web3(shard)
        if nonce is None:
            nonce = self.nonces.next(acct.address, shard)
        tx = {
            "chainId": w3.eth.chain_id,
            "nonce": nonce,
            "from": acct.address,
            "to": to_address,
            "value": int(amount_wei),
            "gas": GAS_TRANSFER,
            "gasPrice": 0,
        }
        signed = acct.sign_transaction(tx)
        raw = signed.raw_transaction
        blk_at_submit = w3.eth.block_number
        tx_hash = w3.eth.send_raw_transaction(raw)
        h = tx_hash.hex()
        self._tx_bytes[h] = len(raw)
        self._submit_block[h] = blk_at_submit
        return h

    def probe_receipt(self, tx_hash: str,
                      shard: int) -> t.Optional[t.Dict[str, int]]:
        """单探针：查一次回执，不睡眠、不重试。None = 尚未入块。

        wait_receipt（阻塞轮询）与 broker_engine（流水线 tick）共用的底座。
        三态语义在这里只覆盖 (None=未入块) / (dict=status 0|1)；
        超时判定由调用方负责——引擎侧的 timeout_s 是策略，探针不是。
        """
        w3 = self._conns.web3(shard)
        try:
            r = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            return None
        import json as _json
        # 回执 JSON 序列化尺寸 = 跨片包含证明 payload 的实测代理（偏保守：
        # RLP 编码会更小，高估只会让 relay 通信账单更贵，不会反过来）
        rsize = len(_json.dumps(dict(r), default=str))
        self._receipt_bytes[tx_hash] = rsize
        return {"status": int(r["status"]), "block": int(r["blockNumber"]),
                "receipt_bytes": rsize}

    def wait_receipt(self, tx_hash: str, shard: int,
                     timeout_s: float = 90.0) -> t.Optional[t.Dict[str, int]]:
        """阻塞轮询直到入块；返回 {'status':0|1,'block':h}，超时返回 None。

        三态语义（与旧三代 check_tx_status 对齐，M2+ 状态机直接复用）：
        None=超时未入块，0=入块但 revert，1=成功。失败与缺失必须可区分。
        """
        start = time.time()
        while time.time() - start < timeout_s:
            r = self.probe_receipt(tx_hash, shard)
            if r is not None:
                return r
            time.sleep(0.2)
        return None

    # ------------------------------------------------------------------
    def set_balance_setup(self, address: str, shard: int, amount_wei: int) -> None:
        """anvil_setBalance —— 只允许在 setup 充值阶段调用（实验循环开始之前）。

        设计红线（审计 F2 的教训）：实验开始后任何代码路径都不得再调用它。
        再平衡、结算必须走真实签名交易，否则区块容量开销不可测。
        名字里的 _setup 就是审查标记：diff 里出现非 setup 调用 = 拒绝合入。
        """
        w3 = self._conns.web3(shard)
        w3.provider.make_request(
            "anvil_setBalance", [address, hex(int(amount_wei))])

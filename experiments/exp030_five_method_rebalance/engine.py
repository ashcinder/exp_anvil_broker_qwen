"""Shared real burn/mint executor, source observations and receipt cost audit."""
import json

from brokerlab.broker_engine import build_services
from brokerlab.brokerchain import BURN_ADDRESS
from brokerlab.tdr_hook import TdrDynamicEngine
from .policies import ComparisonAgent


class AuditedTx:
    def __init__(self, tx, broker_account=None):
        self.inner = tx
        self.broker_account = broker_account
        self.records = {}
        self.kind = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def send_transfer(self, from_idx, to_address, amount_wei, shard, nonce=None):
        h = self.inner.send_transfer(from_idx, to_address, amount_wei, shard, nonce=nonce)
        category = self.kind
        if category is None:
            if to_address.lower() == BURN_ADDRESS.lower():
                category = "fee_burn" if from_idx == self.broker_account else "relay_burn"
            else:
                category = "broker_t2" if from_idx == self.broker_account else "broker_t1"
        self.records[h] = dict(tx_hash=h, shard=int(shard), kind=category,
                               from_idx=int(from_idx), amount_wei=int(amount_wei),
                               status=None, block=None, gas_used=None, gas_price_wei=None,
                               actual_gas_cost_wei=None, tx_bytes=self.inner.tx_bytes(h), receipt_bytes=None)
        return h

    def probe_receipt(self, tx_hash, shard):
        try:
            receipt = self.inner._conns.web3(shard).eth.get_transaction_receipt(tx_hash)
        except Exception:
            return None
        size = len(json.dumps(dict(receipt), default=str))
        self.inner._receipt_bytes[tx_hash] = size
        if tx_hash in self.records:
            gas = int(receipt["gasUsed"])
            price = int(receipt["effectiveGasPrice"])
            self.records[tx_hash].update(status=int(receipt["status"]), block=int(receipt["blockNumber"]),
                                         gas_used=gas, gas_price_wei=price,
                                         actual_gas_cost_wei=gas*price, receipt_bytes=size)
        return dict(status=int(receipt["status"]), block=int(receipt["blockNumber"]), receipt_bytes=size)


class ComparisonEngine(TdrDynamicEngine):
    def _after_ctx_submitted(self, f):
        super()._after_ctx_submitted(f)
        self.tdr.note_ctx(f.ctx["ctx_id"], f.b1_anchor, int(f.ctx["src_shard"]),
                          int(f.ctx["dst_shard"]), int(f.ctx["amount_wei"]))

    def _emit(self, row):
        super()._emit(row)
        if row["ok"] and row["route"] == "broker":
            self.tdr.note_settled(int(row["src"]), int(row["dst"]), int(row["amount_wei"]))

    def _submit_tdr_first(self, i):
        self._tx.kind = "rebalance_burn"
        try:
            super()._submit_tdr_first(i)
        finally:
            self._tx.kind = None

    def envelope(self):
        result = super().envelope()
        result["transactions"] = list(self._tx.records.values())
        return result


def engine_from_payload(payload, ctx_q, rep_q, credit_sink, credit_in):
    raw, users, scale = build_services(payload["cfg"])
    p = payload["params"]
    tp = payload.get("tdr_params") or p["comparison_tdr_defaults"]
    agent_args = {k: v for k, v in tp.items() if k not in ("policy", "block_poll_s")}
    agent_args.update(policy="topup", initial_wei=int(next(iter(payload["init_wei_by_shard"].values()))),
                      method=p["comparison_method"], lai_curvature=p["lai_curvature"], lai_uniform_floor=p["lai_uniform_floor"])
    agent = ComparisonAgent(**agent_args)
    tx = AuditedTx(raw, scale.broker_base_index+int(payload["broker_idx"]))
    return ComparisonEngine(tx, users, broker_idx=int(payload["broker_idx"]),
                            broker_base_index=scale.broker_base_index, ctx_q=ctx_q, rep_q=rep_q,
                            init_wei_by_shard={int(k): int(v) for k, v in payload["init_wei_by_shard"].items()},
                            credit_sink=credit_sink, credit_in=credit_in,
                            max_inflight=int(p["max_inflight"]), poll_s=float(p["poll_s"]),
                            probe_s=float(p["probe_s"]), probe_first_s=float(p["probe_first_s"]),
                            timeout_s=float(p["engine_timeout_s"]),
                            tdr_agent=agent, block_poll_s=float(tp.get("block_poll_s", 1.0)),
                            fee_broker_wei=int(p.get("fee_broker_wei", 0)),
                            fee_burn_wei=int(p.get("fee_burn_wei", 0)))

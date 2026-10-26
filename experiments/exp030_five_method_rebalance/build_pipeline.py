"""Create an isolated, auditable fork of the corrected exp025/026 runner."""
from pathlib import Path
import hashlib
import json
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    source = ROOT / "experiments/exp025_regime_shift_sensitivity/pipeline.py"
    text = source.read_text(encoding="utf-8")
    changes = []

    def change(old, new, name):
        nonlocal text
        if text.count(old) != 1:
            raise ValueError(f"Upstream block changed: {name}, count={text.count(old)}")
        text = text.replace(old, new)
        changes.append(name)

    change('from brokerlab.tdr_hook import tdr_engine_from_payload',
           'from .engine import engine_from_payload, AuditedTx\nfrom .reporting import export_audit', 'isolated executor')
    change('        tx, users, scale = build_services(cfg_plain)\n        nb = scale.num_brokers',
           '        raw_tx, users, scale = build_services(cfg_plain)\n        tx = AuditedTx(raw_tx)\n        nb = scale.num_brokers', 'coordinator receipt audit')
    change('                    h = tx.send_transfer(scale.coordinator_index,',
           '                    tx.kind = "rebalance_mint" if str(rq["ref"]).startswith("T") else "relay_mint"\n                    h = tx.send_transfer(scale.coordinator_index,', 'classify real mint legs')
    change('                   "aborted": aborted, "error": None})',
           '                   "transactions": list(tx.records.values()),\n                   "aborted": aborted, "error": None})', 'mint receipt exports')
    change('                est[b_sel][src] += v\n                est[b_sel][dst] -= v',
           '                # Reserve outgoing capacity; unconfirmed incoming is unavailable.\n                est[b_sel][dst] -= v', 'remove phantom incoming credit')
    change('                    est[ab][src] -= v\n                    est[ab][dst] += v',
           '                    est[ab][dst] += v', 'release outgoing reservation only')
    change('                        est[ab][src] += v\n                        est[ab][dst] -= v',
           '                        est[ab][src] += v + params["fee_broker_wei"]\n                        est[ab][dst] -= v', 'credit confirmed service and retained fee')
    change('        if payload.get("tdr_params"):\n            eng = tdr_engine_from_payload(payload, ctx_q, rep_q, req_q, ack_q)\n        else:\n            eng = dynamic_engine_from_payload(payload, ctx_q, rep_q, req_q, ack_q)',
           '        eng = engine_from_payload(payload, ctx_q, rep_q, req_q, ack_q)', 'five explicit policies')
    anchor = '        cfg_plain = {"chain": asdict(cfg.chain), "scale": asdict(cfg.scale)}\n        init_wei = int(fund_eth * ETH)'
    change(anchor,
           '        params["comparison_method"] = cfg.exp["comparison_method"]\n'
           '        params["lai_curvature"] = cfg.exp["lai_curvature"]\n'
           '        params["lai_uniform_floor"] = cfg.exp["lai_uniform_floor"]\n'
           '        params["comparison_tdr_defaults"] = dict(tdr_params or {\n'
           '            "num_shards": cfg.chain.num_shards, "window_blocks": cfg.exp["tdr_window_blocks"],\n'
           '            "epsilon": cfg.exp["tdr_epsilon"], "q_min": cfg.exp["tdr_q_min"], "chi_blocks": cfg.exp["tdr_chi_blocks"],\n'
           '            "timeout_blocks": cfg.exp["tdr_timeout_blocks"], "offset_blocks": 0,\n'
           '            "demand_ewma_half_life": cfg.exp["tdr_ewma_half_life"], "block_poll_s": cfg.exp["tdr_block_poll_s"]})\n'
           + anchor, 'explicit method parameters')
    change('payload["tdr_params"] = dict(my_tdr, offset_blocks=b % 5)',
           'payload["tdr_params"] = dict(my_tdr, offset_blocks=b % int(cfg.exp["comparison_offset_mod"]))', 'reviewable startup offsets')
    change('        add_communication_bill(rows, int(cfg.exp.get("match_ctrl_bytes", 100)))',
           '        stats["audit"] = export_audit(arm_dir, envs, coord_env, stats, cfg)\n'
           '        add_communication_bill(rows, int(cfg.exp.get("match_ctrl_bytes", 100)))', 'full cost/event/broker ledger')
    text = '# Isolated exp030 fork; original exp025/026 and submit_regime_shift are preserved.\n'+text
    (HERE/"pipeline.py").write_text(text, encoding="utf-8")
    (HERE/"upstream_manifest.json").write_text(json.dumps(dict(
        source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        blueprint=str(ROOT/"submit_regime_shift"), inherited_exp025_fixes=[
            "receipt probe starvation", "modern move_events interface", "same-shard hotspot collision",
            "FIFO closure", "reserved balance retained during routing"], additional_changes=changes),
        ensure_ascii=False, indent=2), encoding="utf-8")
    cfg = yaml.safe_load((ROOT/"experiments/exp026_regime_shift_paper_figures/config.yaml").read_text(encoding="utf-8"))
    cfg["label"] = "exp030_five_method_rebalance"
    cfg["exp"].update(ctx_per_broker=800, max_backlog=40000, repetitions=3,
                       comparison_method="tdr", lai_curvature=1.0, lai_uniform_floor=0.05,
                       tdr_epsilon=0.95, tdr_q_min=0.3, tdr_ewma_half_life=20.0, comparison_offset_mod=5, methods="brokerchain,horcrux,shaduf,lai,tdr",
                       cost_scenarios_wei=[0,2100000,21000000,210000000],
                       pilot=False, profit_broker_idx=0)
    cfg["exp"].pop("arms",None)
    cfg["exp"].pop("tdr_offset_blocks",None)
    cfg["tdr"].update(window_blocks=20,epsilon=0.95,q_min=0.3,chi_blocks=10,timeout_blocks=20)
    cfg["chain"]["base_port"] = 18600
    (HERE/"config.yaml").write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False),encoding="utf-8")


if __name__ == "__main__":
    main()

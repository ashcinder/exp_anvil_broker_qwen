"""Measured receipt accounting; explicit nominal costs, paired run-level statistics."""
from collections import defaultdict
from decimal import Decimal
import csv
import json
import math
import statistics
from pathlib import Path
from .design import evaluation_metrics


def write_csv(path, rows, fields=None):
    path = Path(path)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w",encoding="utf-8-sig",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig",newline="") as stream:
        return list(csv.DictReader(stream))


def export_audit(arm_dir, envs, coordinator, stats, cfg):
    receipts = []
    decisions, links = [], []
    for broker, env in envs.items():
        receipts.extend(dict(broker_idx=broker, **r) for r in env.get("transactions", []))
        decisions.extend(dict(broker_idx=broker, **r) for r in env.get("tdr",{}).get("decisions",[]))
        links.extend(dict(broker_idx=broker, **r) for r in env.get("tdr",{}).get("event_ctx_links",[]))
    receipts.extend(dict(broker_idx=-1, **r) for r in coordinator.get("transactions",[]))
    if len({(r["shard"],r["tx_hash"]) for r in receipts}) != len(receipts):
        raise ValueError("Duplicate receipt record")
    audited = [r for r in receipts if r["status"] is not None]
    actual_cost = sum(r["actual_gas_cost_wei"] for r in audited)
    rebalances = [r for r in audited if r["kind"].startswith("rebalance_")]
    burns = [r for r in rebalances if r["kind"] == "rebalance_burn"]
    cost_each = int(cfg.exp["profit_tdr_cost_wei"])
    fees = int(stats["retained_fee_income_wei"])
    known_ids = {(b, cid) for b,e in envs.items() for cid in [r["ctx_id"] for r in e.get("rows",[])]}
    if any((r["broker_idx"],r["ctx_id"]) not in known_ids or r["observed_block"]>r["decision_block"] for r in links):
        raise ValueError("Invalid or future trigger reference")
    if len({(r["broker_idx"],r["ctx_id"]) for r in links}) != len(links):
        raise ValueError("Trigger references double counted")
    audit = dict(submitted_onchain_legs=len(receipts), confirmed_onchain_legs=len(audited),
                 broker_count=len(envs),
                 missing_receipts=len(receipts)-len(audited),
                 receipt_leg_count_ok=len(audited)==stats["legs"]["ctx_legs"]+stats["legs"]["tdr_legs"],
                 rebalance_burn_legs=len(burns), rebalance_mint_legs=len(rebalances)-len(burns),
                 onchain_gas_used=sum(r["gas_used"] for r in audited),
                 rebalance_gas_used=sum(r["gas_used"] for r in rebalances),
                 actual_chain_gas_cost_wei=actual_cost,
                 actual_rebalance_gas_cost_wei=sum(r["actual_gas_cost_wei"] for r in rebalances),
                 modeled_rebalance_cost_wei=len(burns)*cost_each,
                 nominal_cost_per_burn_wei=cost_each,
                 modeled_net_profit_wei=fees-len(burns)*cost_each,
                 trigger_reference_ctx_count=len(links), rebalance_events=len(decisions),
                 observed_ctx_count=sum(e.get("tdr",{}).get("observed_ctx_count",0) for e in envs.values()),
                 decision_time_ms=sum(e.get("tdr",{}).get("decision_ns",0) for e in envs.values())/1e6,
                 decision_calls=sum(e.get("tdr",{}).get("decision_calls",0) for e in envs.values()),
                 rebalance_signed_tx_bytes=sum(r["tx_bytes"] for r in rebalances),
                 rebalance_receipt_bytes=sum(r["receipt_bytes"] for r in rebalances),
                 interpretation="trigger_reference_ctx_count counts observations since previous event; NOT a causal count; nominal costs are ex-post, gasPrice=0")
    write_csv(arm_dir/"transactions.csv",receipts)
    write_csv(arm_dir/"rebalance_decisions.csv",decisions,
              ["broker_idx","event_id","decision_block","reference_ctx_count","transfer_count","amount_wei","available_wei"])
    write_csv(arm_dir/"event_ctx_links.csv",links,
              ["broker_idx","event_id","ctx_id","observed_block","decision_block"])
    (arm_dir/"audit.json").write_text(json.dumps(audit,indent=2),encoding="utf-8")
    broker_rows = []
    for b,e in sorted(envs.items()):
        rows = e.get("rows",[])
        own_burns = sum(r["broker_idx"]==b for r in burns)
        income = sum(int(r.get("fee_broker_wei",0)) for r in rows if r["ok"])
        td = e.get("tdr",{})
        vals = list(map(int,e.get("final_mirror",{}).values()))
        mean = sum(vals)/len(vals) if vals else 0
        cv = (math.sqrt(sum((v-mean)**2 for v in vals)/len(vals))/mean) if mean else 0
        broker_rows.append(dict(broker_idx=b, ctx=len(rows), served=sum(r["ok"] and r["route"]=="broker" for r in rows),
                                relay=sum(r["ok"] and r["route"]=="relay" for r in rows),
                                events=td.get("events_opened",0), transfers=td.get("transfers_done",0),
                                moved_wei=td.get("moved_wei",0), trigger_reference_ctx=td.get("trigger_reference_ctx_count",0),
                                fee_income_wei=income, modeled_cost_wei=own_burns*cost_each,
                                modeled_profit_wei=income-own_burns*cost_each,
                                final_balance_cv=cv, final_min_balance_wei=min(vals) if vals else None))
        broker_rows[-1].update(evaluation_metrics(broker_rows[-1]['relay'],td.get('transfers_done',0),income,cost_each,1))
    write_csv(arm_dir/"broker_metrics.csv",broker_rows)
    block_rows=defaultdict(lambda:dict(legs=0, gas_used=0, rebalance_legs=0, gas_limit=cfg.chain.gas_limit))
    for r in audited:
        item=block_rows[(r["shard"],r["block"])]
        item["legs"]+=1; item["gas_used"]+=r["gas_used"]
        item["rebalance_legs"]+=int(r["kind"].startswith("rebalance_"))
    write_csv(arm_dir/"capacity_by_block.csv",
              [dict(shard=s,block=h,**v,gas_utilization=v["gas_used"]/v["gas_limit"]) for (s,h),v in sorted(block_rows.items())])
    return audit


def validate_audit(stats, arm_dir, workload):
    a=stats["audit"]
    if (a["missing_receipts"] or not a["receipt_leg_count_ok"] or a["observed_ctx_count"]!=len(workload)
            or a["rebalance_events"]!=stats["tdr"]["events_opened"]
            or a["rebalance_burn_legs"]!=stats["tdr"]["transfers_done"]
            or a["rebalance_mint_legs"]!=stats["tdr"]["transfers_done"]):
        raise ValueError("Receipt, event or observation audit failed")
    rows=read_csv(arm_dir/"transactions.csv")
    if len(rows)!=a["submitted_onchain_legs"] or any(r["status"]!="1" for r in rows):
        raise ValueError("Missing/failed receipt")
    if sum(int(r["actual_gas_cost_wei"]) for r in rows)!=a["actual_chain_gas_cost_wei"]:
        raise ValueError("Gas ledger mismatch")
    b=read_csv(arm_dir/"broker_metrics.csv")
    if (sum(int(r["fee_income_wei"]) for r in b)!=stats["retained_fee_income_wei"]
            or sum(int(r["modeled_profit_wei"]) for r in b)!=a["modeled_net_profit_wei"]):
        raise ValueError("Broker fee/profit mismatch")


def metric_record(task, stats):
    a=stats["audit"]
    primary=evaluation_metrics(stats["relayed"],stats["tdr"]["transfers_done"],
                               stats["retained_fee_income_wei"],a["nominal_cost_per_burn_wei"],a["broker_count"])
    return dict(method=task["method"], round=task["round"],route_seed=task["route_seed"],
                **primary, relay_plus_adjustment_ratio=primary["relay_plus_adjustment_ctx"]/stats["n"],
                ctx=stats["n"], broker_ctx=stats["served"], failed_ctx=stats["failed"],
                broker_service_ratio=stats["served"]/stats["n"], relay_ratio=stats["relayed"]/stats["n"],
                rebalance_events=stats["tdr"]["events_opened"], rebalance_transfers=stats["tdr"]["transfers_done"],
                trigger_reference_ctx=a["trigger_reference_ctx_count"],
                moved_wei=stats["tdr"]["moved_wei"], moved_eth=str(Decimal(stats["tdr"]["moved_wei"])/10**18),
                ctx_legs=stats["legs"]["ctx_legs"], rebalance_legs=stats["legs"]["tdr_legs"],
                receipt_legs=a["confirmed_onchain_legs"],gas_used=a["onchain_gas_used"],
                rebalance_gas_used=a["rebalance_gas_used"],
                fee_income_wei=stats["retained_fee_income_wei"], modeled_cost_wei=a["modeled_rebalance_cost_wei"],
                modeled_profit_wei=a["modeled_net_profit_wei"], actual_gas_cost_wei=a["actual_chain_gas_cost_wei"],
                rebalance_burn_legs=a["rebalance_burn_legs"],
                throughput_ctx_per_s=stats["throughput_ctx_per_s"],latency_p50_s=stats["e2e_secs"]["p50"],
                latency_p95_s=stats["e2e_secs"]["p95"],wall_ctx_s=stats["wall_ctx_s"],
                decision_time_ms=a["decision_time_ms"],gates_ok=True)


def aggregate(records):
    results=[]
    for method in dict.fromkeys(r["method"] for r in records):
        group=[r for r in records if r["method"]==method]
        for key,value in group[0].items():
            if key=='broker_mean_profit_wei':
                value=float(value)
            if key in ("round","route_seed","gates_ok") or not isinstance(value,(int,float)):
                continue
            vals=[float(r[key]) if key=='broker_mean_profit_wei' else r[key] for r in group]
            n=len(vals); mean=statistics.fmean(vals)
            sd=statistics.stdev(vals) if n>1 else None
            # Small-sample Student t; no confidence interval for one run.
            t95={1:12.706,2:4.303,3:3.182,4:2.776,5:2.571,6:2.447,7:2.365,8:2.306,9:2.262}.get(n-1,1.96)
            half=t95*sd/math.sqrt(n) if sd is not None else None
            results.append(dict(method=method,metric=key,n=n,mean=mean,median=statistics.median(vals),
                                std=sd,min=min(vals),max=max(vals),
                                ci95_low=mean-half if half is not None else None,
                                ci95_high=mean+half if half is not None else None))
    return results


def build_report(run, manifest, cfg):
    records=[]
    for task in manifest["tasks"]:
        if task["status"]=="complete":
            stats=json.loads((run/task["summary"]).read_text(encoding="utf-8"))["stats"]
            records.append(metric_record(task,stats))
    write_csv(run/"raw_results.csv",records)
    write_csv(run/"method_summary.csv",aggregate(records))
    primary_keys=['method','round','ctx','relay_ctx','adjustment_ctx','relay_plus_adjustment_ctx',
                  'relay_plus_adjustment_ratio','fee_income_wei','modeled_cost_wei',
                  'broker_total_profit_wei','broker_mean_profit_wei']
    primary=[{key:r[key] for key in primary_keys} for r in records]
    write_csv(run/"primary_metrics.csv",primary,primary_keys)
    write_csv(run/"primary_summary.csv",aggregate(primary))
    costs=[]; pairs=[]
    for r in records:
        for cost in cfg.exp["cost_scenarios_wei"]:
            costs.append(dict(method=r["method"],round=r["round"],nominal_cost_per_burn_wei=cost,
                              fee_income_wei=r["fee_income_wei"],modeled_cost_wei=r["rebalance_burn_legs"]*cost,
                              modeled_profit_wei=r["fee_income_wei"]-r["rebalance_burn_legs"]*cost,
                              ex_post=True))
        baseline=next((b for b in records if b["round"]==r["round"] and b["method"]=="brokerchain"),None)
        if baseline:
            delta_income=r["fee_income_wei"]-baseline["fee_income_wei"]
            pairs.append(dict(method=r["method"],round=r["round"],
                              extra_broker_ctx=r["broker_ctx"]-baseline["broker_ctx"],
                              relay_reduction=baseline["relay_ctx"]-r["relay_ctx"],
                              primary_ctx_reduction=baseline["relay_plus_adjustment_ctx"]-r["relay_plus_adjustment_ctx"],
                              extra_fee_income_wei=delta_income,
                              net_profit_delta_wei=r["modeled_profit_wei"]-baseline["modeled_profit_wei"],
                              breakeven_cost_per_burn_wei=delta_income/r["rebalance_burn_legs"] if r["rebalance_burn_legs"] else None))
    write_csv(run/"cost_sensitivity.csv",costs)
    write_csv(run/"paired_vs_brokerchain.csv",pairs)
    write_csv(run/"paired_summary.csv",aggregate(pairs))
    complete=len(records)==len(manifest["tasks"])
    doc=dict(complete=complete,pilot=manifest["pilot"],completed_tasks=len(records),planned_tasks=len(manifest["tasks"]),
             design_version=2,
             primary_metrics={"minimize":"relay_plus_adjustment_ctx", "maximize":"broker_total_profit_wei"},
             paper_algorithm_reproduction_complete=False,
             limitations=["LAI(1) exact continuous decision rule with custom wallet demand, rounding and delayed execution adapter",
                          "Shaduf++ privacy and channel protocol not implemented", "Horcrux virtual channels not implemented",
                          "BrokerChain core baseline, not entire journal system", "nominal fees are ex-post, not deducted from balances"],
             raw_results=records)
    (run/"report.json").write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# 五方法对比结果", "", f"完整状态：{complete}；pilot：{manifest['pilot']}；完成 {len(records)}/{len(manifest['tasks'])} 次。",
           "", "**OPRE 已排除；第四臂为公式可核验的 LAI(1)。各臂含钱包适配，不能将本表称为四篇论文完整协议复现。**",
           "", "金额均为 wei；模型成本在链外按真实 burn 次数结算，链上 gas 另列。",
           "", "主指标：Relay＋资金调整CTX，越小越好；broker总净利润，越大越好。每笔burn+mint只计1笔资金调整CTX。",
           "", "| 方法 | 轮次 | Relay CTX | 资金调整CTX | 两者总数↓ | Broker服务CTX | 收入wei | 模型成本wei | Broker总净利润wei↑ | 平均每broker利润wei |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in records:
        lines.append(f"| {r['method']} | {r['round']} | {r['relay_ctx']} | {r['adjustment_ctx']} | {r['relay_plus_adjustment_ctx']} | {r['broker_ctx']} | {r['fee_income_wei']} | {r['modeled_cost_wei']} | {r['broker_total_profit_wei']} | {r['broker_mean_profit_wei']} |")
    lines += ["", "主表：primary_metrics.csv；主指标整轮均值及波动：primary_summary.csv。",
              "不把两个目标加权成单一得分。若总CTX更少但利润更低，明确报告该权衡。"]
    lines += ["", "更多指标：raw_results.csv；轮次级均值/中位数/样本标准差/95% t区间：method_summary.csv。",
              "配对增益和盈亏平衡费用：paired_vs_brokerchain.csv、paired_summary.csv；事后费用敏感性：cost_sensitivity.csv。",
              "单次运行下不生成置信区间；broker/CTX不能当作独立重复样本。",
              "事件参考CTX是自上次事件以来已观测并归档的交易数，不表示每笔交易因果触发了调拨。"]
    (run/"RESULTS_CN.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return complete

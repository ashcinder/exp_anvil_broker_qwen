"""Read completed outputs: phase/shard details, economics time series and PNG charts."""
import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from experiments.exp030_five_method_rebalance.reporting import read_csv,write_csv


def analyze(run):
    manifest=json.loads((run/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("invalidated_reason") or manifest.get("superseded_reason"):
        raise ValueError("Cannot analyze invalidated or superseded comparison")
    report=json.loads((run/"report.json").read_text(encoding="utf-8"))
    if report.get('design_version') != 2:
        raise ValueError("This analyzer requires design v2; historical reports remain unchanged")
    if not report["complete"]:
        raise ValueError("All five methods/rounds must finish before final analysis")
    params=json.loads((run/"params.json").read_text(encoding="utf-8"))
    exp=params["exp"]; n=report["raw_results"][0]["ctx"]
    start=int(n*exp["shift_start_frac"]); step=max(1,int(n*exp["shift_period_frac"]))
    phases=[]; shards=[]; traces=[]; feedback=[]; balance_metrics=[]
    for task in manifest["tasks"]:
        directory=run/"tasks"/task["id"]/task["method"]
        rows=read_csv(directory/"ctx_rows.csv")
        transactions=read_csv(directory/"transactions.csv")
        moves=read_csv(directory/"tdr_moves.csv")
        brokers=read_csv(directory/"broker_metrics.csv")
        from statistics import mean
        balance_metrics.append(dict(method=task["method"],round=task["round"],
                                    mean_broker_final_cv=mean(float(b["final_balance_cv"]) for b in brokers),
                                    max_broker_final_cv=max(float(b["final_balance_cv"]) for b in brokers),
                                    minimum_subaccount_balance_wei=min(int(b["final_min_balance_wei"]) for b in brokers),
                                    interpretation="unweighted balance dispersion; does not measure demand fit"))
        by_phase=defaultdict(lambda:dict(ctx=0,broker_ctx=0,relay_ctx=0,fee_income_wei=0,amount_wei=0))
        by_shard=defaultdict(lambda:dict(ctx=0,broker_ctx=0,relay_ctx=0,amount_wei=0))
        events=defaultdict(lambda:dict(income_wei=0,modeled_cost_wei=0,completed_broker_ctx=0,completed_relay_ctx=0))
        for r in rows:
            pos=int(r["arrival_pos"])
            phase="natural_prefix" if pos<start else f"shift_slot_{(pos-start)//step:02d}"
            for item in (by_phase[phase],by_shard[int(r["dst"])]):
                item["ctx"]+=1;item[r["route"]+"_ctx"]+=1;item["amount_wei"]+=int(r["amount_wei"])
            by_phase[phase]["fee_income_wei"]+=int(r["fee_broker_wei"])
            block=int(r["t2_block"])
            events[block]["income_wei"]+=int(r["fee_broker_wei"])
            events[block]["completed_"+r["route"]+"_ctx"]+=1
        for tx in transactions:
            if tx["kind"]=="rebalance_burn":
                events[int(tx["block"])]["modeled_cost_wei"]+=int(exp["profit_tdr_cost_wei"])
        income=cost=0
        for block,v in sorted(events.items()):
            income+=v["income_wei"];cost+=v["modeled_cost_wei"]
            traces.append(dict(method=task["method"],round=task["round"],block=block,**v,
                               cumulative_income_wei=income,cumulative_cost_wei=cost,
                               cumulative_profit_wei=income-cost))
        stats=json.loads((run/task["summary"]).read_text(encoding="utf-8"))["stats"]
        if income!=stats["retained_fee_income_wei"] or cost!=stats["audit"]["modeled_rebalance_cost_wei"]:
            raise ValueError("Profit trajectory does not reconcile")
        phases.extend(dict(method=task["method"],round=task["round"],phase=k,**v,
                           relay_ratio=v["relay_ctx"]/v["ctx"]) for k,v in by_phase.items())
        shards.extend(dict(method=task["method"],round=task["round"],dst_shard=k,**v,
                           relay_ratio=v["relay_ctx"]/v["ctx"]) for k,v in sorted(by_shard.items()))
        seen=set(); reversals=0; reversed_amount=0
        for mv in sorted(moves,key=lambda r:(int(r["burn_block"]),int(r["broker_idx"]))):
            b,s,d=int(mv["broker_idx"]),int(mv["src_shard"]),int(mv["dst_shard"])
            if (b,d,s) in seen:
                reversals+=1;reversed_amount+=int(mv["amount_wei"])
            seen.add((b,s,d))
        feedback.append(dict(method=task["method"],round=task["round"],
                              transfers=len(moves), reverse_direction_transfers=reversals,
                              reverse_direction_amount_wei=reversed_amount,
                              definition="same broker had earlier transfer on the reverse pair; descriptive, not proof of harmful oscillation"))
    write_csv(run/"phase_metrics.csv",phases)
    write_csv(run/"destination_shard_metrics.csv",shards)
    write_csv(run/"economics_by_block.csv",traces)
    write_csv(run/"feedback_metrics.csv",feedback)
    write_csv(run/"balance_metrics.csv",balance_metrics)
    detailed_report(run,report,phases,balance_metrics)
    plot(run,report["raw_results"])
    (run/"analysis_metadata.json").write_text(json.dumps(dict(
        phase_axis="arrival position; natural prefix then sequential shift slots",
        income_recognition="user CTX completion at t2_block; final fees exact",
        cost_recognition="real rebalance burn receipt block, nominal ex-post cost",
        figure_error_bars="sample standard deviation over whole runs, not CI",
        script_sha256=__import__('hashlib').sha256(Path(__file__).read_bytes()).hexdigest()),indent=2),encoding="utf-8")


def plot(run,records):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import statistics
    methods=["brokerchain","horcrux","shaduf","lai","tdr"]
    labels=["BrokerChain\ncore","Horcrux\ninspired","Shaduf AB\nadapted","LAI(1)\nadapted","TDR"]
    colors=["#7f8c8d","#2874a6","#8e44ad","#d68910","#148f77"]
    fig,axes=plt.subplots(2,2,figsize=(12,8),layout="constrained")
    for axis,(metric,title,scale) in zip(axes.flat,[("relay_plus_adjustment_ctx","PRIMARY: Relay + adjustment CTXs (lower better)",1),
                                                 ("broker_total_profit_wei","PRIMARY: Broker total profit (10^9 wei; higher better)",1e9),
                                                 ("relay_ctx","Relay user CTXs",1),
                                                 ("adjustment_ctx","Adjustment CTXs (one burn + mint = one CTX)",1)]):
        groups=[[r[metric]/scale for r in records if r["method"]==m] for m in methods]
        axis.bar(range(5),[statistics.mean(g) for g in groups],
                 yerr=[statistics.stdev(g) if len(g)>1 else 0 for g in groups],color=colors,capsize=4)
        axis.set_xticks(range(5),labels,fontsize=9);axis.set_title(title)
        axis.axhline(0,color="#333333",linewidth=.7);axis.grid(axis="y",alpha=.2)
    fig.suptitle("Wallet-local adapted policies: whole-run means +/- sample SD\nPilot data; verified LAI rule with wallet adapter" if json.loads((run/'report.json').read_text())["pilot"] else
                 "Wallet-local adapted policies: whole-run means +/- sample SD\nVerified LAI rule with wallet adapter",fontsize=13)
    fig.savefig(run/"comparison.png",dpi=180)
    plt.close(fig)


def detailed_report(run,report,phases,balances):
    from statistics import mean
    records=report["raw_results"]
    methods=["brokerchain","horcrux","shaduf","lai","tdr"]
    lines=["# 五方法主指标对比", "",
           "本表仅汇总本批全部通过完整性门的独立运行。pilot不是正式规模结论；原协议适配边界见学习笔记。",
           "", "## 整轮均值", "",
           "金额单位：百万wei。成本按本批design.json中的单笔资金调整CTX费用计算；Anvil真实gas费用另列。",
           "", "| 方法 | Relay | 资金调整CTX | Relay＋调整CTX↓ | Broker服务CTX | 收入 | 模型成本 | Broker总净利润↑ | 平均每broker利润 |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for method in methods:
        group=[r for r in records if r["method"]==method]
        cells=[mean(r[k] for r in group) for k in ("relay_ctx","adjustment_ctx","relay_plus_adjustment_ctx","broker_ctx")]
        cells += [mean(r[k] for r in group)/1e6 for k in ("fee_income_wei","modeled_cost_wei","broker_total_profit_wei")]
        cells += [mean(float(r['broker_mean_profit_wei']) for r in group)/1e6]
        lines.append("| "+method+" | "+" | ".join(f"{v:.3f}" for v in cells)+" |")
    lines += ["", "## 方向切换阶段", "", "按照用户交易到达序号划分阶段，以下为全部整轮合计；热点阶段包含切换后的全部请求。",
              "", "| 方法 | 阶段 | 用户CTX | Relay | Relay比例 |", "|---|---|---:|---:|---:|"]
    for method in methods:
        for label in ("natural_prefix","hotspot_switching"):
            group=[p for p in phases if p["method"]==method and (p["phase"]=="natural_prefix")== (label=="natural_prefix")]
            n=sum(p["ctx"] for p in group); relay=sum(p["relay_ctx"] for p in group)
            lines.append(f"| {method} | {label} | {n} | {relay} | {relay/n:.2%} |")
    lines += ["", "主指标为Relay＋资金调整CTX，以及broker净利润；不将事件次数或参考用户CTX计入主指标。",
              "辅助数据仍保留在raw_results.csv及broker_metrics.csv，包含事件数、延迟、gas、余额CV和逐broker利润。",
              "余额CV越低表示越接近均匀分布，但热点需求下均匀不等于最佳；应与Relay共同阅读。",
              "逐轮原件：RESULTS_CN.md；整轮波动/置信区间：method_summary.csv；配对增益及盈亏平衡费用：paired_vs_brokerchain.csv。",
              "每broker、分片、确认腿、阶段及成本扫描结果均留在同一目录。费用扫描不改变资金轨迹，不是不同真实gas价格下重跑。"]
    (run/"DETAILED_RESULTS_CN.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("run",type=Path)
    analyze(parser.parse_args().run.resolve())

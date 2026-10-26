"""Five-method paired Anvil runs; independent chains; resumable provenance."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from brokerlab.config import apply_overrides,load_config,to_params_dict
from brokerlab.procmon import RSSWatchdog
from experiments.exp025_regime_shift_sensitivity.runner import free_port_block,write_json,digest
from experiments.exp025_regime_shift_sensitivity.paper_data import validate_arm,export_arm_data
from experiments.exp030_five_method_rebalance import pipeline
from experiments.exp030_five_method_rebalance.policies import METHODS
from experiments.exp030_five_method_rebalance.reporting import write_csv,validate_audit,build_report
from experiments.exp030_five_method_rebalance.design import review_design


def tasks_for(cfg):
    methods=cfg.exp["methods"].split(",")
    if set(methods)!=set(METHODS) or len(methods)!=5:
        raise ValueError("Exactly the five documented methods are required")
    tasks=[]
    for rep in range(1,int(cfg.exp["repetitions"])+1):
        seed=int(cfg.exp["route_seed"])+(rep-1)*int(cfg.exp["route_seed_step"])
        # Rotate execution order to avoid always giving one arm the cold run.
        order=methods[(rep-1)%5:]+methods[:(rep-1)%5]
        tasks.extend(dict(id=f"{method}_r{rep}",method=method,round=rep,route_seed=seed,status="pending") for method in order)
    return tasks


def fingerprints():
    files=sorted(HERE.glob("*.py"))+sorted((HERE/"analysis").glob("*.py"))+sorted((ROOT/"brokerlab").glob("*.py"))
    files += [ROOT/"experiments/exp025_regime_shift_sensitivity/paper_data.py",
              ROOT/"experiments/exp025_regime_shift_sensitivity/runner.py",HERE/"upstream_manifest.json"]
    return {str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}


def verify_task(run,task,workload,workload_hash):
    doc=json.loads((run/task["summary"]).read_text(encoding="utf-8"))
    if doc["task_id"]!=task["id"] or doc["workload_sha256"]!=workload_hash or doc["route_seed"]!=task["route_seed"]:
        raise ValueError("Saved task provenance mismatch")
    directory=run/"tasks"/task["id"]/task["method"]
    validate_arm(doc["stats"],directory,workload)
    validate_audit(doc["stats"],directory,workload)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=HERE/"config.yaml")
    parser.add_argument("--set",action="append",default=[])
    parser.add_argument("--pilot",action="store_true",help="16 shards, 4 brokers, 1000 CTX/arm, 2 whole rounds")
    parser.add_argument("--smoke",action="store_true",help="16 shards, 1 broker, 80 CTX/arm, 1 round")
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument("--resume",type=Path)
    parser.add_argument("--output-root",type=Path,default=HERE/"out")
    args=parser.parse_args()
    if args.pilot and args.smoke:
        parser.error("Choose pilot or smoke")
    cfg=apply_overrides(load_config(args.config),args.set)
    if args.pilot or args.smoke:
        nb=4 if args.pilot else 1
        cfg=replace(cfg,scale=replace(cfg.scale,num_brokers=nb))
        cfg=apply_overrides(cfg,[f"exp.ctx_per_broker={250 if args.pilot else 80}",
                                 f"exp.repetitions={2 if args.pilot else 1}",
                                 f"exp.rate={16 if args.pilot else 5}","exp.balances_eth=5",
                                 "exp.profit_broker_idx=0","exp.drain_tail_s=300","exp.pilot=true"])
    if int(cfg.exp["repetitions"])<1:
        raise ValueError("repetitions must be positive")
    tasks=tasks_for(cfg)
    design=review_design(cfg)
    n=cfg.scale.num_brokers*int(cfg.exp["ctx_per_broker"])
    print(f"exp030: {len(tasks)} independent serial runs x {n} CTX; pilot={cfg.exp['pilot']}",flush=True)
    if args.dry_run:
        print(json.dumps(dict(ctx_per_arm=n,methods=METHODS,tasks=tasks,design=design,
                              paper_algorithm_reproduction_complete=False),ensure_ascii=False,indent=2,default=str))
        return 0
    signature=digest(to_params_dict(cfg))
    run=args.resume.resolve() if args.resume else args.output_root.resolve()/datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if args.resume:
        manifest=json.loads((run/"manifest.json").read_text(encoding="utf-8"))
        if signature!=manifest["signature"] or fingerprints()!=manifest["source_files"]:
            raise ValueError("Configuration/source changed; start a new run")
    else:
        run.mkdir(parents=True)
        manifest=dict(experiment="exp030",pilot=bool(cfg.exp["pilot"]),signature=signature,
                      source_files=fingerprints(),tasks=tasks,method_definitions=METHODS,
                      algorithm_fidelity="adapted policies; LAI(1) continuous rule verified against ICML 2024 Algorithm 2")
    workload,total_wei=pipeline.build_workload(cfg,run)
    if len(workload)!=n or any(c["src_shard"]==c["dst_shard"] for c in workload):
        raise ValueError("Incorrect workload size or non-cross-shard transaction")
    workload_hash=digest(workload)
    if args.resume and workload_hash!=manifest["workload_sha256"]:
        raise ValueError("Workload changed")
    manifest["workload_sha256"]=workload_hash
    write_json(run/"manifest.json",manifest)
    write_json(run/"params.json",to_params_dict(cfg))
    write_json(run/"design.json",json.loads(json.dumps(design,default=str)))
    write_csv(run/"workload.csv",workload)
    print(f"Output: {run}",flush=True)
    for number,task in enumerate(manifest["tasks"],1):
        if fingerprints()!=manifest["source_files"]:
            raise ValueError("Source code changed during run; start a fresh comparison")
        if task["status"]=="complete":
            verify_task(run,task,workload,workload_hash)
            continue
        print(f"[{number}/{len(tasks)}] {task['id']} seed={task['route_seed']}",flush=True)
        effective=apply_overrides(cfg,[f"exp.route_seed={task['route_seed']}",f"exp.comparison_method={task['method']}"])
        effective=replace(effective,chain=replace(effective.chain,base_port=free_port_block(cfg.chain.base_port,cfg.chain.num_shards)))
        parent=run/"tasks"/task["id"]
        parent.mkdir(parents=True,exist_ok=True)
        watchdog=RSSWatchdog(float(cfg.exp["rss_cap_mb"]))
        task["status"]="running"
        write_json(run/"manifest.json",manifest)
        watchdog.start()
        try:
            engine="plain" if task["method"]=="brokerchain" else "topup"
            stats=pipeline.run_arm(effective,task["method"],workload,total_wei,parent,watchdog,
                                   engine=engine,fund_eth=float(cfg.exp["balances_eth"]))
            if fingerprints()!=manifest["source_files"]:
                raise ValueError("Source code changed during task; result cannot be accepted")
            write_json(parent/"execution_summary.json",stats)
            export_arm_data(effective,stats,parent/task["method"],workload)
            validate_audit(stats,parent/task["method"],workload)
            stats["rss_peak_mb"]=watchdog.peak_mb
            path=parent/"summary.json"
            write_json(path,dict(task_id=task["id"],workload_sha256=workload_hash,route_seed=task["route_seed"],
                                 params=to_params_dict(effective),stats=stats))
            task.update(status="complete",summary=str(path.relative_to(run)))
            print(f"  broker={stats['served']} relay={stats['relayed']} adjustment_ctx={stats['tdr']['transfers_done']} "
                  f"relay_plus_adjustment={stats['relayed']+stats['tdr']['transfers_done']} "
                  f"modeled_profit_wei={stats['audit']['modeled_net_profit_wei']} gates=PASS",flush=True)
        except BaseException as exc:
            task.update(status="failed",error=f"{type(exc).__name__}: {exc}")
            write_json(run/"manifest.json",manifest)
            build_report(run,manifest,cfg)
            raise
        finally:
            watchdog.stop()
        write_json(run/"manifest.json",manifest)
        build_report(run,manifest,cfg)
    print(f"COMPLETE: {run/'RESULTS_CN.md'}",flush=True)
    from experiments.exp030_five_method_rebalance.analysis.analyze import analyze
    analyze(run)
    print(f"PRIMARY COMPARISON: {run/'DETAILED_RESULTS_CN.md'}",flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())

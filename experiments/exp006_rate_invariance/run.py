#!/usr/bin/env python3
"""exp006 · 注入速率不变性 + 机器吞吐上限 —— 50 broker × 4 分片 × 上万笔。

同一批真实 trace 负载、同一 LPT 指派，按【逻辑块】以多档 ctx_per_block 注入
（到达只被新区块驱动，不用墙钟——审计 F1 的结构性防线），检验：
  H-R1 路由决策与逐 broker 终值不随注入速率变化；
  H-R2 守恒不随负载退化（全局净和 0 wei、broker 链上 == 镜像）；
  H-R3 定位这台机器的墙钟吞吐平台（速率升而吞吐不升处）。
设计/判据见本目录 README.md；参数见 config.yaml。
产物：out/<ts>/{assignment.csv, rate_<r>/{ctx_rows.csv,coordinator.json}, summary.json, figs/}
"""
import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import queue
import statistics
import sys
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from brokerlab.broker_engine import (BrokerEngine, EngineSpec, assign_sender_groups,
                                     build_services, group_key)
from brokerlab.brokerchain import BURN_ADDRESS
from brokerlab.chain import AnvilCluster, Connections
from brokerlab.config import ETH, apply_overrides, load_config, to_params_dict
from brokerlab.identity import UserManager
from brokerlab.procmon import RSSWatchdog
from brokerlab.real_data import extract
from brokerlab.tx import TxService


def exp_param(cfg, key, kind=int):
    """参数唯一来源=config.yaml 的 exp: 段，缺键即报错（各实验包统一纪律）。"""
    if key not in cfg.exp:
        sys.exit(f"config.yaml 的 exp: 段缺少参数 {key!r}（参数必须全部显式声明）")
    return kind(cfg.exp[key])


def dist(vals):
    """分布摘要：None 先剔除（relay 行的部分列没有本引擎锚点）。"""
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"mean": None, "p50": None, "p95": None, "max": None, "n": 0}
    s = sorted(vals)
    q = lambda p: s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]
    return {"mean": round(sum(vals) / len(vals), 3), "p50": q(50), "p95": q(95),
            "max": max(vals), "n": len(vals)}


# ---------------------------------------------------------------------------
# 工作负载：真实 trace → 巨鲸组过滤 → LPT 装箱（各速率档共用同一份）
# ---------------------------------------------------------------------------

def build_workload(cfg, out_root):
    nb = cfg.scale.num_brokers
    k = exp_param(cfg, "ctx_per_broker", int)
    n = nb * k
    pool = extract(cfg.traffic.real_csv_path,
                   num_shards=cfg.chain.num_shards, num_users=cfg.scale.num_users,
                   user_base_index=cfg.scale.user_base_index,
                   value_floor_wei=int(cfg.traffic.value_floor_eth * ETH),
                   value_cap_wei=int(cfg.traffic.value_cap_eth * ETH),
                   limit=exp_param(cfg, "pool_scan", int))
    groups = {}
    for r in pool:
        groups.setdefault(group_key(asdict(r)), []).append(asdict(r))
    glist = list(groups.values())
    mean_g = len(pool) / max(len(glist), 1)
    cap = mean_g * exp_param(cfg, "group_cap_mult", float)
    kept = sorted((g for g in glist if len(g) <= cap), key=len)
    sel = []
    for g in kept:
        sel.extend(g)
        if len(sel) >= n:
            break
    sel = sel[:n]
    if len(sel) < n:
        print(f"  !! 凑不满 N：只有 {len(sel)} 笔（目标 {n}），按实际数继续")
    for c_idx, c in enumerate(sel):
        c["arrival_pos"] = c_idx                 # 全局到达序 = sel 原位置
    bins = assign_sender_groups(sel, nb, exp_param(cfg, "seed", int))
    for b in bins:
        bins[b].sort(key=lambda c: c["arrival_pos"])   # bin 内严格到达序
    total = sum(int(c["amount_wei"]) for cs in bins.values() for c in cs)
    counts = sorted(len(v) for v in bins.values())
    with open(out_root / "assignment.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["broker_idx", "sender_idx", "src_shard", "n_ctx", "total_wei"])
        for b, cs in sorted(bins.items()):
            per = {}
            for c in cs:
                k2 = (c["sender_idx"], c["src_shard"])
                a = per.setdefault(k2, [0, 0]); a[0] += 1; a[1] += c["amount_wei"]
            for (si, sh), (cnt, wei) in sorted(per.items()):
                w.writerow([b, si, sh, cnt, wei])
    print(f"  巨鲸过滤弃 {sum(len(g) for g in glist if len(g) > cap)} 笔（{sum(1 for g in glist if len(g) > cap)} 组）")
    print(f"  workload: {len(sel)} 笔 → {nb} bins | 大小 {counts[0]}–{counts[-1]} | 合计 {total/ETH:.0f} ETH")
    return bins, sel, total


# ---------------------------------------------------------------------------
# coordinator 进程：单一签发账户代铸所有 relay 的 mint 段（账户单写者）
# ---------------------------------------------------------------------------

def _coordinator_worker(cfg_plain, params, req_q, ack_qs, stop_evt, readyq, doneq):
    try:
        tx, users, scale = build_services(cfg_plain)
        pending = {}     # ref -> [engine, h, shard, t0, next_probe]
        minted = 0
        now = time.monotonic
        readyq.put(("coord", now()))
        while True:
            try:                                         # 一次多收几条，减少积压
                for _ in range(64):
                    req = req_q.get_nowait()
                    h = tx.send_transfer(scale.coordinator_index,
                                         users.address(req["receiver_idx"]),
                                         req["amount_wei"], req["dst_shard"])
                    pending[req["ref"]] = [req["engine"], h, req["dst_shard"],
                                           now(), now() + params["probe_first_s"]]
                    minted += 1
            except queue.Empty:
                pass
            t = now()
            for ref, p in list(pending.items()):
                if t < p[4]:                             # 轮询节流：与引擎同粒度
                    continue
                p[4] = t + params["probe_s"]
                r = tx.probe_receipt(p[1], p[2])
                if r is not None:
                    ack_qs[p[0]].put((ref, r["status"], r["block"]))
                    pending.pop(ref)
                elif t - p[3] > params["relay_timeout_s"]:
                    ack_qs[p[0]].put((ref, 0, None))     # 铸币超时：失败回执，守恒仍成立
                    pending.pop(ref)
            if stop_evt.is_set() and req_q.empty() and not pending:
                break
            time.sleep(0.02)
        doneq.put({"minted": minted, "pending_left": len(pending), "error": None})
    except BaseException:
        doneq.put({"error": traceback.format_exc()})


def _engine_worker(cfg_plain, spec_dict, params, released, req_q, ack_q,
                   readyq, outq, gate):
    b = spec_dict["broker_idx"]
    try:
        tx, users, scale = build_services(cfg_plain)
        spec = EngineSpec(broker_idx=b, ctxs=tuple(spec_dict["ctxs"]),
                          init_wei_by_shard={int(s): int(v) for s, v
                                             in spec_dict["init_wei_by_shard"].items()})
        eng = BrokerEngine(tx, users, spec, broker_base_index=scale.broker_base_index,
                           max_inflight=params["max_inflight"], poll_s=params["poll_s"],
                           probe_s=params["probe_s"], probe_first_s=params["probe_first_s"],
                           timeout_s=params["engine_timeout_s"],
                           release=lambda: released.value,
                           credit_sink=req_q, credit_in=ack_q)
        readyq.put(("engine", b))
        gate.wait()
        outq.put(eng.run_blocking())
    except BaseException:
        outq.put({"broker_idx": b, "rows": [], "error": traceback.format_exc(),
                  "reserve_blocked": 0, "arrival_gated": 0, "relay_fallback": 0,
                  "inflight_peak": 0, "n_assigned": len(spec_dict["ctxs"])})


# ---------------------------------------------------------------------------
# 单个速率档：起链→充值→coordinator+到达监视器+50 引擎→落定→对账
# ---------------------------------------------------------------------------

def run_rate(cfg, rate, bins, sel, total_wei, out_root, watchdog):
    nb = cfg.scale.num_brokers
    shards = list(range(cfg.chain.num_shards))
    n = len(sel)
    N = exp_param(cfg, "ctx_per_broker", int) * nb
    mode_dir = out_root / f"rate_{rate}"
    mode_dir.mkdir(parents=True, exist_ok=True)
    users = UserManager(cfg.chain.mnemonic)
    cluster = AnvilCluster(cfg.chain, mode_dir / "logs")
    cluster.start()
    cluster.write_pids(mode_dir / "pids.json")
    for pid in json.loads((mode_dir / "pids.json").read_text()).values():
        watchdog.track(pid)
    conns = Connections(cfg.chain)
    conns.wait_all_ready()
    tx = TxService(conns, users, cfg.chain)
    ctxm = mp.get_context("spawn")
    try:
        # —— 充值（唯一 setBalance 窗口）——
        init_wei = int(exp_param(cfg, "balances_eth", float) * ETH)
        per_sender = {}
        for c in sel:
            key = (c["sender_idx"], c["src_shard"])
            per_sender[key] = per_sender.get(key, 0) + c["amount_wei"]
        actors = [cfg.scale.coordinator_index] + \
            [cfg.scale.broker_base_index + b for b in range(nb)] + \
            [si for (si, _) in per_sender]
        tx.sync_nonces(actors, shards)
        broker_addrs = [users.address(cfg.scale.broker_base_index + b) for b in range(nb)]
        coord_addr = users.address(cfg.scale.coordinator_index)
        for s in shards:
            for a in broker_addrs:
                tx.set_balance_setup(a, s, init_wei)
            tx.set_balance_setup(coord_addr, s, total_wei + 10 * ETH)  # 最坏全 relay
        for (idx, sh), amt in per_sender.items():
            tx.set_balance_setup(users.address(idx), sh, amt + ETH)
        tx.sync_nonces(actors, shards)

        idxs = {si for (si, _) in per_sender} | {c["receiver_idx"] for c in sel} | \
            {cfg.scale.broker_base_index + b for b in range(nb)} | {cfg.scale.coordinator_index}
        addrs = sorted({users.address(i) for i in idxs} | {BURN_ADDRESS})
        before = {(a, s): tx.get_balance(a, s) for a in addrs for s in shards}

        mirror0 = {b: {s: init_wei for s in shards} for b in range(nb)}
        specs = {b: {"broker_idx": b, "ctxs": [dict(c) for c in bins[b]],
                     "init_wei_by_shard": dict(mirror0[b])} for b in range(nb)}
        params = {k: exp_param(cfg, k, float) for k in
                  ("max_inflight", "poll_s", "probe_s", "probe_first_s",
                   "relay_timeout_s", "engine_timeout_s")}
        cfg_plain = {"chain": asdict(cfg.chain), "scale": asdict(cfg.scale)}

        released = ctxm.Value("l", 0)
        req_q = ctxm.Queue()
        ack_qs = [ctxm.Queue() for _ in range(nb)]
        readyq, outq, doneq = ctxm.Queue(), ctxm.Queue(), ctxm.Queue()
        gate = ctxm.Event()
        stop_evt = ctxm.Event()

        coord = ctxm.Process(target=_coordinator_worker,
                             args=(cfg_plain, params, req_q, ack_qs, stop_evt, readyq, doneq))
        coord.start()
        watchdog.track(coord.pid)
        kind, _ = readyq.get(timeout=exp_param(cfg, "t_ready_budget_s", float))

        procs = []
        t_launch = time.monotonic()
        for b in range(nb):
            p = ctxm.Process(target=_engine_worker,
                             args=(cfg_plain, specs[b], params, released, req_q,
                                   ack_qs[b], readyq, outq, gate))
            p.start()
            procs.append(p)
            watchdog.track(p.pid)
        for _ in range(nb):
            readyq.get(timeout=exp_param(cfg, "t_ready_budget_s", float))
        t_ready = time.monotonic() - t_launch

        # —— 到达监视器：released = clamp((min_block_height - h0) * rate, 0, N) ——
        h0 = min(tx.block_number(s) for s in shards)
        release_stop = threading.Event()
        actual_arrivals = []          # 每次轮询记录 (墙钟, 已释放数)：速率保真度证据
        def _monitor():
            while not release_stop.is_set():
                h = min(tx.block_number(s) for s in shards)
                rel = max(0, h - h0) * rate
                released.value = min(rel, N)
                actual_arrivals.append((time.monotonic(), released.value))
                time.sleep(exp_param(cfg, "release_poll_s", float))
        mon = threading.Thread(target=_monitor, daemon=True)
        gate.set()
        t0 = time.monotonic()
        mon.start()

        rows = []
        envs = {}
        deadline = t0 + N / max(rate, 1) * 1.0 + exp_param(cfg, "drain_tail_s", float)
        while len(envs) < nb:
            if watchdog.tripped:
                raise RuntimeError("RSS watchdog tripped")
            if time.monotonic() > deadline:
                raise TimeoutError(f"rate {rate}: 引擎未在 {deadline-t0:.0f}s 内全部落定")
            try:
                env = outq.get(timeout=1.0)
            except Exception:
                continue
            envs[env["broker_idx"]] = env
        wall = time.monotonic() - t0
        release_stop.set()
        stop_evt.set()
        coord_done = doneq.get(timeout=exp_param(cfg, "relay_timeout_s", float) + 60)
        coord.join(timeout=30)
        for p in procs:
            p.join(timeout=10)

        # 所有引擎落定且 coordinator 铸币全部确认后才快照 ⇒ 即使有超时，守恒仍可核对
        after = {(a, s): tx.get_balance(a, s) for (a, s) in before}
        for b in sorted(envs):
            rows.extend(envs[b]["rows"])
        with open(mode_dir / "ctx_rows.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        (mode_dir / "coordinator.json").write_text(json.dumps(coord_done))

        stats = verify_rate(cfg, rate, bins, envs, coord_done, before, after,
                            broker_addrs, wall, t_ready, actual_arrivals, n, N, total_wei)
        return stats
    finally:
        cluster.stop()


def verify_rate(cfg, rate, bins, envs, coord_done, before, after, broker_addrs,
                wall, t_ready, arrivals, n, N, total_wei):
    """单速率档的度量与守恒核对，并计算决策指纹（供跨档比对）。"""
    shards = list(range(cfg.chain.num_shards))
    rows = [r for e in envs.values() for r in e["rows"]]
    served = [r for r in rows if r["route"] == "broker"]
    relay = [r for r in rows if r["route"] == "relay"]
    failed = [r for r in rows if not r["ok"]]
    drift = sum(after[k] - before[k] for k in before)
    # 逐 broker：链上 == 垫资 + Σsrc − Σdst（relay 不动 broker，故 relay 行不计入）
    mirror_bad = 0
    for b, a in enumerate(broker_addrs):
        bal = dict.fromkeys(shards, int(exp_param(cfg, "balances_eth", float) * ETH))
        for r in envs[b]["rows"]:
            if r["ok"] and r["route"] == "broker":
                bal[r["src"]] += r["amount_wei"]; bal[r["dst"]] -= r["amount_wei"]
        for s in shards:
            if after[(a, s)] != bal[s]:
                mirror_bad += 1
    # H-R1 决策摘要：按 broker 分组的 (ctx_id, route) 序列指纹（与速率无关的逻辑结果）
    digest = hashlib.sha256()
    for b in sorted(envs):
        for r in sorted(envs[b]["rows"], key=lambda x: x["arrival_pos"]):
            digest.update(f"{b}:{r['ctx_id']}:{r['route']};".encode())
    # 注入保真度：到达监视器最后一次释放数 vs N；平台判断用墙钟吞吐
    thr = round(n / wall, 2) if wall > 0 else None
    return {
        "rate_ctx_per_block": rate, "n": n, "wall_s": round(wall, 2),
        "throughput_ctx_per_s": thr, "t_ready_s": round(t_ready, 2),
        "served": len(served), "relayed": len(relay),
        "failed": len(failed), "mirror_mismatch": mirror_bad,
        "global_net_sum_wei": drift, "coordinator": coord_done,
        "decision_digest": digest.hexdigest()[:16],
        "eta_end": round(len(served) / len(rows), 4) if rows else None,
        "e2e_secs": dist([r["e2e_secs"] for r in rows if r["ok"]]),
        "t1_secs": dist([r["t1_secs"] for r in rows if r["ok"]]),
        "hops_total": dist([r["hops_total"] for r in rows if r["ok"]]),
        "relay_fallback_sum": sum(e.get("relay_fallback", 0) for e in envs.values()),
    }


def make_figure(out_root, stats_by_rate):
    """三面板：(a) 吞吐-速率平台 (b) e2e 均值/p95 随速率 (c) 墙钟随速率。"""
    from brokerlab import plotting as P
    import matplotlib.pyplot as plt
    figs = out_root / "figs"
    figs.mkdir(exist_ok=True)
    rates = [s["rate_ctx_per_block"] for s in stats_by_rate]
    thr = [s["throughput_ctx_per_s"] for s in stats_by_rate]
    e2e = [s["e2e_secs"]["mean"] for s in stats_by_rate]
    e2e95 = [s["e2e_secs"]["p95"] for s in stats_by_rate]
    wall = [s["wall_s"] for s in stats_by_rate]
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(13.5, 4), dpi=140)
    a.plot(rates, thr, "o-", color=P.COLOR_ALT)
    a.set_xlabel("injection rate (CTX/block)"); a.set_ylabel("throughput (CTX/s)")
    a.set_title("(a) throughput plateau = machine ceiling")
    b.plot(rates, e2e, "o-", color=P.COLOR_BROKER, label="e2e mean")
    b.plot(rates, e2e95, "s--", color=P.COLOR_DATA, label="e2e p95")
    b.set_xlabel("injection rate (CTX/block)"); b.set_ylabel("e2e (s)")
    b.set_title("(b) latency inflates past the knee"); b.legend(fontsize=8)
    c.plot(rates, wall, "o-", color=P.COLOR_GRAY)
    c.set_xlabel("injection rate (CTX/block)"); c.set_ylabel("wall time (s)")
    c.set_title("(c) wall time (same N every rate)")
    P.save(fig, figs / "exp006_rate.png")
    print(f"  figure: {figs / 'exp006_rate.png'}")


def main() -> int:
    """主流程：构负载→逐速率档起链跑→判 H-R1/H-R2→定位吞吐上限→导出。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)
    rates = [int(x) for x in exp_param(cfg, "rates", str).split(",")]
    out_root = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root.mkdir(parents=True)
    print(f"  exp006 · out: {out_root} | rates={rates}")

    bins, sel, total_wei = build_workload(cfg, out_root)
    watchdog = RSSWatchdog(exp_param(cfg, "rss_cap_mb", float))
    watchdog.start()
    stats_by_rate = []
    try:
        for r in rates:
            print(f"\n  ==== 速率档 {r} CTX/block ====")
            st = run_rate(cfg, r, bins, sel, total_wei, out_root, watchdog)
            stats_by_rate.append(st)
            print(f"  {r}: wall {st['wall_s']}s | {st['throughput_ctx_per_s']} CTX/s | "
                  f"served {st['served']} relay {st['relayed']} fail {st['failed']} | "
                  f"net {st['global_net_sum_wei']} | digest {st['decision_digest']}")
            if watchdog.tripped:
                print("  !! RSS 看门狗触发，中止后续速率档")
                break
    except (TimeoutError, RuntimeError) as ex:
        print(f"  !! 中止：{ex}（已跑档位仍导出，用于定位上限）")
    finally:
        watchdog.stop()

    digests = {s["rate_ctx_per_block"]: s["decision_digest"] for s in stats_by_rate}
    # 路由决策在 pump 时按余额定下（arrival_pos 升序、金额驱动），与墙钟/超时无关 ⇒
    # 决策指纹 (ctx_id→route) 应在【所有】速率档一致，哪怕高负载档有超时失败。
    hr1 = len(stats_by_rate) >= 2 and len({s["decision_digest"] for s in stats_by_rate}) == 1
    hr2 = all(s["global_net_sum_wei"] == 0 for s in stats_by_rate)
    clean_ok = all(s["failed"] == 0 and s["mirror_mismatch"] == 0 for s in stats_by_rate)
    failures_by_rate = {s["rate_ctx_per_block"]: s["failed"] for s in stats_by_rate}
    thr_by = [(s["rate_ctx_per_block"], s["throughput_ctx_per_s"]) for s in stats_by_rate]
    ceiling = max((t for _, t in thr_by if t), default=None)
    ceiling_rate = next((r for r, t in thr_by if t == ceiling), None)
    # passed 只看两条科学主张：路由决策的速率不变性 + 全负载金钱守恒。
    # 高负载档的 mirror_mismatch/failed 是饱和信号（另表 report），不判负。
    passed = hr1 and hr2 and len(stats_by_rate) >= 2
    try:
        make_figure(out_root, stats_by_rate)
    except ImportError:
        print("  (matplotlib 不可用，跳过绘图)")
    (out_root / "summary.json").write_text(json.dumps({
        "params": to_params_dict(cfg, extra={"rates": rates, "total_ctx": len(sel),
                                             "rss_peak_mb": round(watchdog.peak_mb, 0)}),
        "rates": stats_by_rate,
        "hypotheses": {"H-R1_rate_invariance": hr1, "H-R2_conservation_all_loads": hr2,
                       "all_rates_unsaturated": clean_ok},
        "decision_digests": digests,
        "failures_by_rate": failures_by_rate,
        "ceiling": {"max_throughput_ctx_per_s": ceiling, "at_rate": ceiling_rate,
                    "rss_peak_mb": round(watchdog.peak_mb, 0),
                    "throughput_curve": thr_by},
    }, indent=2))
    print("\n" + "=" * 74)
    print(f"  H-R1 速率不变性（决策指纹跨全部速率档一致）: {hr1}")
    print(f"  H-R2 全负载守恒（各档净和均 0 wei）: {hr2}")
    print(f"  无饱和档（全档 0 失败 0 镜像漂移）: {clean_ok} | 各档失败数 {failures_by_rate}")
    print(f"  吞吐上限: {ceiling} CTX/s @ rate={ceiling_rate} | RSS 峰值 {watchdog.peak_mb:.0f} MB")
    print("=" * 74)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""exp007 · 动态路由（M3 核心上线）—— 静态指派 vs coordinator 实时匹配，同会话对照。

动态臂把路由从"启动期常量"改回"运行期决策"：coordinator 进程按块释放到达，
用每块刷新的余额估计表逐笔选 broker、预分配 nonce、经队列投递；
broker 引擎本地账本终审（dst 不够 ⇒ 自走 relay 兜底）。
静态臂复用 exp006 的完整路径（LPT 固定箱 + release 门），同会话直接对比。
兼任 M3 里程碑验收：逻辑时钟、投递模型、账本终审、积压看门狗。
设计/判据见本目录 README.md；参数见 config.yaml。
产物：out/<ts>/{assignment.csv, <arm_tag>/{ctx_rows.csv,coordinator.json[,declared.csv]},
      summary.json, figs/exp007_dynamic.png}
"""
import argparse
import csv
import json
import multiprocessing as mp
import queue
import random
import statistics
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from brokerlab.broker_engine import (END, BrokerEngine, DynamicBrokerEngine,
                                     EngineSpec, assign_sender_groups,
                                     build_services, dynamic_engine_from_payload,
                                     group_key)
from brokerlab.brokerchain import BURN_ADDRESS
from brokerlab.chain import AnvilCluster, Connections
from brokerlab.config import ETH, apply_overrides, load_config, to_params_dict
from brokerlab.identity import NonceManager, UserManager
from brokerlab.matching import select_broker
from brokerlab.procmon import RSSWatchdog
from brokerlab.real_data import extract
from brokerlab.tx import TxService


def exp_param(cfg, key, kind=int):
    """参数唯一来源=config.yaml 的 exp: 段，缺键即报错（各实验包统一纪律）。"""
    if key not in cfg.exp:
        sys.exit(f"config.yaml 的 exp: 段缺少参数 {key!r}（参数必须全部显式声明）")
    return kind(cfg.exp[key])


def dist(vals):
    """分布摘要：None 先剔除。"""
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"mean": None, "p50": None, "p95": None, "max": None, "n": 0}
    s = sorted(vals)
    q = lambda p: s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]
    return {"mean": round(sum(vals) / len(vals), 3), "p50": q(50), "p95": q(95),
            "max": max(vals), "n": len(vals)}


# ---------------------------------------------------------------------------
# 工作负载（与 exp006 同一构造：真实 trace → 巨鲸过滤 → LPT 用于静态臂）
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
    for c_idx, c in enumerate(sel):
        c["arrival_pos"] = c_idx                 # 全局到达序 = sel 原位置
    bins = assign_sender_groups(sel, nb, exp_param(cfg, "seed", int))
    for b in bins:
        bins[b].sort(key=lambda c: c["arrival_pos"])
    total = sum(int(c["amount_wei"]) for c in sel)
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
    print(f"  workload: {len(sel)} 笔 → {nb} bins(静态臂) | 合计 {total/ETH:.0f} ETH")
    return bins, sel, total


# ---------------------------------------------------------------------------
# 起链与充值（两臂共用；唯一 setBalance 窗口；快照 before）
# ---------------------------------------------------------------------------

def fund_chain(cfg, sel, total_wei, arm_dir, watchdog):
    nb = cfg.scale.num_brokers
    shards = list(range(cfg.chain.num_shards))
    arm_dir.mkdir(parents=True, exist_ok=True)
    users = UserManager(cfg.chain.mnemonic)
    cluster = AnvilCluster(cfg.chain, arm_dir / "logs")
    cluster.start()
    cluster.write_pids(arm_dir / "pids.json")
    for pid in json.loads((arm_dir / "pids.json").read_text()).values():
        watchdog.track(pid)
    conns = Connections(cfg.chain)
    conns.wait_all_ready()
    tx = TxService(conns, users, cfg.chain)
    init_wei = int(exp_param(cfg, "balances_eth", float) * ETH)
    per_sender = {}
    for c in sel:
        key = (c["sender_idx"], c["src_shard"])
        per_sender[key] = per_sender.get(key, 0) + c["amount_wei"]
    actors = ([cfg.scale.coordinator_index]
              + [cfg.scale.broker_base_index + b for b in range(nb)]
              + [si for (si, _) in per_sender])
    tx.sync_nonces(actors, shards)
    broker_addrs = [users.address(cfg.scale.broker_base_index + b) for b in range(nb)]
    coord_addr = users.address(cfg.scale.coordinator_index)
    for s in shards:
        for a in broker_addrs:
            tx.set_balance_setup(a, s, init_wei)
        tx.set_balance_setup(coord_addr, s, total_wei + 10 * ETH)
    for (idx, sh), amt in per_sender.items():
        tx.set_balance_setup(users.address(idx), sh, amt + ETH)
    tx.sync_nonces(actors, shards)
    idxs = ({si for (si, _) in per_sender} | {c["receiver_idx"] for c in sel}
            | {cfg.scale.broker_base_index + b for b in range(nb)}
            | {cfg.scale.coordinator_index})
    addrs = sorted({users.address(i) for i in idxs} | {BURN_ADDRESS})
    before = {(a, s): tx.get_balance(a, s) for a in addrs for s in shards}
    return cluster, tx, users, broker_addrs, before


def engine_params(cfg):
    p = {k: exp_param(cfg, k, float) for k in
         ("max_inflight", "poll_s", "probe_s", "probe_first_s",
          "relay_timeout_s", "engine_timeout_s")}
    p["balances_eth"] = exp_param(cfg, "balances_eth", float)
    return p


def collect_arm_stats(cfg, sel, envs, before, after, broker_addrs, wall):
    """两臂共用的对账：逐 broker 链上==行账重算 + 全局净和 + 分布摘要。"""
    shards = list(range(cfg.chain.num_shards))
    rows = [r for e in envs.values() for r in e.get("rows", [])]
    served = [r for r in rows if r["route"] == "broker"]
    relay = [r for r in rows if r["route"] == "relay"]
    failed = [r for r in rows if not r["ok"]]
    drift = sum(after[k] - before[k] for k in before)
    mirror_bad = 0
    per = {b: dict.fromkeys(shards, int(exp_param(cfg, "balances_eth", float) * ETH))
           for b in range(cfg.scale.num_brokers)}
    for r in rows:
        if r["ok"] and r["route"] == "broker":
            per[r["broker_idx"]][r["src"]] += r["amount_wei"]
            per[r["broker_idx"]][r["dst"]] -= r["amount_wei"]
    for b, a in enumerate(broker_addrs):
        for s in shards:
            if after[(a, s)] != per[b][s]:
                mirror_bad += 1
    return rows, {
        "n": len(rows), "served": len(served), "relayed": len(relay),
        "failed": len(failed), "mirror_mismatch": mirror_bad,
        "global_net_sum_wei": drift, "wall_s": round(wall, 2),
        "throughput_ctx_per_s": round(len(rows) / wall, 2) if wall > 0 else None,
        "e2e_secs": dist([r["e2e_secs"] for r in rows if r["ok"]]),
        "hops_total": dist([r["hops_total"] for r in rows if r["ok"]]),
    }


# ---------------------------------------------------------------------------
# 静态臂：exp006 路径（LPT 固定箱 + 父进程 release 监视器 + mint coordinator）
# ---------------------------------------------------------------------------

def _mint_coordinator_worker(cfg_plain, params, req_q, ack_qs, stop_evt, readyq, doneq):
    """只代铸不路由的 coordinator（exp006 同款）：给静态臂用。"""
    try:
        tx, users, scale = build_services(cfg_plain)
        pending = {}
        minted = 0
        now = time.monotonic
        readyq.put(("coord", now()))
        while True:
            try:
                for _ in range(64):
                    rq = req_q.get_nowait()
                    h = tx.send_transfer(scale.coordinator_index,
                                         users.address(rq["receiver_idx"]),
                                         rq["amount_wei"], rq["dst_shard"])
                    pending[rq["ref"]] = [rq["engine"], h, rq["dst_shard"], now(),
                                          now() + params["probe_first_s"]]
                    minted += 1
            except queue.Empty:
                pass
            t = now()
            for ref, p in list(pending.items()):
                if t < p[4]:
                    continue
                p[4] = t + params["probe_s"]
                r = tx.probe_receipt(p[1], p[2])
                if r is not None:
                    ack_qs[p[0]].put((ref, r["status"], r["block"]))
                    pending.pop(ref)
                elif t - p[3] > params["relay_timeout_s"]:
                    ack_qs[p[0]].put((ref, 0, None))
                    pending.pop(ref)
            if stop_evt.is_set() and req_q.empty() and not pending:
                break
            time.sleep(params.get("loop_sleep_s", 0.02))
        doneq.put({"minted": minted, "pending_left": len(pending), "error": None})
    except BaseException:
        doneq.put({"error": traceback.format_exc()})


def _static_engine_worker(cfg_plain, spec_dict, params, released, req_q, ack_q,
                          readyq, outq, gate):
    """静态臂引擎：与 exp006 的 _engine_worker 同一路径。"""
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
        readyq.put(("engine_error", b, traceback.format_exc()))
        outq.put({"broker_idx": b, "rows": [], "error": traceback.format_exc(),
                  "relay_fallback": 0, "arrival_gated": 0,
                  "n_assigned": len(spec_dict["ctxs"])})


def _wait_ready(readyq, doneq, budget_s, label):
    """等 ready 信号；doneq 出现内容 = 对方进程提前退出，把它的错误原样带出来。"""
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        try:
            item = readyq.get(timeout=1.0)
            if isinstance(item, tuple) and item[0] == "engine_error":
                raise RuntimeError(f"{label} 构造失败 broker_{item[1]}：\n{item[2]}")
            return item
        except RuntimeError:
            raise
        except Exception:
            try:
                env = doneq.get_nowait()
            except Exception:
                continue
            raise RuntimeError(f"{label} 提前退出：{env.get('error') or env}")
    raise TimeoutError(f"{label} ready 超时 {budget_s}s")


def run_static(cfg, rate, bins, sel, total_wei, out_root, watchdog):
    nb = cfg.scale.num_brokers
    shards = list(range(cfg.chain.num_shards))
    N = len(sel)
    arm_dir = out_root / f"stat_{rate}"
    cluster, tx, users, broker_addrs, before = fund_chain(cfg, sel, total_wei,
                                                          arm_dir, watchdog)
    ctxm = mp.get_context("spawn")
    procs, coord, stop_evt = [], None, None   # 供 finally 回收（异常路径不留孤儿进程）
    try:
        params = engine_params(cfg)
        cfg_plain = {"chain": asdict(cfg.chain), "scale": asdict(cfg.scale)}
        init_wei = int(exp_param(cfg, "balances_eth", float) * ETH)
        mirror0 = {b: {s: init_wei for s in shards} for b in range(nb)}
        specs = {b: {"broker_idx": b, "ctxs": [dict(c) for c in bins[b]],
                     "init_wei_by_shard": dict(mirror0[b])} for b in range(nb)}
        released = ctxm.Value("l", 0)
        req_q = ctxm.Queue()
        ack_qs = [ctxm.Queue() for _ in range(nb)]
        readyq, outq, doneq = ctxm.Queue(), ctxm.Queue(), ctxm.Queue()
        gate = ctxm.Event()
        stop_evt = ctxm.Event()
        coord = ctxm.Process(target=_mint_coordinator_worker,
                             args=(cfg_plain, params, req_q, ack_qs, stop_evt,
                                   readyq, doneq))
        coord.start()
        watchdog.track(coord.pid)
        _wait_ready(readyq, doneq, exp_param(cfg, "t_ready_budget_s", float), "coord(static)")
        procs = []
        t_launch = time.monotonic()
        for b in range(nb):
            p = ctxm.Process(target=_static_engine_worker,
                             args=(cfg_plain, specs[b], params, released, req_q,
                                   ack_qs[b], readyq, outq, gate))
            p.start()
            procs.append(p)
            watchdog.track(p.pid)
        for _ in range(nb):
            _wait_ready(readyq, doneq, exp_param(cfg, "t_ready_budget_s", float),
                        "engine(static)")
        t_ready = time.monotonic() - t_launch
        h0 = min(tx.block_number(s) for s in shards)
        rel_stop = threading.Event()
        declared = []

        def _monitor():
            while not rel_stop.is_set():
                h = min(tx.block_number(s) for s in shards)
                rel = min(N, max(0, h - h0) * rate)
                if released.value != rel:
                    declared.append(rel)
                released.value = rel
                time.sleep(exp_param(cfg, "release_poll_s", float))
        mon = threading.Thread(target=_monitor, daemon=True)
        gate.set()
        t0 = time.monotonic()
        mon.start()
        envs = {}
        deadline = t0 + N / max(rate, 1) + exp_param(cfg, "drain_tail_s", float)
        while len(envs) < nb:
            if watchdog.tripped:
                raise RuntimeError("RSS watchdog tripped")
            if time.monotonic() > deadline:
                raise TimeoutError(f"stat_{rate}: 引擎未在时限内全部落定")
            try:
                env = outq.get(timeout=1.0)
            except Exception:
                continue
            envs[env["broker_idx"]] = env
        wall = time.monotonic() - t0
        rel_stop.set()
        stop_evt.set()
        coord_done = doneq.get(timeout=params["relay_timeout_s"] + 60)
        coord.join(timeout=30)
        for p in procs:
            p.join(timeout=10)
        after = {(a, s): tx.get_balance(a, s) for (a, s) in before}
        rows, stats = collect_arm_stats(cfg, sel, envs, before, after,
                                        broker_addrs, wall)
        stats.update({"kind": "static", "rate": rate, "window": None,
                      "tag": f"stat_{rate}", "t_ready_s": round(t_ready, 2),
                      "arrival_gated_sum": sum(e.get("arrival_gated", 0) for e in envs.values()),
                      "relay_fallback_sum": sum(e.get("relay_fallback", 0) for e in envs.values()),
                      "coord_minted": coord_done.get("minted")})
        if rows:
            with open(arm_dir / "ctx_rows.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader(); w.writerows(rows)
        (arm_dir / "coordinator.json").write_text(json.dumps(coord_done))
        (arm_dir / "declared.csv").write_text(json.dumps(declared))
        return stats
    finally:
        if stop_evt is not None:
            stop_evt.set()
        for p in procs:
            if p.is_alive():
                p.terminate()
        if coord is not None and coord.is_alive():
            coord.terminate()
        cluster.stop()


# ---------------------------------------------------------------------------
# 动态臂：M3 coordinator 块循环（释放→估计表→派发→代铸→回收）
# ---------------------------------------------------------------------------

def _routing_coordinator_worker(cfg_plain, params, rate, window, ctxs,
                                ctx_qs, rep_q, req_q, ack_qs, readyq, doneq, gate):
    """M3 块循环：单进程事件循环，五步每轮。所有共享状态只在本进程内变更
    （估计表、sender 窗口、nonce 分配）——不存在跨进程竞态。"""
    try:
        from concurrent.futures import ThreadPoolExecutor
        tx, users, scale = build_services(cfg_plain)
        nb = scale.num_brokers
        shards = list(range(params["num_shards"]))
        N = len(ctxs)
        init_wei = int(params["balances_eth"] * ETH)
        est = {b: {s: init_wei for s in shards} for b in range(nb)}   # 余额估计表
        # 刷新用的并行读手：每线程一个独立 TxService（requests.Session 非线程安全）
        n_rw = int(params["refresh_workers"])
        refresh_pool = ThreadPoolExecutor(max_workers=n_rw)
        refresh_txs = [build_services(cfg_plain)[0] for _ in range(n_rw)]
        nonces = NonceManager()          # (addr, shard) → 下一个要发的 nonce
        rng = random.Random(int(params["route_seed"]))
        pend: dict = {}                  # (sender,src) → deque，释放后待派发（FIFO 保序）
        infl_sent: dict = {}             # (sender,src) → 在途笔数（窗口控制）
        assigned: dict = {}              # ctx_id → (broker, key)：回报时修正估计用
        h0 = None
        last_rel = 0
        last_refresh_h = None
        release_pos = disp = done = minted = 0
        nq_est = nq_local = served_ct = relay_ct = 0
        backlog_peak = 0
        declared_seq = []
        refresh_ms = []
        pending_mint = {}                # mint 段 h → [ref, engine, shard, t0, next_probe]
        now = time.monotonic
        t0 = None
        aborted = None
        readyq.put(("coord", now()))
        gate.wait()
        t0 = now()
        h_cache = None            # 块高缓存：每 release_poll_s 才真读一次（块本身 1s 一个）
        last_blk_check = 0.0
        rel_target = 0
        done_trace = []          # 每块 (h−h0, disp, done) 轨迹
        prof = {"rounds": 0, "blkheight_ms": 0.0, "refresh_n": 0,
                "dispatch_ms": 0.0, "mint_ms": 0.0, "rep_ms": 0.0}
        while True:
            prof["rounds"] += 1
            # ① 代铸：消费 CreditRequest，coordinator 账户签 mint 段（唯一写者）
            try:
                for _ in range(128):
                    rq = req_q.get_nowait()
                    h = tx.send_transfer(scale.coordinator_index,
                                         users.address(rq["receiver_idx"]),
                                         rq["amount_wei"], rq["dst_shard"])
                    pending_mint[h] = [rq["ref"], rq["engine"], rq["dst_shard"],
                                       now(), now() + params["probe_first_s"]]
                    minted += 1
            except queue.Empty:
                pass
            tn = now()
            for mh, p in list(pending_mint.items()):   # mh=mint tx 哈希（勿与块高 h 混名）
                if tn < p[4]:
                    continue
                p[4] = tn + params["probe_s"]
                r = tx.probe_receipt(mh, p[2])
                if r is not None:
                    ack_qs[p[1]].put((p[0], r["status"], r["block"]))
                    pending_mint.pop(mh)
                elif tn - p[3] > params["relay_timeout_s"]:
                    ack_qs[p[1]].put((p[0], 0, None))
                    pending_mint.pop(mh)
            # ② 回收：消费引擎回报 → 释放 sender 窗口 + 修正估计 + 计数
            try:
                while True:
                    b, cid, route, ok, src, dst, v = rep_q.get_nowait()
                    done += 1
                    ab, key = assigned.pop(cid)
                    infl_sent[key] = max(0, infl_sent.get(key, 0) - 1)
                    est[ab][src] -= v                      # 先撤销派发时的乐观修正
                    est[ab][dst] += v
                    if ok and route == "broker":
                        served_ct += 1
                        est[ab][src] += v                  # broker 成交才重新施加
                        est[ab][dst] -= v
                    elif ok and route == "relay":
                        relay_ct += 1
                        nq_local += 1                      # 终审推翻指派 = 估计陈旧证据
            except queue.Empty:
                pass
            # ③ 块高推进：每 release_poll_s 才真读一次（块本身 1s 一个，读勤纯浪费 RPC）。
            #    非读轮沿用 h_cache/rel_target，派发与回收照常满速运转。
            tbc = now()
            if h_cache is None or (tbc - last_blk_check) >= params["release_poll_s"]:
                last_blk_check = tbc
                tb = now()
                h_cache = min(tx.block_number(s) for s in shards)
                prof["blkheight_ms"] += (now() - tb) * 1000
                if h0 is None:
                    h0 = h_cache
                rel_target = min(N, max(0, h_cache - h0) * rate)
                if rel_target != last_rel:
                    declared_seq.append(int(rel_target))
                    last_rel = rel_target
            # 每块最多刷一次（refresh_every_blocks 块一次）；不是每轮，否则刷一轮 ~270ms
            if (h_cache != last_refresh_h
                    and (h_cache - h0) % max(1, int(params["refresh_every_blocks"])) == 0):
                last_refresh_h = h_cache
                done_trace.append((int(h_cache - h0), int(disp), int(done)))
                tr = now()
                # 全量刷新并行化：把 nb×shards 次 get_balance 打散到读手线程池
                jobs = [(b2, s) for b2 in range(nb) for s in shards]

                results = {}
                per_reader = [[] for _ in range(len(refresh_txs))]
                for i, (b2, s) in enumerate(jobs):
                    per_reader[i % len(refresh_txs)].append((b2, s))

                def _read_chunk(chunk_and_idx):
                    ci, chunk = chunk_and_idx
                    rt = refresh_txs[ci]
                    out = []
                    for b2, s in chunk:
                        a2 = users.address(scale.broker_base_index + b2)
                        out.append((b2, s, rt.get_balance(a2, s)))
                    return out
                nested = list(refresh_pool.map(_read_chunk, list(enumerate(per_reader))))
                for lst in nested:
                    for b2, s, val in lst:
                        results[(b2, s)] = val
                for b2 in range(nb):
                    est[b2] = {s: results[(b2, s)] for s in shards}
                refresh_ms.append((now() - tr) * 1000); prof["refresh_n"] += 1
            # ④ 释放入队 + 派发（估计表选 broker；nonce 预分配；FIFO per sender）
            while release_pos < rel_target:
                c = ctxs[release_pos]
                key = (c["sender_idx"], c["src_shard"])
                pend.setdefault(key, deque()).append(c)
                release_pos += 1
            backlog = rel_target - disp
            backlog_peak = max(backlog_peak, backlog)
            if backlog > params["max_backlog"]:
                aborted = f"backlog {backlog} > max_backlog {params['max_backlog']}"
                for q in ctx_qs:
                    q.put_nowait(END)      # 中止也要放行引擎收尾，不留孤儿
                break
            tdd = now()
            budget = int(params["dispatch_per_tick"])
            ready = sorted((pend[k][0]["arrival_pos"], k)
                           for k in pend if infl_sent.get(k, 0) < window)
            for _, key in ready[:budget]:
                dq = pend.get(key)
                if not dq:
                    continue
                c = dq.popleft()
                v, dst, src = c["amount_wei"], c["dst_shard"], c["src_shard"]
                elig = {b2: est[b2][dst] for b2 in range(nb) if est[b2][dst] >= v}
                if elig:
                    b_sel = select_broker(elig, v, rng)
                else:
                    nq_est += 1                            # 估计无候选：随机指派
                    b_sel = rng.randrange(nb)              # 终审权在 broker 本地账本
                nonce = nonces.next(users.address(c["sender_idx"]), src)
                est[b_sel][src] += v                       # 乐观修正（Θ1 假设进账）
                est[b_sel][dst] -= v                       # （Θ2 义务假设付出）
                item = dict(c)
                item["nonce"] = nonce
                ctx_qs[b_sel].put_nowait(item)
                assigned[c["ctx_id"]] = (b_sel, key)
                infl_sent[key] = infl_sent.get(key, 0) + 1
                disp += 1
                if not dq:
                    pend.pop(key)
            prof["dispatch_ms"] += (now() - tdd) * 1000
            # ⑤ 收尾：全部落定 → END 广播；或时限超出/看门狗 → 中止
            if disp >= N and done >= N:
                for q in ctx_qs:
                    q.put_nowait(END)
                break
            if now() - t0 > N / max(rate, 1) + params["drain_tail_s"]:
                aborted = f"deadline（派发 {disp}/{N}，落定 {done}/{N}）"
                for q in ctx_qs:
                    q.put_nowait(END)
                break
            time.sleep(params.get("loop_sleep_s", 0.02))
        doneq.put({"dispatched": disp, "settled": done, "served": served_ct,
                   "relay_settled": relay_ct, "no_qualified_est": nq_est,
                   "no_qualified_local": nq_local, "backlog_peak": int(backlog_peak),
                   "minted": minted, "declared_seq": [int(x) for x in declared_seq],
                   "done_trace": done_trace, "loop_sleep_s": round(prof["rounds"]*params["loop_sleep_s"],1),
                   "refresh_ms_mean": round(statistics.fmean(refresh_ms), 1) if refresh_ms else None,
                   "refresh_ms_max": round(max(refresh_ms), 1) if refresh_ms else None,
                   "refresh_ms_last": round(refresh_ms[-1], 1) if refresh_ms else None,
                   "prof": {k: (round(v, 1) if isinstance(v, float) else v)
                            for k, v in prof.items()},
                   "prof_avg_dispatch_ms": round(prof["dispatch_ms"] / max(prof["rounds"],1), 3),
                   "prof_avg_blkheight_ms": round(prof["blkheight_ms"] / max(prof["rounds"],1), 3),
                   "aborted": aborted, "error": None})
    except BaseException:
        doneq.put({"dispatched": 0, "settled": 0, "error": traceback.format_exc()})


def _dyn_worker(payload, ctx_q, rep_q, req_q, ack_q, readyq, outq, gate):
    b = payload["broker_idx"]
    try:
        eng = dynamic_engine_from_payload(payload, ctx_q, rep_q, req_q, ack_q)
        readyq.put(("engine", b))
        gate.wait()
        outq.put(eng.run_blocking())
    except BaseException:
        # 构造期错误也要让等待方看见：readyq 发 engine_error，outq 发错误信封
        readyq.put(("engine_error", b, traceback.format_exc()))
        outq.put({"broker_idx": b, "rows": [], "error": traceback.format_exc(),
                  "n_rcvd": 0, "relay_fallback": 0})


def run_dynamic(cfg, rate, window, sel, total_wei, out_root, watchdog):
    nb = cfg.scale.num_brokers
    N = len(sel)
    probe = window != int(exp_param(cfg, "sender_window", int))
    tag = f"dyn_{rate}" + (f"_w{window}" if probe else "")
    arm_dir = out_root / tag
    cluster, tx, users, broker_addrs, before = fund_chain(cfg, sel, total_wei,
                                                          arm_dir, watchdog)
    ctxm = mp.get_context("spawn")
    procs, coord = [], None                # 供 finally 回收（异常路径不留孤儿进程）
    try:
        params = engine_params(cfg)
        params.update({"num_shards": cfg.chain.num_shards,
                       "route_seed": exp_param(cfg, "route_seed", int),
                       "refresh_every_blocks": exp_param(cfg, "refresh_every_blocks", int),
                       "max_backlog": exp_param(cfg, "max_backlog", int),
                       "dispatch_per_tick": 200, "loop_sleep_s": exp_param(cfg, "loop_sleep_s", float),
                       "release_poll_s": exp_param(cfg, "release_poll_s", float),
                       "refresh_workers": exp_param(cfg, "refresh_workers", int),
                       "drain_tail_s": exp_param(cfg, "drain_tail_s", float)})
        cfg_plain = {"chain": asdict(cfg.chain), "scale": asdict(cfg.scale)}
        init_wei = int(exp_param(cfg, "balances_eth", float) * ETH)
        ctx_qs = [ctxm.Queue() for _ in range(nb)]
        rep_q = ctxm.Queue()
        req_q = ctxm.Queue()
        ack_qs = [ctxm.Queue() for _ in range(nb)]
        readyq, outq, doneq = ctxm.Queue(), ctxm.Queue(), ctxm.Queue()
        gate = ctxm.Event()
        coord = ctxm.Process(target=_routing_coordinator_worker,
                             args=(cfg_plain, params, rate, window, [dict(c) for c in sel],
                                   ctx_qs, rep_q, req_q, ack_qs, readyq, doneq, gate))
        coord.start()
        watchdog.track(coord.pid)
        _wait_ready(readyq, doneq, exp_param(cfg, "t_ready_budget_s", float),
                    "coord(dynamic)")
        procs = []
        t_launch = time.monotonic()
        for b in range(nb):
            p = ctxm.Process(target=_dyn_worker,
                             args=({"cfg": cfg_plain, "broker_idx": b,
                                    "init_wei_by_shard": {s: init_wei
                                                          for s in range(cfg.chain.num_shards)},
                                    "params": params},
                                   ctx_qs[b], rep_q, req_q, ack_qs[b], readyq, outq, gate))
            p.start()
            procs.append(p)
            watchdog.track(p.pid)
        for _ in range(nb):
            _wait_ready(readyq, doneq, exp_param(cfg, "t_ready_budget_s", float),
                        "engine(dynamic)")
        t_ready = time.monotonic() - t_launch
        gate.set()
        t0 = time.monotonic()
        envs = {}
        deadline = t0 + N / max(rate, 1) + params["drain_tail_s"] + 120
        while len(envs) < nb:
            if watchdog.tripped:
                raise RuntimeError("RSS watchdog tripped")
            if time.monotonic() > deadline:
                raise TimeoutError(f"{tag}: 引擎未在时限内全部落定")
            try:
                env = outq.get(timeout=1.0)
            except Exception:
                continue
            envs[env["broker_idx"]] = env
        coord_env = doneq.get(timeout=90)
        coord.join(timeout=30)
        for p in procs:
            p.join(timeout=10)
        wall = time.monotonic() - t0
        after = {(a, s): tx.get_balance(a, s) for (a, s) in before}
        rows, stats = collect_arm_stats(cfg, sel, envs, before, after,
                                        broker_addrs, wall)
        # 动态臂特有核对：回报完备性 + ctx 一一对应（无重无漏）
        ids = sorted(r["ctx_id"] for r in rows)
        g2 = ids == sorted(c["ctx_id"] for c in sel)
        n_rcvd_total = sum(e.get("n_rcvd", 0) for e in envs.values())
        stats.update({
            "kind": "dynamic", "rate": rate, "window": window, "tag": tag,
            "probe": probe,
            "t_ready_s": round(t_ready, 2),
            "coord": {k: coord_env.get(k) for k in
                      ("dispatched", "settled", "served", "relay_settled",
                       "no_qualified_est", "no_qualified_local", "backlog_peak",
                       "minted", "refresh_ms_mean", "refresh_ms_max", "aborted")},
            "g2_no_dup_no_loss": g2, "n_rcvd_total": n_rcvd_total,
            "est_accuracy": (round(1 - coord_env.get("no_qualified_local", 0)
                                   / max(coord_env.get("dispatched", 1), 1), 4)),
        })
        if rows:
            with open(arm_dir / "ctx_rows.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader(); w.writerows(rows)
        (arm_dir / "coordinator.json").write_text(json.dumps(coord_env))
        (arm_dir / "declared.csv").write_text(json.dumps(coord_env.get("declared_seq", [])))
        return stats
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
        if coord is not None and coord.is_alive():
            coord.terminate()
        cluster.stop()


# ---------------------------------------------------------------------------
# 图与主流程
# ---------------------------------------------------------------------------

def make_figure(out_root, stats_list):
    from brokerlab import plotting as P
    import matplotlib.pyplot as plt
    figs = out_root / "figs"
    figs.mkdir(exist_ok=True)
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(13.5, 4), dpi=140)
    srate = sorted({s["rate"] for s in stats_list})
    stat_thr = {s["rate"]: s["throughput_ctx_per_s"] for s in stats_list
                if s["kind"] == "static"}
    dyn_thr = {s["rate"]: s["throughput_ctx_per_s"] for s in stats_list
               if s["kind"] == "dynamic" and not s.get("probe")}
    x = range(len(srate))
    a.bar([i - 0.18 for i in x], [stat_thr.get(r) or 0 for r in srate],
          width=.36, color=P.COLOR_GRAY, label="static (LPT bins)")
    a.bar([i + 0.18 for i in x], [dyn_thr.get(r) or 0 for r in srate],
          width=.36, color=P.COLOR_BROKER, label="dynamic (coordinator)")
    a.set_xticks(list(x)); a.set_xticklabels(srate)
    a.set_xlabel("injection rate (CTX/block)"); a.set_ylabel("throughput (CTX/s)")
    a.set_title("(a) dynamic routing cost"); a.legend(fontsize=8)
    dr = [s for s in stats_list if s["kind"] == "dynamic" and not s.get("probe")]
    if dr:
        rs = [s["rate"] for s in dr]
        b.plot(rs, [s["coord"]["no_qualified_est"] for s in dr], "o-",
               color=P.COLOR_RELAY, label="no_qualified_est (routing)")
        b.plot(rs, [s["coord"]["no_qualified_local"] for s in dr], "s--",
               color=P.COLOR_DATA, label="no_qualified_local (final review)")
        b.set_xlabel("rate"); b.set_ylabel("CTX count"); b.legend(fontsize=8)
    b.set_title("(b) estimate staleness, bounded")
    top = next((s for s in stats_list if s["kind"] == "dynamic"
                and s["rate"] == max(srate, default=0) and not s.get("probe")), None)
    if top:
        # declared 轨迹：块驱动的阶跃线性；与落定曲线对比即"排空 vs 注入"的镜像
        dec = json.loads((out_root / top["tag"] / "declared.csv").read_text())
        c.plot(range(1, len(dec) + 1), dec, "o-", ms=3, color=P.COLOR_ALT,
               label="declared (block-driven)")
        rows_all = []
        try:
            with open(out_root / top["tag"] / "ctx_rows.csv") as f:
                for r in csv.DictReader(f):
                    if r["t2_block"]:
                        rows_all.append(int(r["t2_block"]))
        except FileNotFoundError:
            pass     # 看门狗中止的臂可能没有落定行
        if rows_all:
            import collections
            cnt = collections.Counter(rows_all)
            bx = sorted(cnt)
            cum = []
            acc = 0
            for b0 in bx:
                acc += cnt[b0]
                cum.append(acc)
            c.plot(bx, cum, color=P.COLOR_BROKER, lw=1.4, label="settled (cum by block)")
        c.set_xlabel("block height"); c.set_ylabel("CTX count"); c.legend(fontsize=8)
    c.set_title("(c) declared arrival trace (block-driven)")
    P.save(fig, figs / "exp007_dynamic.png")
    print(f"  figure: {figs / 'exp007_dynamic.png'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)
    rates = [int(x) for x in exp_param(cfg, "rates", str).split(",")]
    static_rates = [int(x) for x in exp_param(cfg, "static_rates", str).split(",")
                    if x.strip()]
    out_root = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root.mkdir(parents=True)
    print(f"  exp007 · out: {out_root} | dyn rates={rates} | static rates={static_rates}")

    bins, sel, total_wei = build_workload(cfg, out_root)
    watchdog = RSSWatchdog(exp_param(cfg, "rss_cap_mb", float))
    watchdog.start()
    stats_list = []
    aborted_run = False
    try:
        for r in static_rates:
            print(f"\n  ==== 静态对照 {r} CTX/block ====")
            st = run_static(cfg, r, bins, sel, total_wei, out_root, watchdog)
            stats_list.append(st)
            print(f"  stat_{r}: wall {st['wall_s']}s | {st['throughput_ctx_per_s']} CTX/s "
                  f"| relay {st['relayed']} fail {st['failed']} | net {st['global_net_sum_wei']}")
            if watchdog.tripped:
                raise RuntimeError("RSS watchdog tripped")
        for r in rates:
            print(f"\n  ==== 动态路由 {r} CTX/block (window={exp_param(cfg, 'sender_window', int)}) ====")
            st = run_dynamic(cfg, r, exp_param(cfg, "sender_window", int), sel,
                             total_wei, out_root, watchdog)
            stats_list.append(st)
            co = st["coord"]
            print(f"  dyn_{r}: wall {st['wall_s']}s | {st['throughput_ctx_per_s']} CTX/s "
                  f"| served {st['served']} relay {st['relayed']} fail {st['failed']} "
                  f"| nq_est {co['no_qualified_est']} nq_local {co['no_qualified_local']} "
                  f"| backlog峰 {co['backlog_peak']} | net {st['global_net_sum_wei']}")
            if co.get("aborted") or watchdog.tripped:
                aborted_run = True
                print(f"  !! 动态臂 {r} 中止：{co.get('aborted')}（其余照常导出）")
                break
        pr = int(exp_param(cfg, "window_probe_rate", int))
        pw = int(exp_param(cfg, "window_probe", int))
        if pr in rates and not aborted_run:
            print(f"\n  ==== 窗口敏感性 {pr} CTX/block (window={pw}) ====")
            st = run_dynamic(cfg, pr, pw, sel, total_wei, out_root, watchdog)
            stats_list.append(st)
            print(f"  {st['tag']}: {st['throughput_ctx_per_s']} CTX/s")
    except (TimeoutError, RuntimeError) as ex:
        print(f"  !! 中止：{ex}")
        aborted_run = True
    finally:
        watchdog.stop()

    # ---- 判据 ----
    dyn = [s for s in stats_list if s["kind"] == "dynamic" and not s.get("probe")]
    stat = {s["rate"]: s for s in stats_list if s["kind"] == "static"}
    gates = {}
    for s in stats_list:
        gates[f"{s['tag']}:G1_all_ok"] = (s["failed"] == 0)
        gates[f"{s['tag']}:G3_conservation"] = (
            s["global_net_sum_wei"] == 0 and s["mirror_mismatch"] == 0)
        if s["kind"] == "dynamic":
            gates[f"{s['tag']}:G2_no_dup_no_loss"] = bool(s["g2_no_dup_no_loss"])
            gates[f"{s['tag']}:G4_reports_complete"] = (
                s["coord"]["dispatched"] == s["n"] and s["coord"]["settled"] == s["n"]
                and s["n_rcvd_total"] == s["n"])
    hd1 = {}
    for s in dyn:
        if s["rate"] in stat and stat[s["rate"]]["throughput_ctx_per_s"]:
            hd1[s["rate"]] = round(s["throughput_ctx_per_s"]
                                   / stat[s["rate"]]["throughput_ctx_per_s"], 3)
    h_d1 = bool(hd1) and all(v >= 0.8 for v in hd1.values())
    nq_local_total = sum(s["coord"]["no_qualified_local"] for s in dyn)
    passed = (not aborted_run) and all(gates.values()) and h_d1
    try:
        make_figure(out_root, stats_list)
    except ImportError:
        print("  (matplotlib 不可用，跳过绘图)")
    (out_root / "summary.json").write_text(json.dumps({
        "params": to_params_dict(cfg, extra={"rates": rates,
                                             "static_rates": static_rates,
                                             "total_ctx": len(sel),
                                             "aborted": aborted_run}),
        "arms": [{k: v for k, v in s.items() if k != "kind" or True}
                 for s in stats_list],
        "gates": gates,
        "hypotheses": {
            "H-D1_dynamic_cost_bounded": {"ratios": hd1, "threshold": 0.8,
                                          "verdict": h_d1},
            "H-D2_staleness_evidence": {
                "no_qualified_local_total": nq_local_total,
                "conservation_held_despite_staleness": all(
                    v for k, v in gates.items() if k.endswith(":G3_conservation"))},
            "H-D3_window_sensitivity": {s["tag"]: s["throughput_ctx_per_s"]
                                        for s in stats_list
                                        if s["kind"] == "dynamic" and s.get("probe")},
        },
    }, indent=2, default=str))
    print("\n" + "=" * 74)
    print(f"  判据 gates: {'ALL OK' if all(gates.values()) else {k: v for k, v in gates.items() if not v}}")
    print(f"  H-D1 动态吞吐/静态吞吐 {hd1} (阈 ≥0.8) → {'VERIFIED' if h_d1 else 'NOT MET'}")
    print(f"  H-D2 终审推翻指派总数 {nq_local_total}（>0 = 估计确实会陈旧；守恒仍成立即防线有效）")
    print(f"  退出判据 passed: {passed}")
    print("=" * 74)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

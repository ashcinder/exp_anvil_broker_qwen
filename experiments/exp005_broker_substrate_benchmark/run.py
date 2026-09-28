#!/usr/bin/env python3
"""exp005 · broker 执行基底基准 —— 独立启动脚本（执行 + 绘图）。

同一个流水线 tick 引擎（brokerlab/broker_engine.py），同一个静态指派的工作负载，
三种容器各跑一遍：serial（单进程轮转 50 引擎）/ threads（50 线程）/ processes（50 进程）。
差的是执行基底，不是工作量——设计/判据见本目录 README.md。
运行：python run.py [--set exp.ctx_per_broker=2 --set exp.modes=serial]
产物：out/<ts>/{assignment.csv, <mode>/ctx_rows.csv+actor_summary.csv, summary.json, figs/}
"""
import argparse
import csv
import json
import os
import sys
import threading
import time
import traceback
import multiprocessing as mp
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from brokerlab.broker_engine import (BrokerEngine, EngineSpec,
                                     assign_sender_groups, build_services,
                                     engine_from_payload, funding_plan, group_key)
from brokerlab.chain import AnvilCluster, Connections
from brokerlab.config import ETH, apply_overrides, load_config, to_params_dict
from brokerlab.identity import UserManager
from brokerlab.procmon import vmrss_kb
from brokerlab.real_data import extract
from brokerlab.tx import TxService

MODES = ("serial", "threads", "processes")


def exp_param(cfg, key, kind=str):
    """实验参数唯一来源=config.yaml 的 exp: 段，缺键即报错（exp001/002 纪律）。"""
    if key not in cfg.exp:
        sys.exit(f"config.yaml 的 exp: 段缺少参数 {key!r}（本实验参数必须全部显式声明）")
    return kind(cfg.exp[key])


def pct(vals, q):
    """分位数（与 exp001 同实现）。"""
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(q / 100 * (len(s) - 1))))]


def dist(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"mean": None, "p50": None, "p95": None, "min": None, "max": None, "n": 0}
    return {"mean": round(sum(vals) / len(vals), 4), "p50": pct(vals, 50),
            "p95": pct(vals, 95), "min": min(vals), "max": max(vals), "n": len(vals)}


# ---------------------------------------------------------------------------
# RSS 看门狗：跨平台采样（getrusage 只报单个子进程，不能求和——见 PLAN 讨论）
# ---------------------------------------------------------------------------

class RSSMonitor(threading.Thread):
    def __init__(self, cap_mb: float):
        super().__init__(daemon=True)
        self.pids = {os.getpid()}
        self.cap_mb = cap_mb
        self.peak_mb = 0.0
        self.tripped = False
        self._stop = threading.Event()

    def track(self, pid: int):
        self.pids.add(pid)

    def _vmrss_kb(self, pid: int):
        return vmrss_kb(pid)

    def run(self):
        while not self._stop.is_set():
            total = 0.0
            for pid in list(self.pids):
                kb = self._vmrss_kb(pid)
                if kb is not None:
                    total += kb
            self.peak_mb = max(self.peak_mb, total / 1024)
            if total / 1024 > self.cap_mb:
                self.tripped = True
                return
            self._stop.wait(1.0)

    def stop(self):
        self._stop.set()


# ---------------------------------------------------------------------------
# 工作负载（三方案共用，一次性构造并钉死）
# ---------------------------------------------------------------------------

def build_workload(cfg, out_root: Path):
    """真实 trace 有巨鲸发送者（单个 (sender,src) 组实测最多 34 笔/2000 行扫描）。
    组是 nonce 原子单位不可拆 ⇒ 先按组大小上限过滤（group_cap_mult × 平均组大小），
    再按 CSV 首现顺序整组累加到 ≥ N 为止。被滤掉的巨鲸流量如实计数打印。"""
    nb = cfg.scale.num_brokers
    k = exp_param(cfg, "ctx_per_broker", int)
    n = nb * k
    pool = extract(cfg.traffic.real_csv_path,
                   num_shards=cfg.chain.num_shards, num_users=cfg.scale.num_users,
                   user_base_index=cfg.scale.user_base_index,
                   value_floor_wei=int(cfg.traffic.value_floor_eth * ETH),
                   value_cap_wei=int(cfg.traffic.value_cap_eth * ETH),
                   limit=exp_param(cfg, "pool_scan", int))
    groups: dict = {}
    for r in pool:
        c = asdict(r)
        groups.setdefault(group_key(c), []).append(c)
    glist = list(groups.values())
    # 上限随组均值走（= pool_scan/用户数 决定的自然密度），与 K 无关：
    # 巨鲸 = 超出均值 group_cap_mult 倍的组。pool_scan 要调到 均值组 ≈ K/2，
    # 每箱才装得下 ≥2 组、LPT 才有腾挪空间。当前 K=800、pool_scan=200000。
    mean_g = len(pool) / max(len(glist), 1)
    cap = mean_g * exp_param(cfg, "group_cap_mult", float)
    kept_g = [g for g in glist if len(g) <= cap]
    whale_ctx = sum(len(g) for g in glist if len(g) > cap)
    # 小组优先累加到 ≥N：牺牲"重发送者代表性"换箱间均衡（基准的显式取舍，
    # README 如实声明；金额分布不受影响，组大小才是墙钟的原子单位）。
    kept_g.sort(key=len)
    sel: list = []
    for g in kept_g:
        sel.extend(g)
        if len(sel) >= n:
            break
    if len(sel) < n * 0.9:
        sys.exit(f"过滤巨鲸后只剩 {len(sel)} 笔（目标 {n}）——调大 exp.pool_scan "
                 f"或 exp.group_cap_mult")
    # 只截取最后一组的尾部，使正式档严格等于 exp008 的 40000 笔。
    # 入选的同一 (sender, src) 仍由 assign_sender_groups 整体分配给单一 broker。
    sel = sel[:n]
    bins = assign_sender_groups(sel, nb, exp_param(cfg, "seed", int))
    print(f"  巨鲸过滤：{whale_ctx} 笔落在 {sum(1 for g in glist if len(g) > cap)} "
          f"个超 {cap:.0f} 笔的组（整组弃用）")
    fp = funding_plan(bins, cfg.chain.num_shards,
                      exp_param(cfg, "fund_buffer_eth", float),
                      exp_param(cfg, "broker_buffer_eth", float))
    with open(out_root / "assignment.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["broker_idx", "sender_idx", "src_shard", "n_ctx", "total_wei"])
        for b, cs in sorted(bins.items()):
            per = {}
            for c in cs:
                key = group_key(c)
                agg = per.setdefault(key, [0, 0])
                agg[0] += 1
                agg[1] += int(c["amount_wei"])
            for (si, sh), (cnt, wei) in sorted(per.items()):
                w.writerow([b, si, sh, cnt, wei])
    counts = [len(v) for v in bins.values()]
    print(f"  workload: {n} CTX → {nb} bins | counts min/max = {min(counts)}/{max(counts)} "
          f"| stream total {fp.total_wei / ETH:.1f} ETH")
    return bins, fp


# ---------------------------------------------------------------------------
# 链上准备（每个方案一条全新链；exp002 逐条件起链的先例）
# ---------------------------------------------------------------------------

def chain_up(cfg, mode_dir: Path, monitor: RSSMonitor):
    users = UserManager(cfg.chain.mnemonic)
    cluster = AnvilCluster(cfg.chain, mode_dir / "logs")
    cluster.start()
    cluster.write_pids(mode_dir / "pids.json")
    for i, pid in json.loads((mode_dir / "pids.json").read_text()).items():
        monitor.track(pid)
    conns = Connections(cfg.chain)
    conns.wait_all_ready()
    tx = TxService(conns, users, cfg.chain)
    return cluster, tx, users


def fund(cfg, tx: TxService, users: UserManager, bins, fp):
    shards = range(cfg.chain.num_shards)
    for b in bins:
        addr = users.address(cfg.scale.broker_base_index + b)
        for s in shards:
            tx.set_balance_setup(addr, s, fp.broker_init_wei[b][s])
    buf = int(exp_param(cfg, "fund_buffer_eth", float) * ETH)
    for (idx, sh), amt in fp.sender_out_wei.items():
        tx.set_balance_setup(users.address(idx), sh, amt + buf)
    return buf


def watch_addrs(cfg, users, bins):
    idxs = set()
    for cs in bins.values():
        for c in cs:
            idxs.add(c["sender_idx"])
            idxs.add(c["receiver_idx"])
    idxs |= {cfg.scale.broker_base_index + b for b in bins}
    addrs = sorted({users.address(i) for i in idxs})
    return {(a, s) for a in addrs for s in range(cfg.chain.num_shards)}


def snapshot(tx: TxService, keys):
    return {k: tx.get_balance(*k) for k in keys}


# ---------------------------------------------------------------------------
# 引擎参数与规格
# ---------------------------------------------------------------------------

def engine_params(cfg):
    return {"max_inflight": exp_param(cfg, "max_inflight", int),
            "poll_s": exp_param(cfg, "poll_s", float),
            "probe_s": exp_param(cfg, "probe_s", float),
            "probe_first_s": exp_param(cfg, "probe_first_s", float),
            "timeout_s": exp_param(cfg, "timeout_s", float)}


def make_specs(cfg, bins, fp):
    return {b: EngineSpec(broker_idx=b, ctxs=tuple(bins[b]),
                          init_wei_by_shard=dict(fp.broker_init_wei[b]))
            for b in bins}


def _mp_worker(payload, readyq, outq, gate):
    """spawn 进程入口：本地重建全套服务，等闸门，跑完回传信封。"""
    b = payload["spec"]["broker_idx"]
    try:
        eng = engine_from_payload(payload)
        readyq.put(b)
        gate.wait()
        outq.put(eng.run_blocking())
    except BaseException:
        outq.put({"broker_idx": b, "rows": [], "error": traceback.format_exc()})


# ---------------------------------------------------------------------------
# 三方案：同一引擎，只差容器
# ---------------------------------------------------------------------------

def run_serial(cfg, cfg_plain, specs, ep, monitor):
    """serial 容器：一个进程协作式轮转 50 个引擎的 tick_once()。"""
    t_build = time.monotonic()
    engines = []
    for b in specs:
        tx_b, users_b, scale_b = build_services(cfg_plain)
        engines.append(BrokerEngine(tx_b, users_b, specs[b],
                                    broker_base_index=scale_b.broker_base_index, **ep))
    t_ready = time.monotonic() - t_build
    cpu0 = os.times()
    t0 = time.monotonic()
    while not all(e.done() for e in engines):
        for e in engines:
            e.tick_once()
        time.sleep(ep["poll_s"])
        if monitor.tripped:
            return None, t_ready, "watchdog"
    wall = time.monotonic() - t0
    cpu1 = os.times()
    envs = []
    for e in engines:
        env = e.envelope()
        env["cpu_user_s"] = round((cpu1[0] - cpu0[0]) / len(engines), 4)   # 单进程：按引擎摊分
        env["cpu_sys_s"] = round((cpu1[1] - cpu0[1]) / len(engines), 4)
        env["pid"] = os.getpid()
        envs.append(env)
    return envs, t_ready, wall


def run_threads(cfg, cfg_plain, specs, ep, monitor):
    """threads 容器：50 线程各跑一个引擎，Barrier 齐发。"""
    nb = len(specs)
    engines = []
    for b in specs:
        tx_b, users_b, scale_b = build_services(cfg_plain)
        engines.append(BrokerEngine(tx_b, users_b, specs[b],
                                    broker_base_index=scale_b.broker_base_index, **ep))
    barrier = threading.Barrier(nb + 1)
    out = {}

    def body(b):
        try:
            barrier.wait()
            out[b] = engines[b].run_blocking(t0=time.monotonic())
        except BaseException:
            out[b] = {"broker_idx": b, "rows": [], "error": traceback.format_exc()}

    cpu0 = os.times()
    t_launch = time.monotonic()
    ths = [threading.Thread(target=body, args=(b,)) for b in range(nb)]
    for th in ths:
        th.start()
    barrier.wait()                    # 全部就位后齐发：主线程也是 barrier 一员
    t_ready = time.monotonic() - t_launch
    t0 = time.monotonic()
    for th in ths:
        th.join()
    wall = time.monotonic() - t0
    cpu1 = os.times()
    envs = [out[b] for b in range(nb)]
    # 线程方案的 CPU 必须以【进程总账】为准摊分：os.times() 是进程级，
    # run_blocking 里每线程各自差分 = 同一份账记 50 遍（首轮 1779s 假象的根源）。
    for env in envs:
        env["cpu_user_s"] = round((cpu1[0] - cpu0[0]) / nb, 4)
        env["cpu_sys_s"] = round((cpu1[1] - cpu0[1]) / nb, 4)
        env.setdefault("pid", os.getpid())
        env.setdefault("inflight_peak", None)
        env.setdefault("reserve_blocked", None)
        env.setdefault("n_assigned", None)
    return envs, t_ready, wall


def run_processes(cfg, cfg_plain, specs, ep, monitor):
    """processes 容器：50 个 spawn 子进程，服务全本地重建，Event 齐发。"""
    nb = len(specs)
    ctxm = mp.get_context("spawn")
    readyq, outq = ctxm.Queue(), ctxm.Queue()
    gate = ctxm.Event()
    payload_base = {"cfg": cfg_plain, "params": ep}
    procs = []
    t_start = time.monotonic()
    for b in specs:
        sp = specs[b]
        payload = dict(payload_base, spec={"broker_idx": sp.broker_idx,
                                           "ctxs": [dict(c) for c in sp.ctxs],
                                           "init_wei_by_shard":
                                               {str(k): int(v)
                                                for k, v in sp.init_wei_by_shard.items()}})
        p = ctxm.Process(target=_mp_worker, args=(payload, readyq, outq, gate), daemon=False)
        p.start()
        procs.append((b, p))
        monitor.track(p.pid)
    try:
        ready: set = set()
        budget = exp_param(cfg, "t_ready_budget_s", float)
        while len(ready) < nb:
            if time.monotonic() - t_start > budget:
                _drain_errors(outq)
                return None, None, "t_ready_budget"
            try:
                ready.add(readyq.get(timeout=1.0))
            except Exception:
                if any(not p.is_alive() for _, p in procs) and len(ready) < nb:
                    _drain_errors(outq)
                    return None, None, "worker_died_before_ready"
        t_ready = time.monotonic() - t_start
        gate.set()
        t0 = time.monotonic()
        envs = {}
        deadline = t0 + exp_param(cfg, "timeout_s", float) * 4
        while len(envs) < nb:
            if monitor.tripped:
                _drain_errors(outq)
                return None, t_ready, "watchdog"
            if time.monotonic() > deadline:
                _drain_errors(outq)
                return None, t_ready, "timeout_arm"
            try:
                env = outq.get(timeout=1.0)
            except Exception:
                alive = [b for b, p in procs if not p.is_alive()
                         and b not in envs]
                if alive:
                    _drain_errors(outq)
                    return None, t_ready, f"worker_died:{alive[:5]}"
                continue
            envs[env["broker_idx"]] = env
        wall = time.monotonic() - t0
        for b, p in procs:
            p.join(timeout=10)
        return [envs[b] for b in specs], t_ready, wall
    finally:
        gate.set()
        for _, p in procs:
            if p.is_alive():
                p.terminate()


def _drain_errors(outq):
    """失败分支里把 worker 回传的错误信封打印出来（否则现场丢在队列里）。"""
    while True:
        try:
            env = outq.get_nowait()
        except Exception:
            return
        if env.get("error"):
            print(f"  worker broker_{env['broker_idx']} error:\n{env['error']}")


# ---------------------------------------------------------------------------
# 校验与聚合
# ---------------------------------------------------------------------------

def verify_arm(cfg, tx, users, bins, fp, envs, before, after, rows_all, wall_s):
    """一道容器的正确性门：G1 全成功、G2 负载同一性、G3 链上==镜像且净和 0。"""
    n_expect = sum(len(v) for v in bins.values())
    shard_rng = range(cfg.chain.num_shards)
    g1 = all(not env.get("error") for env in envs) \
        and len(rows_all) == n_expect \
        and all(r["ok"] for r in rows_all) \
        and sum(env.get("reserve_blocked") or 0 for env in envs) == 0
    g2 = sorted(r["ctx_id"] for r in rows_all) == \
        sorted(c["ctx_id"] for cs in bins.values() for c in cs) \
        and all(r["route"] == "broker" for r in rows_all)
    # G3 守恒：链上 broker 子账户 == 垫资 + Σsrc − Σdst（用行重算，不信镜像）
    per = {b: dict(fp.broker_init_wei[b]) for b in bins}
    mirror_ok = True
    for env in envs:
        b = env["broker_idx"]
        if env.get("error"):
            mirror_ok = False
            continue
        for r in env["rows"]:
            if not r["ok"]:
                continue
            per[b][r["src"]] += r["amount_wei"]
            per[b][r["dst"]] -= r["amount_wei"]
        for s_str, v in env["final_mirror"].items():
            if v != per[b][int(s_str)]:
                mirror_ok = False
    chain_ok = True
    for b in bins:
        addr = users.address(cfg.scale.broker_base_index + b)
        for s in shard_rng:
            delta = after[(addr, s)] - before[(addr, s)]
            expected_delta = per[b][s] - fp.broker_init_wei[b][s]
            if delta != expected_delta:
                chain_ok = False
    drift = sum(after[k] - before[k] for k in before)
    g3 = chain_ok and mirror_ok and drift == 0
    stats = {
        "wall_s": round(wall_s, 2), "n_ctx": len(rows_all),
        "throughput_ctx_per_s": round(len(rows_all) / wall_s, 3) if wall_s > 0 else None,
        "e2e_secs": dist([r["e2e_secs"] for r in rows_all]),
        "hops_total": dist([r["hops_total"] for r in rows_all]),
        "t1_secs": dist([r["t1_secs"] for r in rows_all]),
        "t2_secs": dist([r["t2_secs"] for r in rows_all]),
        "inflight_peak_max": max((e.get("inflight_peak") or 0 for e in envs), default=None),
        "reserve_blocked_total": sum(e.get("reserve_blocked") or 0 for e in envs),
        "cpu_s_total": round(sum((e.get("cpu_user_s") or 0) + (e.get("cpu_sys_s") or 0)
                                 for e in envs), 3),
        "gates": {"G1_all_ok_and_no_throttle": g1, "G2_workload_identity": g2,
                  "G3_conservation": g3},
        "global_net_sum_wei": drift,
    }
    return stats, rows_all, per


def load_anchor(cfg):
    """读取 exp001 阻塞式产物作外部锚点（e2e ECDF 曲线 + 每笔秒数）。"""
    adir = ROOT / exp_param(cfg, "anchor_dir")
    rows_csv = adir / "ctx_rows.csv"
    summ = adir / "summary.json"
    if not rows_csv.exists():
        return None
    e2e = []
    total_rows = 0
    with open(rows_csv) as f:
        for r in csv.DictReader(f):
            total_rows += 1
            if r["route"] == "broker" and r["e2e_secs"]:
                e2e.append(float(r["e2e_secs"]))
    wall = json.loads(summ.read_text())["params"]["_runtime"]["wall_s"]
    # 阻塞口径的每笔成本 = 整次 run 的墙钟 ÷ 全部 CTX 数（含 relay 方案），
    # 不是只除 broker 半——锚点是"这套基座跑一笔要多久"。
    secs_per_ctx = wall / (total_rows or 1)
    return {"dir": str(adir), "e2e": e2e, "secs_per_ctx": round(secs_per_ctx, 4),
            "e2e_mean": round(sum(e2e) / len(e2e), 4)}


# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------

def make_figure(out_root, anchor, stats_by_mode, per_actor):
    """三面板：(a) 各容器墙钟 (b) e2e ECDF 叠 exp001 阻塞虚线 (c) 箱大小×耗时。"""
    from brokerlab import plotting as P
    import matplotlib.pyplot as plt

    figs = out_root / "figs"
    figs.mkdir(exist_ok=True)
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(13.5, 4.2), dpi=140)

    order = [m for m in MODES if m in stats_by_mode]
    walls = [stats_by_mode[m]["wall_s"] for m in order]
    cols = [P.COLOR_GRAY, P.COLOR_BROKER, P.COLOR_ALT]
    display = {"serial": "Single Process", "threads": "50 Threads",
               "processes": "50 Processes"}
    a.barh(range(len(order)), walls, color=cols[:len(order)])
    for i, m in enumerate(order):
        st = stats_by_mode[m]
        a.text(st["wall_s"] * 1.02, i,
               f"{st['wall_s']:.1f} s | {st['throughput_ctx_per_s']:.1f} transactions/s",
               va="center", fontsize=8)
    a.set_yticks(range(len(order)))
    a.set_yticklabels([display[m] for m in order])
    a.set_xlim(0, max(walls) * 1.38)
    a.set_xlabel("Time to Complete the Workload (seconds)")
    a.set_title("(a) Workload Completion Time")

    lab_by_mode = {"serial": ("Single Process", P.COLOR_GRAY),
                   "threads": ("50 Threads", P.COLOR_BROKER),
                   "processes": ("50 Processes", P.COLOR_ALT)}
    if anchor:
        vals = sorted(anchor["e2e"])
        ys = [(i + 1) / len(vals) for i in range(len(vals))]
        b.step(vals, ys, where="post", color=P.COLOR_DATA, lw=1.2, ls="--",
               label=f"Blocking Execution ({anchor['secs_per_ctx']:.2f} s/transaction)")
    for m in order:
        vals = [r for r in stats_by_mode[m].get("_e2e_all", []) if r is not None]
        if not vals:
            continue
        vals = sorted(vals)
        ys = [(i + 1) / len(vals) for i in range(len(vals))]
        lab, col = lab_by_mode[m]
        b.step(vals, ys, where="post", color=col, lw=1.6, label=lab)
    b.set_xlabel("End-to-End Transaction Latency (seconds)")
    b.set_ylabel("Cumulative Fraction of Transactions (ECDF)")
    b.legend(fontsize=7, loc="upper left", framealpha=.95)
    b.set_title("(b) End-to-End Latency Distribution")

    for m in order:
        pts = per_actor.get(m, [])
        if pts:
            lab, col = lab_by_mode[m]
            c.scatter([p[0] for p in pts], [p[1] for p in pts], s=10, alpha=.6,
                      color=col, label=lab)
    c.set_xlabel("Transactions Assigned to Each Broker")
    c.set_ylabel("Broker Processing Time (seconds)")
    c.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1.0),
             borderaxespad=0, frameon=False)
    c.set_title("(c) Broker Workload Size and Processing Time")

    P.save(fig, figs / "exp005_substrate.png")
    print(f"  figure: {figs / 'exp005_substrate.png'}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)

    modes = [m.strip() for m in exp_param(cfg, "modes").split(",") if m.strip()]
    if not set(modes) <= set(MODES):
        sys.exit(f"unknown mode in {modes}; allowed {MODES}")
    out_root = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root.mkdir(parents=True)
    print(f"  exp005 · out: {out_root} | modes={modes}")

    bins, fp = build_workload(cfg, out_root)
    specs = make_specs(cfg, bins, fp)
    ep = engine_params(cfg)
    cfg_plain = {"chain": asdict(cfg.chain), "scale": asdict(cfg.scale)}
    anchor = load_anchor(cfg)
    if anchor:
        print(f"  anchor: exp001 blocking {anchor['secs_per_ctx']} s/CTX "
              f"(e2e mean {anchor['e2e_mean']}s)")

    monitor = RSSMonitor(exp_param(cfg, "rss_cap_mb", float))
    monitor.start()
    stats_by_mode, per_actor, e2e_by_mode = {}, {}, {}
    serial_thr = None
    try:
        for mode in modes:
            print(f"\n  ==== 方案 {mode} ====")
            mode_dir = out_root / mode
            mode_dir.mkdir(parents=True)
            cluster, tx, users = chain_up(cfg, mode_dir, monitor)
            try:
                fund(cfg, tx, users, bins, fp)
                keys = watch_addrs(cfg, users, bins)
                before = snapshot(tx, keys)
                t0_run = time.monotonic()
                if mode == "serial":
                    envs, t_ready, res = run_serial(cfg, cfg_plain, specs, ep, monitor)
                elif mode == "threads":
                    envs, t_ready, res = run_threads(cfg, cfg_plain, specs, ep, monitor)
                else:
                    envs, t_ready, res = run_processes(cfg, cfg_plain, specs, ep, monitor)
                if envs is None:
                    print(f"  !! 方案 {mode} 中止（{res}）")
                    return 2
                wall = res if isinstance(res, float) else (time.monotonic() - t0_run)
                after = snapshot(tx, keys)
                rows_all = [r for env in envs for r in env["rows"]]
                stats, rows_all, per = verify_arm(
                    cfg, tx, users, bins, fp, envs, before, after, rows_all, wall)
                stats["t_ready_s"] = round(t_ready, 2)
                stats["rss_peak_mb_monitor"] = round(monitor.peak_mb, 0)
                if mode == "serial":
                    serial_thr = stats["throughput_ctx_per_s"]
                stats_by_mode[mode] = stats
                e2e_by_mode[mode] = [r["e2e_secs"] for r in rows_all]
                per_actor[mode] = [(env["n_assigned"], env.get("actor_wall_s"))
                                   for env in envs]
                with open(mode_dir / "ctx_rows.csv", "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(rows_all[0].keys()))
                    w.writeheader()
                    w.writerows(rows_all)
                with open(mode_dir / "actor_summary.csv", "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["broker_idx", "n_assigned", "actor_wall_s", "run_window_s",
                                "cpu_user_s", "cpu_sys_s", "inflight_peak",
                                "reserve_blocked", "pid", "error"])
                    for env in envs:
                        w.writerow([env["broker_idx"], env["n_assigned"],
                                    env.get("actor_wall_s"), env.get("run_window_s"),
                                    env.get("cpu_user_s"), env.get("cpu_sys_s"),
                                    env["inflight_peak"], env["reserve_blocked"],
                                    env.get("pid"), env.get("error")])
                print(f"  {mode}: wall {stats['wall_s']}s | "
                      f"{stats['throughput_ctx_per_s']} CTX/s | e2e mean "
                      f"{stats['e2e_secs']['mean']}s | G1/2/3 "
                      f"{[stats['gates'][k] for k in stats['gates']]}")
            finally:
                cluster.stop()
    except KeyboardInterrupt:
        print("  !! KeyboardInterrupt，导出已有结果后退出")
        monitor.stop()
        _export(cfg, out_root, modes, bins, stats_by_mode, e2e_by_mode,
                per_actor, anchor, serial_thr, aborted=True)
        return 2
    monitor.stop()

    ok, summary = _export(cfg, out_root, modes, bins, stats_by_mode, e2e_by_mode,
                          per_actor, anchor, serial_thr, aborted=False)
    print(f"\n  artifacts: {out_root}/")
    return 0 if ok else 1


def _export(cfg, out_root, modes, bins, stats_by_mode, e2e_by_mode,
            per_actor, anchor, serial_thr, aborted):
    for m, st in stats_by_mode.items():
        st["_e2e_all"] = e2e_by_mode.get(m, [])
    n = sum(len(v) for v in bins.values())
    speedup = {}
    if "serial" in stats_by_mode:
        for m in stats_by_mode:
            if m != "serial" and stats_by_mode[m]["wall_s"] > 0:
                speedup[m] = round(stats_by_mode["serial"]["wall_s"]
                                   / stats_by_mode[m]["wall_s"], 2)
    gates = {}
    for m, st in stats_by_mode.items():
        for gk, gv in st["gates"].items():
            gates[f"{m}:{gk}"] = gv
    min_sp = exp_param(cfg, "serial_speedup_min", float)
    # H-B1 只在"串行方案 + 至少一个并行方案"时可判；dev 单方案不进退出码判定（README 言明）
    hb1 = all(v >= min_sp for v in speedup.values()) if speedup else None
    g4 = None
    if anchor and "serial" in stats_by_mode and serial_thr:
        g4 = round(serial_thr * anchor["secs_per_ctx"], 2)   # × 阻塞口径
    gates_ok = all(gates.values()) and not aborted and bool(gates)
    passed = gates_ok and (hb1 is not False) and (g4 is None or g4 >= 5)
    hb2 = None
    if "threads" in stats_by_mode and "processes" in stats_by_mode:
        p95t = stats_by_mode["threads"]["e2e_secs"]["p95"]
        p95p = stats_by_mode["processes"]["e2e_secs"]["p95"]
        if p95t and p95p:
            hb2 = round(p95t / p95p, 3)
    summary = {
        "params": to_params_dict(cfg, extra={
            "modes_run": modes, "total_ctx": n,
            "anchor": anchor and {k: v for k, v in anchor.items() if k != "e2e"},
            "aborted": aborted}),
        "workload": {"num_brokers": cfg.scale.num_brokers,
                     "bin_counts": sorted(len(v) for v in bins.values())},
        "modes": {m: {k: v for k, v in st.items() if k != "_e2e_all"}
                  for m, st in stats_by_mode.items()},
        "speedup_vs_serial": speedup,
        "g4_tick_vs_blocking_x": g4,
        "hypotheses": {
            "H-B1_parallel_speedup_vs_serial": {"speedup": speedup,
                                                "min_required": min_sp,
                                                "verdict": hb1},
            "H-B2_p95_threads_over_processes (evidence)": hb2,
            "H-B4": {m: {"cpu_s_total": stats_by_mode[m].get("cpu_s_total"),
                         "rss_peak_mb": stats_by_mode[m]["rss_peak_mb_monitor"]}
                     for m in stats_by_mode}},
        "gates": gates,
        "passed": passed,
    }
    (out_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False))
    try:
        make_figure(out_root, anchor, stats_by_mode, per_actor)
    except ImportError:
        print("  (matplotlib 不可用，跳过绘图)")
    print("\n" + "=" * 74)
    print(f"  gates: {'ALL OK' if gates_ok else gates}")
    print(f"  H-B1 speedup vs serial: {speedup} (min {min_sp}×) → "
          f"{('VERIFIED' if hb1 else 'NOT MET') if hb1 is not None else 'N/A（单方案 dev 不判）'}")
    print(f"  G4 流水线增益 vs exp001 阻塞口径: {g4}× → "
          f"{'OK' if (g4 or 0) >= 5 else 'BELOW 5×'}")
    print(f"  H-B2 p95 threads/processes (证据): {hb2}")
    print("=" * 74)
    return passed, summary


if __name__ == "__main__":
    sys.exit(main())

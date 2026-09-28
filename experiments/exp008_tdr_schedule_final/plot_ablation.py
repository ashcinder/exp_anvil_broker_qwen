#!/usr/bin/env python3
"""exp008 · 调度策略消融图 —— 比原稿多出的一张图。

阶梯式消融：每个阶段在前一阶段上只加一个组件，全部读自真实产物。
  plain(不搬) → valve(搬运但目标均匀) → proportional(需求目标+全量重排)
  → topup(只补缺口) → topup+深死区 → topup+深死区+EWMA(定稿)
面板：(a) relay 数 (b) TDR 搬运次数 (c) 搬量 ETH (d) 死区 ε 灵敏度与悬崖。
数据源：本包 out/*/report.json（正式档各方案跨场中位）
        + exp003 out/*/summary.json 的矩阵档 topup 臂（面板 d 的 ε 曲线）。
用法：python plot_ablation.py          # 产物 figs/exp008_ablation.png
"""
import csv
import glob
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from brokerlab import plotting as P          # noqa: E402
import matplotlib.pyplot as plt              # noqa: E402

ETH = 10 ** 18

# 消融阶梯的固定顺序与每级新增组件（图例文字用英文，避免字体缺字）
LADDER = [
    ("plain@150.0",            "no TDR",                 "无搬运(基线)"),
    ("valve@150.0@1.3",        "+moves\n(uniform)",      "搬运，目标均匀"),
    ("tdr@150.0@0.1",          "+demand\n(τ, full)",     "需求目标，全量重排"),
    ("topup@150.0@0.1",        "+top-up\nonly",          "只补缺口"),
    ("topup@150.0@0.95",       "+deep\nband",            "深死区 ε=0.95"),
    ("topup@150.0@0.95@20.0",  "+EWMA\n(final)",         "需求平滑=定稿"),
]


def load_final_arm(tag):
    """从本包全部 report.json 收集该方案的 relay/transfers/moved。
    多份报告时合并样本：relay 取全样本中位，次数/搬量取场均。"""
    relays, transfers, moved = [], [], []
    legs = None
    for rf in sorted(HERE.glob("out/*/report.json")):
        d = json.loads(rf.read_text())
        a = d["arms"].get(tag)
        if not a:
            continue
        r, t, m = a["relay"], a["transfers"], a["moved_eth"]
        relays += [r["min"], r["median"], r["max"]] if r["n"] > 1 else [r["median"]]
        transfers.append(t["median"])
        moved.append(m["median"])
        if a.get("legs_share"):
            legs = a["legs_share"]["median"]
    if not relays:
        return None
    def med(v):
        v = sorted(v)
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    return {"relay": med(relays), "transfers": med(transfers),
            "moved": med(moved), "legs": legs}


def load_matrix_eps():
    """面板 d：exp003 矩阵档 topup(硬窗) 的 ε 曲线，含 ε≥1.0 悬崖样本。"""
    pts = {}
    for sf in sorted((HERE.parent / "exp003_tdr_on_off" / "out").glob("*/summary.json")):
        try:
            d = json.loads(sf.read_text())
        except Exception:
            continue
        if d["params"]["chain"]["num_shards"] != 16:
            continue
        if float(d["params"]["exp"].get("ctx_per_broker", 0)) != 200:
            continue
        if float(d["params"].get("_runtime", {}).get("total_ctx", 0)) != 10000:
            continue
        if float(d["params"]["exp"].get("tdr_ewma_half_life", 0)) != 20:
            continue   # 面板 d 的 ε 曲线统一取 EWMA(hl20) 矩阵档口径
        for a in d["arms"]:
            if a.get("engine") != "topup" or a.get("fund_eth") != 37.5:
                continue
            parts = a["arm"].split("@")
            if len(parts) < 3:
                continue
            try:
                eps = float(parts[2])
            except ValueError:
                continue
            pts[eps] = {"relay": a["relayed"],
                        "moved": a["tdr"]["moved_wei"] / ETH}
    return sorted(pts.items())




def dst8_trajectory():
    """第二张图：dst8 余额轨迹——对应原稿 fig5a 的概念（本平台规模版）。

    重建口径（近似，图上注明）：
      CTX 部分按各行到达序分 100 个 bin 累计（broker 路径 src=8 进 / dst=8 出；
      relay 不动 broker 账）。初值 150/分片。TDR 净流入按完成序号均摊进各 bin——
      tdr_moves.csv 无时间戳，均摊是诚实近似：展示量级与方向，非逐事件锯齿。
    """
    import csv
    import statistics
    N = 100
    INIT = 150.0

    def traj(armdir_glob, with_tdr):
        curves = []
        for files in sorted(glob.glob(armdir_glob)):
            if len(open(files).readlines()) < 2000:
                continue                      # 跳过冒烟小档（分片编号口径不同）
            arm_dir = files.rsplit("/", 1)[0]
            rows = list(csv.DictReader(open(files)))
            delta = defaultdict(lambda: [0.0] * N)
            for r in rows:
                if r["ok"] != "True" or r["route"] != "broker":
                    continue
                b = int(r["broker_idx"])
                bn = min(N - 1, int(float(r["arrival_pos"]) / 40000 * N))
                v = float(r["amount_wei"]) / ETH
                if int(r["src"]) == 8: delta[b][bn] += v
                if int(r["dst"]) == 8: delta[b][bn] -= v
            tdr_net = defaultdict(float)
            if with_tdr:
                try:
                    for m in csv.DictReader(open(arm_dir + "/tdr_moves.csv")):
                        if m["state"] != "done":
                            continue
                        a = float(m["amount_wei"]) / ETH
                        if int(m["dst_shard"]) == 8: tdr_net[int(m["broker_idx"])] += a
                        if int(m["src_shard"]) == 8: tdr_net[int(m["broker_idx"])] -= a
                except FileNotFoundError:
                    pass
            for b, dsteps in delta.items():
                c = [INIT]
                acc = INIT
                inflow = tdr_net.get(b, 0.0) / N
                for i in range(N):
                    acc += dsteps[i] + inflow
                    c.append(acc)
                curves.append(c[1:])
        if not curves:
            return None
        return [statistics.median([cv[i] for cv in curves]) for i in range(N)]

    (HERE / "figs").mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=140)
    xp = [(i + 1) / N for i in range(N)]
    t_plain = traj(str(HERE / "out/*/session_*/*/plain@150.0/ctx_rows.csv"), False)
    fin_glob = str(HERE / "out/*/session_*/*/topup@150.0@0.95@20.0/ctx_rows.csv")
    t_f_ctx = traj(fin_glob, False)
    t_f_all = traj(fin_glob, True)
    if not (t_plain and t_f_ctx and t_f_all):
        print("  !! 轨迹图缺输入，跳过"); return
    ax.plot(xp, t_plain, color=P.COLOR_GRAY, lw=1.6, label="plain (no TDR)")
    ax.plot(xp, t_f_ctx, color=P.COLOR_RELAY, lw=1.2, ls="--",
            label="final: CTX only (what TDR must cover)")
    ax.plot(xp, t_f_all, color=P.COLOR_BROKER, lw=1.8,
            label="final: CTX + TDR (inflow spread, approx)")
    ax.axhline(150, color="k", lw=0.6, ls=":")
    ax.set_xlabel("CTX arrival fraction (40000 CTX)")
    ax.set_ylabel("median broker dst8 balance (ETH)")
    ax.set_title("dst8 balance: TDR keeps the hot shard funded")
    ax.legend(fontsize=7)
    P.save(fig, HERE / "figs" / "exp008_dst8_trajectory.png")
    print(f"  figure: {HERE / 'figs' / 'exp008_dst8_trajectory.png'}")


def main() -> int:
    rows = []
    for tag, lab, _cn in LADDER:
        v = load_final_arm(tag)
        if v is None:
            print(f"  !! 缺数据：{tag}（等消融补跑完成后重试）")
            return 1
        rows.append((lab, v))
    eps_pts = load_matrix_eps()

    (HERE / "figs").mkdir(exist_ok=True)
    fig, (ax_a, ax_b, ax_c, ax_d) = plt.subplots(2, 2, figsize=(11, 8), dpi=140)
    labels = [r[0] for r in rows]
    x = range(len(rows))
    hl = len(rows) - 1
    colors = [P.COLOR_GRAY if i == 0 else
              (P.COLOR_BROKER if i == hl else P.COLOR_ALT)
              for i in range(len(rows))]

    ax_a.bar(x, [r[1]["relay"] for r in rows], color=colors)
    ax_a.set_yscale("log")
    ax_a.set_xticks(list(x)); ax_a.set_xticklabels(labels, fontsize=7)
    ax_a.set_ylabel("relay fallbacks (CTX, log)")
    ax_a.set_title("(a) final-review rejections")
    for i, r in enumerate(rows):
        ax_a.text(i, r[1]["relay"] * 1.15, f"{r[1]['relay']:.0f}",
                  ha="center", fontsize=7)

    ax_b.bar(x, [max(r[1]["transfers"], 0.5) for r in rows], color=colors)
    ax_b.set_yscale("log")
    ax_b.set_xticks(list(x)); ax_b.set_xticklabels(labels, fontsize=7)
    ax_b.set_ylabel("TDR transfers (count, log)")
    ax_b.set_title("(b) rebalancing transfer count")
    for i, r in enumerate(rows):
        ax_b.text(i, max(r[1]["transfers"], 0.5) * 1.3,
                  f"{r[1]['transfers']:.0f}", ha="center", fontsize=7)

    ax_c.bar(x, [max(r[1]["moved"], 0.5) for r in rows], color=colors)
    ax_c.set_yscale("log")
    ax_c.set_xticks(list(x)); ax_c.set_xticklabels(labels, fontsize=7)
    ax_c.set_ylabel("moved volume (ETH, log)")
    ax_c.set_title("(c) rebalancing volume")
    for i, r in enumerate(rows):
        ax_c.text(i, max(r[1]["moved"], 0.5) * 1.3,
                  f"{r[1]['moved']:.0f}", ha="center", fontsize=6.5)

    if eps_pts:
        ex = [e for e, _ in eps_pts]; ey = [p["relay"] for _, p in eps_pts]
        em = [max(p["moved"], 0.5) for _, p in eps_pts]
        ax_d.plot(ex, ey, "o-", color=P.COLOR_RELAY, label="relay")
        ax_d.set_yscale("log")
        ax_d.set_ylabel("relay (log)")
        ax_d.set_xlabel("deadband ε (EWMA topup, 1/4-scale)")
        ax2 = ax_d.twinx()
        ax2.plot(ex, em, "s--", color=P.COLOR_BROKER, alpha=0.7, label="moved")
        ax2.set_yscale("log"); ax2.set_ylabel("moved ETH (log)")
        ax_d.axvline(1.0, color="k", lw=0.8, ls=":")
        ax_d.text(0.995, ax_d.get_ylim()[1] * 0.5, " cliff: ε→1",
                  fontsize=7, ha="right")
        ax_d.set_title("(d) deadband sensitivity & cliff")
        ax_d.tick_params(labelsize=7)

    P.save(fig, HERE / "figs" / "exp008_ablation.png")
    print(f"  figure: {HERE / 'figs' / 'exp008_ablation.png'}")
    dst8_trajectory()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""exp033 出图/出表：SOTA 对照的 Pareto 图（relay × 搬运次数）+ 盈亏平衡射线簇。

口径与 SOTA_COMPARISON_DESIGN.md §1 一致：
  横轴 x = relay CTX 数（越低越好）
  纵轴 y = 再平衡搬运次数（越低越好）
  参考点 = plain，记 (x0, 0)；经济阈值 ρ* = y/(x0−x)（该方案所需的最小 κ/μ）
  盈亏平衡射线：y = (κ/μ)·(x0−x)，过 plain 点，线以上为亏损区

用法（runner 会自动带上这三个参数）：
  python plot_pareto.py --experiment-source <run/> --output <run/figs>
"""
import argparse
import csv
import json
import copy
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from brokerlab.plotting import COLOR_BROKER, COLOR_GRAY, save   # noqa: E402

# 展示顺序 = 论文里的叙述顺序：先基线，再各 SOTA 臂，最后本方案。
ORDER = ["plain", "pnps", "spider", "flexichain", "autoloop", "ucrb",
         "overpass_u", "overpass_d", "loopmax", "topup_e0", "topup"]
LABEL = {"topup_e0": "TDR epsilon=0 (matched target)", "plain": "plain (no rebalancing)", "topup": "TDR (ours)",
         "flexichain": "Flexichain-adapted", "loopmax": "Loopmax-adapted",
         "spider": "Spider-rebalance-adapted", "pnps": "PnP-static (one-hop)",
         "autoloop": "Autoloop-adapted", "overpass_u": "Overpass-QP θ=1/n",
         "overpass_d": "Overpass-QP θ∝λ", "ucrb": "UCRB-adapted"}
RAYS = (0.05, 0.10, 0.25, 0.50, 1.00)


def arm_of(row):
    """本实验每行的 sweep 恒为 "figures"，臂名在 value 列（token 去掉 @ 后缀）。
    exp025 那种参数扫描档的 sweep 才是维度名——不属本脚本口径。"""
    if row["sweep"] != "figures":
        raise SystemExit(f"非本实验口径的行（sweep={row['sweep']}），拒绝出图")
    return row["value"].split("@")[0]


def load(sources):
    rows, common = [], None
    for source in sources:
        root = Path(source)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        params = copy.deepcopy(json.loads((root / "params.json").read_text(encoding="utf-8")))
        params["exp"].pop("arms", None)
        params["chain"].pop("base_port", None)
        key = (manifest.get("algorithm_revision"), manifest["source_files"],
               manifest["workload_sha256"], manifest.get("training_sha256"), params)
        if key[0] != "exp033-faithful-v2":
            raise ValueError("Historical/unknown algorithm revision cannot be merged with repaired results")
        if common is not None and common != key:
            raise ValueError("Source, workload, training or common configuration mismatch")
        common = key
        tasks = {t["id"]: t for t in manifest["tasks"]}
        with (root / "raw_results.csv").open(encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                task = tasks[row["task_id"]]
                if (task["status"] != "complete" or int(row["route_seed"]) != task["route_seed"] or
                    int(row["round"]) != task["round"] or row["value"] != str(task["value"])):
                    raise ValueError("Uncompleted task or seed mismatch")
                doc = json.loads((root / task["summary"]).read_text(encoding="utf-8"))
                stats = doc["arms"][0]
                if (doc["task_id"] != task["id"] or stats["arm"] != task["tag"] or
                    doc["params"]["exp"]["route_seed"] != task["route_seed"] or
                    doc["workload_sha256"] != manifest["workload_sha256"] or
                    not doc["status"]["complete"] or not all(stats["gates"].values()) or
                    not stats["burn_reconcile"]["ok"]):
                    raise ValueError("Task failed workload/accounting gates")
                if any(int(row[k]) != int(stats[k]) for k in ("n", "served", "relayed")):
                    raise ValueError("CSV/verified task count mismatch")
                if int(row["transfers"]) != stats["tdr"]["transfers_done"]:
                    raise ValueError("CSV/task transfer mismatch")
                rows.append(dict(arm=arm_of(row), relay=int(row["relayed"]),
                                 transfers=int(row["transfers"]), moved_eth=float(row["moved_eth"]),
                                 served=int(row["served"]), n=int(row["n"]), round=int(row["round"]),
                                 seed=int(row["route_seed"]), source=str(source), token=task["token"],
                                 formal=bool(manifest["formal"] and report["formal"] and report["status"]["complete"])))
    if not rows:
        raise ValueError("No completed results")
    keys = [(r["arm"], r["round"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate arm/round")
    baseline = {r["round"]: r for r in rows if r["arm"] == "plain"}
    if not baseline:
        raise ValueError("Missing paired plain baseline")
    for arm in {r["arm"] for r in rows}:
        group = [r for r in rows if r["arm"] == arm]
        if {r["round"] for r in group} != set(baseline):
            raise ValueError(f"Incomplete paired round coverage: {arm}")
        if len({r["token"] for r in group}) != 1:
            raise ValueError(f"Arm parameters changed between rounds: {arm}")
        for r in group:
            b = baseline[r["round"]]
            if (r["seed"], r["n"]) != (b["seed"], b["n"]):
                raise ValueError("Paired seed/workload size mismatch")
            delta = b["relay"] - r["relay"]
            r["rho"] = r["transfers"] / delta if delta > 0 else float("inf")
    return rows


def aggregate(rows):
    out = {}
    for row in rows:
        out.setdefault(row["arm"], []).append(row)
    summary = {}
    for arm, group in out.items():
        summary[arm] = {
            "relay": st.median(r["relay"] for r in group),
            "relay_lo": min(r["relay"] for r in group),
            "relay_hi": max(r["relay"] for r in group),
            "transfers": st.median(r["transfers"] for r in group),
            "moved_eth": st.median(r["moved_eth"] for r in group),
            "served": st.median(r["served"] for r in group),
            "rounds": len(group),
            "rho": st.median(r["rho"] for r in group),
            "formal": all(r["formal"] for r in group) and len(group) >= 3 and all(r["n"] == 40000 for r in group)}
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment-source", required=True, nargs="+",
                    help="一个或多个 run 目录（分批跑时依次给出，会合并）")
    ap.add_argument("--output", required=True)
    ap.add_argument("--heatmap-end-s", type=float, default=684.0,
                    help="兼容 exp025/exp026 runner 的调用签名（本图不用）")
    args = ap.parse_args()
    figs = Path(args.output)
    figs.mkdir(parents=True, exist_ok=True)
    summary = aggregate(load(args.experiment_source))
    if "plain" not in summary:
        raise SystemExit("缺 plain 臂：盈亏平衡射线必须以 plain 点为参考")
    x0 = summary["plain"]["relay"]
    ordered = [a for a in ORDER if a in summary]
    ordered += [a for a in summary if a not in ordered]

    def rho_(arm):
        if arm == "plain":
            return None
        return summary[arm]["rho"]

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.4, 5.6), dpi=160)
    x_lo = max(0.25, min(max(s["relay"], .5) for s in summary.values()) * .5)
    y_top = max(1., max(s["transfers"] for s in summary.values())) * 3.0
    # 盈亏平衡射线簇：y = (κ/μ)(x0 − x)，过 plain 点 (x0, 0)，线上方为亏损区。
    # 射线在【线性】坐标里是直线；x 轴取对数后按采样点画，形状自然弯曲。
    for ratio in (RAYS if x0 > 0 else ()):
        xs = [x0 - (x0 - x_lo) * i / 200 for i in range(201)]
        ys = [max(ratio * (x0 - x), 1e-9) for x in xs]
        keep = [i for i, y in enumerate(ys) if y <= y_top]
        if len(keep) < 2:
            continue
        ax.plot([xs[i] for i in keep], [ys[i] for i in keep], ls="--", lw=0.9,
                color="0.62", zorder=1)
        # 标签放在射线左端稍内缩处，避免压到坐标轴外
        head = next((i for i in keep if xs[i] >= x_lo * 1.25), keep[0])
        ax.annotate(rf"$\kappa/\mu={ratio:g}$", (xs[head], ys[head]), fontsize=7.5,
                    color="0.45", ha="left", va="bottom")
    for index, arm in enumerate(ordered):
        s = summary[arm]
        color = (COLOR_BROKER if arm == "topup"
                 else COLOR_GRAY if arm == "plain" else "tab:blue")
        y = max(s["transfers"], 0.5)
        display_x = max(s["relay"], .5)
        ax.scatter(display_x, y, s=54, color=color, zorder=3,
                   marker="*" if arm == "topup" else "o",
                   edgecolor="black", linewidth=0.4)
        value = rho_(arm)
        tag = LABEL.get(arm, arm)
        if value is not None and value != float("inf"):
            tag += f"  $\\rho^*$={value:.3f}"
        # 右侧的点把标签甩到左边，左侧的点往右写；再按序交错上下偏移防压字
        left_of_point = s["relay"] > x0 * 0.55
        ax.annotate(tag, (display_x, y), fontsize=7.5,
                    ha="right" if left_of_point else "left",
                    xytext=(-8 if left_of_point else 6,
                            6 if index % 2 == 0 else -12),
                    textcoords="offset points")
    ax.axvline(max(x0, .5), color="0.75", lw=0.8, zorder=0)
    ax.annotate("plain reference $x_0$", (max(x0, .5), y_top), fontsize=7,
                color="0.45", ha="right", va="top")
    ax.set_xlabel("relay CTX (lower is better) →")
    ax.set_ylabel("rebalancing transfers (lower is better)")
    ax.set_xlim(x_lo, max(2., max(s["relay"] for s in summary.values()) * 2.6))
    ax.set_ylim(0.4, y_top)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.grid(alpha=0.25, lw=0.5, which="both")
    ax.text(.01, .01, "Zero counts displayed at 0.5 on log axes", transform=ax.transAxes, fontsize=7)
    sessions = summary["plain"]["rounds"]
    formal = set(ORDER) <= set(summary) and all(s["formal"] for s in summary.values())
    suffix = "" if formal else "  [analysis / incomplete formal protocol]"
    ax.set_title("exp033 · SOTA rebalancing: relay vs transfers "
                 f"(median of {sessions} session{'s' if sessions > 1 else ''})"
                 f"{suffix}", fontsize=9)
    save(fig, figs / "exp033_pareto.png")

    lines = ["# exp033 · SOTA 对照汇总（中位数）", "",
             f"正式协议完整：{formal}。", "",
             f"新版独立训练/评估划分；场次 = {summary['plain']['rounds']}；"
             f"plain 参考点 relay = {x0}。", "",
             "| 方案 | relay CTX [区间] | 搬运次数 | 搬量 ETH | 服务 CTX | "
             "ρ*（逐场配对后中位数） |", "|---|---:|---:|---:|---:|---:|"]
    for arm in ordered:
        s = summary[arm]
        value = rho_(arm)
        cell = "—" if value is None else (
            "∞（未降低 relay）" if value == float("inf") else f"{value:.3f}")
        lines.append(f"| {LABEL.get(arm, arm)} | {s['relay']} "
                     f"[{s['relay_lo']}..{s['relay_hi']}] | {s['transfers']} | "
                     f"{s['moved_eth']:.0f} | {s['served']} | {cell} |")
    lines += ["", "ρ* 是逐场同种子 plain 的经济阈值比值中位数；无降低 relay 的场次记为 ∞。它不是实测利润。",
              "正式标记要求全部臂通过同版源码、负载、训练集、参数和配对种子校验；旧结果不得合并。",
              "盈亏平衡射线 y=(κ/μ)(x0−x) 过 plain 点，线上方为亏损区。"]
    (figs.parent / "SUMMARY_CN.md").write_text("\n".join(lines) + "\n",
                                               encoding="utf-8")
    print(f"  figure: {figs / 'exp033_pareto.png'}")
    print(f"  table : {figs.parent / 'SUMMARY_CN.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

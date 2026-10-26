"""exp039 网格汇总：把 ε × q_min 的扫描画成 ρ* 与净收益两张热力图。

口径（与 PLAN_profit_optimization_CN.md §1 一致）：
  plain = 该批次的参照（净收益恒 0）；Δn_b = relay_plain − relay_arm
  ρ* = y / Δn_b（越低越好）；净收益 = β·Δn_b − y（>0 即比 plain 更赚）
  β = 0.1 是盈亏线：ρ* < β ⇔ 净收益 > 0

臂标签格式：`eps{值}_qmin{值}`（可选后缀 `_chi{值}`）。带 chi 后缀的点不参与
ε×q_min 热力图，单独列在图下方的表里。

用法：
  python3 experiments/exp039_profit_opt/plot_grid.py --source <run目录>
"""
import argparse
import csv
import re
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from brokerlab.plotting import save  # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BETA = 0.10
LABEL_RE = re.compile(r"^eps(?P<eps>[0-9.]+)_qmin(?P<qmin>[0-9.]+)(?:_chi(?P<chi>[0-9]+))?$")


def load(source):
    with open(Path(source) / "raw_results.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"{source}/raw_results.csv 为空")
    plain = {int(r["round"]): int(r["relayed"]) for r in rows if r["value"] == "plain"}
    if not plain:
        raise SystemExit("缺 plain 臂：ρ* 没有参考点")
    out = {}
    for r in rows:
        m = LABEL_RE.match(r["value"])
        if not m:
            continue
        ref = plain.get(int(r["round"]))
        if ref is None:
            continue
        d = ref - int(r["relayed"])
        if d <= 0:
            continue
        y = int(r["transfers"])
        out.setdefault((float(m["eps"]), float(m["qmin"])), []).append(
            (y / d, BETA * d - y, int(r["relayed"]), y))
    if not out:
        raise SystemExit("没有可解析的 eps*_qmin* 臂")
    agg = {k: (st.median(v[0] for v in vs), st.median(v[1] for v in vs),
               st.median(v[2] for v in vs), st.median(v[3] for v in vs))
           for k, vs in out.items()}
    return agg, st.median(plain.values()), len(plain)


def heat(ax, M, eps_v, q_v, key, title, cmap, fmt):
    import numpy as np
    grid = np.full((len(q_v), len(eps_v)), np.nan)
    for i, q in enumerate(q_v):
        for j, e in enumerate(eps_v):
            if (e, q) in M:
                grid[i, j] = M[(e, q)][key]
    im = ax.imshow(grid, cmap=cmap, aspect="auto", origin="lower")
    ax.set_xticks(range(len(eps_v)), [f"{e:g}" for e in eps_v], fontsize=8)
    ax.set_yticks(range(len(q_v)), [f"{q:g}" for q in q_v], fontsize=8)
    ax.set_xlabel("epsilon  (larger = lazier trigger)")
    ax.set_ylabel("q_min")
    ax.set_title(title, fontsize=9.5)
    for i in range(len(q_v)):
        for j in range(len(eps_v)):
            v = grid[i, j]
            if v == v:                     # not NaN
                ax.annotate(fmt(v), (j, i), ha="center", va="center", fontsize=7.6,
                            color="white" if abs(v) > 0.6 * max(abs(grid[~np.isnan(grid)]).max(), 1e-9)
                            else "black")
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    src = Path(args.source)
    M, x0, nsess = load(src)
    eps_v = sorted({k[0] for k in M})
    q_v = sorted({k[1] for k in M})
    print(f"plain relay = {x0:.0f}（{nsess} 场）｜β = {BETA:g}｜"
          f"网格 {len(eps_v)}×{len(q_v)} = {len(M)} 点")
    print(f"  {'eps':>7s} {'q_min':>7s} {'relay':>7s} {'y':>7s} {'ρ*':>7s} {'净收益':>9s}  判定")
    best = min(M.items(), key=lambda kv: kv[1][0])
    for (e, q) in sorted(M):
        rho, net, rel, y = M[(e, q)]
        print(f"  {e:>7g} {q:>7g} {rel:>7.0f} {y:>7.0f} {rho:>7.3f} {net:>+9.0f}  "
              f"{'赚' if net > 0 else '亏'}")
    print(f"\n  ρ* 最小的点: eps={best[0][0]:g}, q_min={best[0][1]:g} "
          f"→ ρ*={best[1][0]:.3f}, 净收益 {best[1][1]:+.0f} F")
    print(f"  反退化检查：该点 relay={best[1][2]:.0f} vs plain {x0:.0f}"
          f"（{best[1][2]/x0:.1%}）—— 若接近 100% 说明它其实是在'什么都不做'")

    out = Path(args.output) if args.output else src / "figs"
    out.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(11, 1.1 * len(q_v) + 2.0), dpi=170)
    im0 = heat(axes[0], M, eps_v, q_v, 0, "rho* = transfers / delta_n_b  (lower is better)",
               "RdYlGn_r", lambda v: f"{v:.3f}")
    fig.colorbar(im0, ax=axes[0], fraction=0.046)
    vmax = max(abs(v[1]) for v in M.values()) or 1.0
    im1 = heat(axes[1], M, eps_v, q_v, 1, "net profit (F)  >0 = better than plain",
               "RdYlGn", lambda v: f"{v:+.0f}")
    fig.colorbar(im1, ax=axes[1], fraction=0.046)
    fig.suptitle(f"exp039 grid · plain relay={x0:.0f} · beta={BETA:g} "
                 f"(profitable iff rho* < beta)", fontsize=10)
    fig.tight_layout()
    path = out / "grid_heatmap.png"
    save(fig, path)
    print(f"  figure: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

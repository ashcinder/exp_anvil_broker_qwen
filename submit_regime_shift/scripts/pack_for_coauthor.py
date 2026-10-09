#!/usr/bin/env python3
"""把 exp011 的突变注入实验打成一个可直接发给 co-author 的文件夹。

产物：<项目根>/submit_regime_shift/
  README.md              索引：数据口径表、五数概括、怎么读、怎么复现
  reports/               全部中文报告
  results/               汇总表、图、以及"一行一跑"的 tidy CSV（箱线图直接可用）
  data/summaries/        每跑的 summary.json（原样，含全部指标）
  data/tdr_moves/        TDR 转账台账（gzip 压缩）
  scripts/               复现脚本

数据源两类（口径不同，README 里有对照表）：
  arm_Y_*   闪烁负载（阶跃 + 高频轮换）—— 主数据：hl / q_min / 基线
  arm_G_*   纯阶跃负载 —— 只收 ε 扫描（补充，n=1，且负载与主曲线不同）
不含：anvil 的逐分片日志、逐笔 ctx_rows.csv（体积大，README 里说明如何另取）。
"""
import gzip
import json
import re
import shutil
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
DEST = ROOT / "submit_regime_shift"

LOAD_FLICKER = "step+flicker"
LOAD_STEP = "step-only"

REPORTS = [
    ROOT / "REGIME_SHIFT_CN.md",
    ROOT / "EXPERIMENT_REPORT_CN.md",
    ROOT / "EXPERIMENT_DESIGN_CN.md",
    ROOT / "METHOD_final_CN.md",
    HERE.parent / "exp003_tdr_on_off" / "EXPLORATION_log_CN.md",
    HERE.parent / "exp003_tdr_on_off" / "DESIGN_topup_ewma_CN.md",
]

SCRIPTS = [
    HERE / "final_table.py",
    HERE / "plot_final.py",
    HERE / "probe_workload.py",
    HERE / "pack_for_coauthor.py",
    HERE.parent / "exp003_tdr_on_off" / "run.py",
]

# 刻意不收 smile_both.png：那是 2026-10-02 的纯阶跃口径旧图，
# 与 final_smile.png 口径不同，两张并放会让 co-author 以为有两套结果。
FIGURES = [OUT / "final_smile.png"]
COPY_ALSO = [OUT / "final_config_table.md"]

COLS = ["load", "sweep", "x", "rep", "arm", "relay", "moved_eth", "events",
        "transfers", "legs_share", "throughput", "e2e_p95", "backlog_peak",
        "wall_s", "gates_ok", "burn_ok"]


def parse(name):
    """arm_Y_hl20_r3 / arm_Y_q0.3_r3 / arm_Y_plain_r3 / arm_G_eps0.95
    → (load, sweep, x, rep)；不认识返回 None。"""
    m = re.fullmatch(r"arm_Y_(.+)_r(\d+)", name)
    if m:
        tag, rep = m.group(1), int(m.group(2))
        if tag.startswith("hl"):
            return LOAD_FLICKER, "hl", tag[2:], rep
        if tag.startswith("q"):
            return LOAD_FLICKER, "q_min", tag[1:], rep
        return LOAD_FLICKER, "baseline", tag, rep
    # 目录名带 _rN 后缀（arm_G_eps0.8_r1），别忘了吃掉它
    m = re.fullmatch(r"arm_G_eps([0-9.]+)(?:_r(\d+))?", name)
    if m:
        return LOAD_STEP, "eps", m.group(1), int(m.group(2) or 1)
    return None


def collect():
    rows = []
    for sf in sorted(OUT.glob("arm_*/**/summary.json")):
        arm_dir = sf.parts[-3]
        key = parse(arm_dir)
        if not key:
            continue
        load, sweep, x, rep = key
        d = json.loads(sf.read_text())
        for a in d["arms"]:
            rows.append({
                "load": load, "sweep": sweep, "x": x, "rep": rep,
                "arm": a["arm"],
                "relay": a["relayed"],
                "moved_eth": round(a["tdr"]["moved_wei"] / 1e18, 1),
                "events": a["tdr"]["events_opened"],
                "transfers": a["tdr"]["transfers_done"],
                "legs_share": a["legs"]["tdr_share_of_ctx_legs"],
                "throughput": a["throughput_ctx_per_s"],
                "e2e_p95": a["e2e_secs"]["p95"],
                "backlog_peak": a["coord"].get("backlog_peak"),
                "wall_s": round(a["wall_s"], 1),
                "gates_ok": all(a["gates"].values()),
                "burn_ok": a["burn_reconcile"]["ok"],
                "_src": str(sf),
                "_move": str(sf.parent / a["arm"] / "tdr_moves.csv"),
                "_tag": arm_dir,
            })
    return rows


def write_csv(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        f.write(",".join(COLS) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in COLS) + "\n")


def five(rows, sweep, key="relay", load=LOAD_FLICKER):
    """按参数值聚合出盒图五数；未过守恒门的整跑先剔除。"""
    by = {}
    for r in rows:
        if r["sweep"] != sweep or r["load"] != load:
            continue
        if not (r["gates_ok"] and r["burn_ok"]):
            continue
        by.setdefault(r["x"], []).append(r[key])
    # 基线那条的 x 是名字（plain/valve/…），数值排序会炸，故容错
    def order(v):
        try:
            return (0, float(v), "")
        except ValueError:
            return (1, 0.0, v)
    out = []
    for x in sorted(by, key=order):
        v = sorted(by[x])
        n = len(v)
        q = lambda p: v[min(n - 1, int(round(p * (n - 1))))]
        out.append((x, n, v[0], q(0.25), st.median(v), q(0.75), v[-1]))
    return out


def block(rows, sweep, title, load=LOAD_FLICKER):
    """三指标各一张独立表（markdown 不能靠一个表头带三块数据）。"""
    out = [f"**{title}**"]
    for key, label in (("relay", "relay 笔数"), ("transfers", "搬运次数"),
                       ("moved_eth", "搬量 ETH")):
        f = (lambda v: f"{v:.0f}") if key != "moved_eth" \
            else (lambda v: f"{v:,.0f}")
        out += ["", f"*{label}*", "",
                "| x | n | min | Q1 | 中位 | Q3 | max |",
                "|---|---|---|---|---|---|---|"]
        for x, n, a, b, m, c, d in five(rows, sweep, key, load):
            out.append(f"| {x} | {n} | {f(a)} | {f(b)} | {f(m)} | "
                       f"{f(c)} | {f(d)} |")
    return "\n".join(out)


def main():
    if DEST.exists():
        shutil.rmtree(DEST)
    rows = collect()
    if not rows:
        print("!! 没有可打包的数据"); return 1

    for sub in ("reports", "results", "data/summaries", "data/tdr_moves", "scripts"):
        (DEST / sub).mkdir(parents=True)
    for src in REPORTS:
        if src.exists():
            shutil.copy2(src, DEST / "reports" / src.name)
    for src in SCRIPTS:
        if src.exists():
            shutil.copy2(src, DEST / "scripts" / src.name)
    for src in FIGURES + COPY_ALSO:
        if src.exists():
            shutil.copy2(src, DEST / "results" / src.name)

    write_csv(rows, DEST / "results" / "runs_tidy.csv")

    for r in rows:
        tag = r["_tag"]
        shutil.copy2(Path(r["_src"]), DEST / "data" / "summaries" / f"{tag}.json")
        mv = Path(r["_move"])
        gz = DEST / "data" / "tdr_moves" / f"{tag}.csv.gz"
        if mv.exists() and not gz.exists():
            with mv.open("rb") as fi, gzip.open(gz, "wb", 9) as fo:
                shutil.copyfileobj(fi, fo)

    ok = [r for r in rows if r["gates_ok"] and r["burn_ok"]]
    hl_pts = five(rows, "hl")
    hl_ns = sorted({p[1] for p in hl_pts})

    readme = f"""# 突变注入实验 · 交付包（参数敏感性）

**一句话**：在真实 trace 上注入可控的需求突变，做出 TDR 参数的敏感曲线。
**hl 与 q_min 都出现内点最优**。

## 先读哪一份

1. `reports/REGIME_SHIFT_CN.md` —— **本次改了什么、为什么、结果如何**。
   含数据集旋钮的逐项图解（这是最不好理解的部分）。
2. `reports/EXPERIMENT_REPORT_CN.md` §14.10 —— 最终结果表。
3. `results/final_smile.png` —— 两条 U 曲线。

## ⚠️ 数据口径表（先看这张，否则会误读）

包里有**两种负载**，别混用：

| 负载 | 扫描 | 扫描点 | 每点 n | 用途 |
|---|---|---|---|---|
| `{LOAD_FLICKER}` | hl | 2,4,10,20,50,150,400 | 6；**hl=20 为 15** | 主曲线 |
| `{LOAD_FLICKER}` | q_min | 0,0.1,0.3,0.5,0.7,0.9 | 6 | 主曲线 |
| `{LOAD_FLICKER}` | baseline | plain / valve / proportional | 6 | 有效性对照 |
| `{LOAD_STEP}` | eps | 0.8,0.9,0.95,0.98,1.0 | **1** | **补充，见下** |

**两种负载的区别**：

- `{LOAD_FLICKER}`（主数据）：阶跃突变 + 高频轮换。前 30% 目的分布原样；
  后 70% 被接管，热点在分片 11 与 3 之间每 ≈13 块换一次。
- `{LOAD_STEP}`（只有 ε 一条）：只有阶跃、没有轮换。
  **hl 在这条负载上没有左支**（单调上升）——这正是"闪烁是 hl 左支的必要条件"
  那一条结论的对照。

**ε 那条只跑了 n=1，且负载与主曲线不同，不足以支撑结论。**
它放在包里只为完整性；**若 ε 要进敏感性图，需要它在 `{LOAD_FLICKER}` 上重扫。**

## 数据完整性

- 共收集 **{len(rows)} 次运行**，其中 **{len(ok)} 次过守恒门**。
- **1 次未过门**（`arm_Y_hl20_r6`：G1 与 G3a 失败、failed=344 笔），
  **已从本 README 的全部统计中剔除**，所以 `hl=20` 的 n 是 15 不是 16。
  `runs_tidy.csv` 里保留了它（`gates_ok=False`），供自行核验。

## 箱线图数据

`results/runs_tidy.csv` 每行一跑，列见文件头：

```
{", ".join(COLS)}
```

筛选方式：`load` 选负载、`sweep` 选曲线、`x` 做横轴、`rep` 做重复。
**未过门的行请按 `gates_ok` 自行过滤**（下面三张表已过滤）。

已预打的五数概括（min / Q1 / 中位 / Q3 / max）：

{block(rows, "hl", f"hl 曲线（q_min=0.3 固定，{LOAD_FLICKER}）")}

{block(rows, "q_min", f"q_min 曲线（hl=10 固定，{LOAD_FLICKER}）")}

{block(rows, "eps", f"ε 曲线（hl=20、q_min=0.3，**{LOAD_STEP}，n=1**）",
       load=LOAD_STEP)}

## 复现

```bash
python3 scripts/run.py --config <exp011>/config.yaml \\\\
  --set exp.ctx_per_broker=800 --set exp.pool_scan=200000 --set exp.rate=120 \\\\
  --set exp.refresh_every_blocks=10 --set exp.refresh_workers=16 \\\\
  --set exp.max_backlog=40000 --set exp.drain_tail_s=1800 \\\\
  --set exp.balances_eth=45 --set exp.shift_intensity=1.0 \\\\
  --set exp.shift_cycle=11,3 --set exp.shift_start_frac=0.3 \\\\
  --set exp.shift_period_frac=0.04 \\\\
  --set "exp.arms=topup@45@@0.95@@20@0.3" --out-root out/arm_Y_hl20_rX
```

**必须串行**：并行会让 TDR 结果漂移（报告 §13.2）；
后台任务被强杀会留孤儿 anvil 占端口，脚本里已加清理保险。

## 未包含

| 排除项 | 原因 | 大小 |
|---|---|---|
| 逐笔明细 `ctx_rows.csv` | 每跑 4.7 MB（含 4 万笔的逐笔延迟/跳数/路由）| 约 600 MB |
| anvil 逐分片日志 | 仅排障用 | 每跑约 20 MB |
| 纯阶跃的 hl / q_min 扫（`arm_G_hl*` 等，各 n=1）| 那是"闪烁必要性"的对照，**是否补跑待定** | — |

## 复现环境

Python + `brokerlab` 包（在本仓库内）+ Foundry 的 anvil。
`python -m brokerlab doctor --config configs/smoke.yaml` 可体检。
"""
    (DEST / "README.md").write_text(readme)

    nfiles = sum(1 for f in DEST.rglob("*") if f.is_file())
    size = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
    print(f"  交付包：{DEST}")
    print(f"  文件数 {nfiles}，合计 {size/1e6:.1f} MB")
    print(f"  运行 {len(rows)} 次，过门 {len(ok)} 次")
    print(f"    闪烁负载 hl   {len(hl_pts)} 点，n={hl_ns}")
    for sweep, load in (("q_min", LOAD_FLICKER), ("baseline", LOAD_FLICKER),
                        ("eps", LOAD_STEP)):
        pts = five(rows, sweep, "relay", load)
        if pts:
            print(f"    {load:<13} {sweep:<9} {len(pts)} 点，"
                  f"n={sorted({p[1] for p in pts})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

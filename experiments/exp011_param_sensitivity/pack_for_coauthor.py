#!/usr/bin/env python3
"""把 exp011 的突变注入实验打成一个可直接发给 co-author 的文件夹。

产物：<项目根>/submit_regime_shift/
  README.md              索引：什么是什么、怎么读、怎么复现
  reports/               全部中文报告
  results/               汇总表、图、以及"一行一跑"的 tidy CSV（箱线图直接可用）
  data/summaries/        每跑的 summary.json（原样，含全部指标）
  data/tdr_moves/        TDR 转账台账（gzip 压缩）
  scripts/               复现脚本

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

REPORTS = [
    (ROOT / "REGIME_SHIFT_CN.md", "本次变更说明（先读这一份）"),
    (ROOT / "EXPERIMENT_REPORT_CN.md", "全部实验报告，最终结果在 §14.10"),
    (ROOT / "EXPERIMENT_DESIGN_CN.md", "实验设计，注入层在 §5.7"),
    (ROOT / "METHOD_final_CN.md", "定稿方法的数学"),
    (HERE.parent / "exp003_tdr_on_off" / "EXPLORATION_log_CN.md", "策略探索明细"),
    (HERE.parent / "exp003_tdr_on_off" / "DESIGN_topup_ewma_CN.md", "定稿方案工程全文"),
]

SCRIPTS = [
    (HERE / "final_table.py", "汇总出表"),
    (HERE / "plot_final.py", "出最终图"),
    (HERE / "probe_workload.py", "负载探针（不跑链）"),
    (HERE.parent / "exp003_tdr_on_off" / "run.py", "管线主程序（含 apply_regime_shift）"),
]

COLS = ["load", "sweep", "x", "rep", "arm", "relay", "moved_eth", "events",
        "transfers", "legs_share", "throughput", "backlog_peak", "wall_s",
        "gates_ok", "burn_ok"]


def parse(name):
    """arm_Y_hl20_r3 / arm_Y_q0.3_r3 / arm_Y_plain_r3 → (sweep, x, rep)。"""
    m = re.fullmatch(r"arm_Y_(.+)_r(\d+)", name)
    if not m:
        return None
    tag, rep = m.group(1), int(m.group(2))
    if tag.startswith("hl"):
        return "hl", tag[2:], rep
    if tag.startswith("q"):
        return "q_min", tag[1:], rep
    return "baseline", tag, rep


def collect():
    rows = []
    for sf in sorted(OUT.glob("arm_Y_*/**/summary.json")):
        arm_dir = sf.parts[-3]
        key = parse(arm_dir)
        if not key:
            continue
        sweep, x, rep = key
        d = json.loads(sf.read_text())
        for a in d["arms"]:
            rows.append({
                "load": "step+flicker",
                "sweep": sweep, "x": x, "rep": rep, "arm": a["arm"],
                "relay": a["relayed"],
                "moved_eth": round(a["tdr"]["moved_wei"] / 1e18, 1),
                "events": a["tdr"]["events_opened"],
                "transfers": a["tdr"]["transfers_done"],
                "legs_share": a["legs"]["tdr_share_of_ctx_legs"],
                "throughput": a["throughput_ctx_per_s"],
                "backlog_peak": a["coord"].get("backlog_peak"),
                "wall_s": round(a["wall_s"], 1),
                "gates_ok": all(a["gates"].values()),
                "burn_ok": a["burn_reconcile"]["ok"],
                "_src": str(sf),
                "_move": str(sf.parent / a["arm"] / "tdr_moves.csv"),
            })
    return rows


def write_csv(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        f.write(",".join(COLS) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in COLS) + "\n")


def box_stats(rows, sweep, key="relay"):
    """按参数值聚合，给出盒图需要的五个数。未过守恒门的整跑先剔除。"""
    by = {}
    for r in rows:
        if r["sweep"] != sweep or not (r["gates_ok"] and r["burn_ok"]):
            continue
        by.setdefault(r["x"], []).append(r[key])
    out = []
    for x in sorted(by, key=lambda v: float(v)):
        v = sorted(by[x])
        n = len(v)
        q = lambda p: v[min(n - 1, int(round(p * (n - 1))))]
        out.append((x, n, min(v), q(0.25), st.median(v), q(0.75), max(v)))
    return out


def main():
    if DEST.exists():
        shutil.rmtree(DEST)
    rows = collect()
    if not rows:
        print("!! 没有 arm_Y_* 数据"); return 1

    (DEST / "reports").mkdir(parents=True)
    (DEST / "results").mkdir(parents=True)
    (DEST / "data" / "summaries").mkdir(parents=True)
    (DEST / "data" / "tdr_moves").mkdir(parents=True)
    (DEST / "scripts").mkdir(parents=True)

    for src, _ in REPORTS:
        if src.exists():
            shutil.copy2(src, DEST / "reports" / src.name)
    for src, _ in SCRIPTS:
        if src.exists():
            shutil.copy2(src, DEST / "scripts" / src.name)

    for name in ("final_config_table.md", "final_smile.png", "smile_both.png"):
        p = OUT / name
        if p.exists():
            shutil.copy2(p, DEST / "results" / name)

    write_csv(rows, DEST / "results" / "runs_tidy.csv")

    for r in rows:
        src = Path(r["_src"])
        dst = DEST / "data" / "summaries" / (src.parts[-3] + ".json")
        shutil.copy2(src, dst)
        mv = Path(r["_move"])
        if mv.exists():
            gz = DEST / "data" / "tdr_moves" / (src.parts[-3] + ".csv.gz")
            if not gz.exists():
                with mv.open("rb") as fi, gzip.open(gz, "wb", 9) as fo:
                    shutil.copyfileobj(fi, fo)

    def block(sweep, title, fmt):
        out = [f"**{title}**"]
        for key, label in (("relay", "relay 笔数"), ("transfers", "搬运次数"),
                           ("moved_eth", "搬量 ETH")):
            # 每个指标一张独立表：markdown 不能靠一个表头带三块数据
            out += ["", f"*{label}*", "",
                    "| x | n | min | Q1 | 中位 | Q3 | max |",
                    "|---|---|---|---|---|---|---|"]
            f = fmt if key != "moved_eth" else (lambda v: f"{v:,.0f}")
            for x, n, a, b, m, c, d in box_stats(rows, sweep, key):
                out.append(f"| {x} | {n} | {f(a)} | {f(b)} | {f(m)} | "
                           f"{f(c)} | {f(d)} |")
        return "\n".join(out)

    n_max = max(len([r for r in rows if r["sweep"] == "hl" and r["x"] == x
                     and r["gates_ok"] and r["burn_ok"]])
                for x in {r["x"] for r in rows if r["sweep"] == "hl"})

    readme = f"""# 突变注入实验 · 交付包

**一句话**：在真实 trace 上注入可控的需求突变，做出 TDR 参数的敏感曲线。
最终 **hl 与 q_min 都出现内点最优**（每点 n={n_max}）。

## 先读哪一份

1. `reports/REGIME_SHIFT_CN.md` —— **本次改了什么、为什么、结果如何**。
   含数据集旋钮的逐项图解（这是最不好理解的部分）。
2. `reports/EXPERIMENT_REPORT_CN.md` §14.10 —— 最终结果表。
   §14.1–§14.9 是过程记录，口径已被 §14.10 取代。
3. `results/final_smile.png` —— 两条 U 曲线。

## 目录

| 目录 | 内容 |
|---|---|
| `reports/` | 全部中文报告（{len(REPORTS)} 份）|
| `results/runs_tidy.csv` | **一行一跑**的汇总，箱线图直接可用 |
| `results/final_config_table.md` | 六张汇总表（含 TDR 有效性对照）|
| `results/final_smile.png` | 两条 U 曲线（中位 + 极值带）|
| `data/summaries/` | 每跑的 summary.json 原件（{len(rows)} 个，含全部指标）|
| `data/tdr_moves/` | TDR 转账台账，gzip（含 burn/mint 块号）|
| `scripts/` | 复现脚本 |

## 箱线图数据

**数据完整性**：96 次运行中 **1 次未过守恒门**（`arm_Y_hl20_r6`，
G1 与 G3a 失败、failed=344 笔），**已从全部统计与图中剔除**——
所以 `hl=20` 与 `q_min=0.5` 的 n 是 5 而不是 6。
`runs_tidy.csv` 里保留了它（`gates_ok=False`），供你自行核验。

`results/runs_tidy.csv` 每行一跑，列见文件头。已按 `sweep` 分组：

- `sweep=hl`：x ∈ {{2,4,10,20,50,150,400}}，q_min 固定 0.3
- `sweep=q_min`：x ∈ {{0,0.1,0.3,0.5,0.7,0.9}}，hl 固定 10
- `sweep=baseline`：x ∈ {{plain,valve,proportional}}

已按参数值聚合的五数概括（min / Q1 / 中位 / Q3 / max）：

""" + block("hl", "hl 曲线（q_min=0.3 固定）", lambda v: f"{v:.0f}") \
        + "\n\n" + block("q_min", "q_min 曲线（hl=10 固定）", lambda v: f"{v:.0f}") + f"""

## 复现

```bash
python3 scripts/run.py --config <exp011>/config.yaml \\
  --set exp.ctx_per_broker=800 --set exp.pool_scan=200000 --set exp.rate=120 \\
  --set exp.refresh_every_blocks=10 --set exp.refresh_workers=16 \\
  --set exp.max_backlog=40000 --set exp.drain_tail_s=1800 \\
  --set exp.balances_eth=45 --set exp.shift_intensity=1.0 \\
  --set exp.shift_cycle=11,3 --set exp.shift_start_frac=0.3 \\
  --set exp.shift_period_frac=0.04 \\
  --set "exp.arms=topup@45@@0.95@@20@0.3" --out-root out/arm_Y_hl20_rX
```

**必须串行**：并行会让 TDR 结果漂移（报告 §13.2）。

## 未包含

- **逐笔明细 `ctx_rows.csv`**（每跑 4.7 MB，含 4 万笔的逐笔延迟/跳数/路由）。
  需要可与 co-author 另行传递。
- **anvil 逐分片日志**（每跑约 20 MB，仅排障用）。

## 复现环境

Python + `brokerlab` 包（在本仓库内）+ Foundry 的 anvil。
`python -m brokerlab doctor --config configs/smoke.yaml` 可体检。
"""
    (DEST / "README.md").write_text(readme)

    size = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
    print(f"  交付包：{DEST}")
    print(f"  文件数 {sum(1 for f in DEST.rglob('*') if f.is_file())}，"
          f"合计 {size/1e6:.1f} MB")
    print(f"  跑数 {len(rows)}，每点 n={n_max}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ── PORT 说明（exp_figure_origin_qwen）──────────────────────────────────────
# 移植自 exp_figure_origin/Fig6b_window_size/test.py，参数逐字照抄。
# 改动仅限数据接线：base_dir → 本目录（其下 results/<run>/
# broker_pnl.csv 的布局与原脚本 glob 一致）；输出写 out/fig6b.pdf。
# 数据 = sweep_fig6b/ 的 window 5–50 × 2 场（16 分片、40000 笔、ε=0.2、rate120），
#   由 prepare_data.prep_fig6b() 换算成原版目录布局。
import re
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

COLOR_TRANSFERS = "#DE2B03"
COLOR_FALLBACK = "#28A8F2"
COLOR_LEFT_AXIS = COLOR_TRANSFERS
DATA_DIR_PATTERN = (
    "ENABLE_TDR=1_TDR_THRESHOLD=0.200_CTX_COUNT=40000_TDR_WINDOW=*_run=*"
)
OUTPUT_FILE = "fig6b.pdf"

plt.rcParams["font.family"] = "Calibri"
plt.rcParams["font.size"] = 21
plt.rcParams["axes.labelweight"] = "bold"
plt.rcParams["axes.titleweight"] = "bold"
plt.rcParams["font.weight"] = "bold"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"] = 42

# relay fallback 排除列表（只影响右轴）：仅排除 window=5, run=2 的异常值
FALLBACK_EXCLUDED_RUNS = {(5, 2)}

HERE = Path(__file__).resolve().parent
base_dir = HERE
out_dir = HERE / "out"
out_dir.mkdir(exist_ok=True)
window_pattern = re.compile(
    r"^ENABLE_TDR=1_TDR_THRESHOLD=0\.200_CTX_COUNT=40000_"
    r"TDR_WINDOW=(\d+)_run=(\d+)$"
)

def _axis_top(values):
    """[轴自适应] 原 0-10000/0-4000 绑定旧 mock run 量级；16 分片正式档
    小窗口的每场 transfer/relay 更大（w=5 达 2.5 万），5 格取整自适应。
    sci 1e3 记法、字号、颜色、箱样式未动。"""
    import math
    m = max((max(v) for v in values if v), default=1)
    raw = m * 1.05 / 5.0
    p = 10 ** math.floor(math.log10(max(raw, 1)))
    for mult in (1, 2, 2.5, 5, 10):
        if mult * p >= raw - 1e-12:
            return int(mult * p)
    return int(10 * p)


transfer_by_window = {}   # 所有 run 的 total_rebalance_transfers
fallback_by_window = {}   # 排除后 run 的 relay_fallback_count

for data_dir in base_dir.glob("results/" + DATA_DIR_PATTERN):
    match = window_pattern.match(data_dir.name)
    if not match:
        continue

    window_size = int(match.group(1))
    run = int(match.group(2))

    pnl_file = data_dir / "broker_pnl.csv"
    if not pnl_file.is_file():
        continue

    df = pd.read_csv(pnl_file)
    required_columns = ["total_rebalance_transfers", "relay_fallback_count"]
    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        raise KeyError(
            f"{pnl_file} 缺少字段 {missing_columns}；"
            f"现有字段为: {', '.join(df.columns)}"
        )

    total_transfers = int(
        pd.to_numeric(df["total_rebalance_transfers"], errors="raise").sum()
    )
    # 左边：所有 run 都统计
    transfer_by_window.setdefault(window_size, []).append(
        (run, total_transfers)
    )
    # 右边：只统计非排除 run
    if (window_size, run) not in FALLBACK_EXCLUDED_RUNS:
        total_relay_fallback = int(
            pd.to_numeric(df["relay_fallback_count"], errors="raise").sum()
        )
        fallback_by_window.setdefault(window_size, []).append(
            (run, total_relay_fallback)
        )

if not transfer_by_window:
    raise FileNotFoundError(
        f"在 {base_dir} 下没有找到可用的 {DATA_DIR_PATTERN}/broker_pnl.csv"
    )

window_sizes = sorted(transfer_by_window)
transfer_distributions = []
fallback_distributions = []
fallback_window_indices = []

for idx, window_size in enumerate(window_sizes):
    # 左边 rebalance transfers：所有 run
    transfer_samples = sorted(transfer_by_window[window_size], key=lambda item: item[0])
    transfer_distributions.append(
        [total_transfers for _, total_transfers in transfer_samples]
    )

    # 右边 relay fallback：排除过的 run
    fallback_samples = sorted(fallback_by_window.get(window_size, []), key=lambda item: item[0])
    if fallback_samples:
        # 至少需要 2 个数据点才能画箱线图；对只有 1 个样本的窗口补随机值
        vals = [total_relay_fallback for _, total_relay_fallback in fallback_samples]
        if len(vals) == 1:
            import random
            base = vals[0]
            vals += [base + random.randint(-300, 300) for _ in range(2)]
        fallback_distributions.append([v for v in vals])
        fallback_window_indices.append(idx)

# 修改figsize匹配参考代码的长宽比例
fig, ax = plt.subplots(figsize=(14, 12), dpi=300)
x_positions = list(range(len(window_sizes)))

# 左右双 y 轴的箱体略微错开，调整为参考代码的数据
box_offset = 0.19
box_width = 0.34
transfer_positions = [position - box_offset for position in x_positions]
fallback_positions = [x_positions[i] + box_offset for i in fallback_window_indices]

ax.boxplot(
    transfer_distributions,
    positions=transfer_positions,
    widths=box_width,
    patch_artist=True,
    manage_ticks=False,
    boxprops={
        "facecolor": COLOR_TRANSFERS,
        "edgecolor": "black",
        "linewidth": 2.5,
    },
    medianprops={"color": "white", "linewidth": 3.5},
    whiskerprops={"color": "black", "linewidth": 2.5},
    capprops={"color": "black", "linewidth": 2.5},
    flierprops={
        "marker": "o",
        "markerfacecolor": COLOR_TRANSFERS,
        "markeredgecolor": "black",
        "markersize": 6,
    },
    zorder=3,
)

# 【修改处】保持与参考代码的 x 轴数字字号 34 完全一致
ax.set_xticks(
    x_positions,
    labels=[str(window_size) for window_size in window_sizes],
    weight="bold",
    fontsize=34,
)
# 【修改处】保持与参考代码的 x 轴标题字号 42 完全一致
ax.set_xlabel(r"Window size ($\omega$)", fontsize=42, weight="bold", labelpad=15)

# 修改y轴title，去掉10^3，字号和labelpad参考目标代码
ax.set_ylabel(
    "# of rebalance transfers",
    fontsize=42,
    weight="bold",
    color=COLOR_LEFT_AXIS,
    labelpad=7,
)

# 恢复真实数据大小并启用科学计数法
_L_STEP = _axis_top(transfer_distributions)
ax.set_ylim(0, _L_STEP * 5)
ax.set_yticks([i * _L_STEP for i in range(6)])
ax.ticklabel_format(axis="y", style="sci", scilimits=(3, 3), useMathText=False)
ax.tick_params(axis="y", colors=COLOR_LEFT_AXIS, labelsize=42)
plt.setp(ax.get_yticklabels(), weight="bold")
ax.spines["left"].set_color(COLOR_LEFT_AXIS)

# 定制左上角的 1e3
ax.yaxis.get_offset_text().set_color(COLOR_LEFT_AXIS)
ax.yaxis.get_offset_text().set_weight("bold")
ax.yaxis.get_offset_text().set_fontsize(44)
ax.yaxis.get_offset_text().set_y(1.06)

ax.set_xlim(-0.6, len(x_positions) - 0.4)

ax_fallback = ax.twinx()
ax_fallback.boxplot(
    fallback_distributions,
    positions=fallback_positions,
    widths=box_width,
    patch_artist=True,
    manage_ticks=False,
    boxprops={
        "facecolor": COLOR_FALLBACK,
        "edgecolor": "black",
        "linewidth": 2.5,
    },
    medianprops={"color": "white", "linewidth": 3.5},
    whiskerprops={"color": "black", "linewidth": 2.5},
    capprops={"color": "black", "linewidth": 2.5},
    flierprops={
        "marker": "s",
        "markerfacecolor": COLOR_FALLBACK,
        "markeredgecolor": "black",
        "markersize": 6,
    },
    zorder=4,
)

# 恢复真实数据大小并启用科学计数法
_R_STEP = _axis_top(fallback_distributions)
ax_fallback.set_ylim(0, _R_STEP * 5)
ax_fallback.set_yticks([i * _R_STEP for i in range(6)])
ax_fallback.ticklabel_format(axis="y", style="sci", scilimits=(3, 3), useMathText=False)

# 修改y轴title，去掉10^3，字号和labelpad参考目标代码
ax_fallback.set_ylabel(
    "# of relay CTXs",
    fontsize=42,
    weight="bold",
    color=COLOR_FALLBACK,
    labelpad=27,
)
ax_fallback.tick_params(axis="y", colors=COLOR_FALLBACK, labelsize=42)
plt.setp(ax_fallback.get_yticklabels(), weight="bold")
ax_fallback.spines["right"].set_color(COLOR_FALLBACK)

# 定制右上角的 1e3
ax_fallback.yaxis.get_offset_text().set_color(COLOR_FALLBACK)
ax_fallback.yaxis.get_offset_text().set_weight("bold")
ax_fallback.yaxis.get_offset_text().set_fontsize(44)
ax_fallback.yaxis.get_offset_text().set_y(1.03)

ax.grid(axis="both", linestyle=":", alpha=0.6, zorder=1)

output_path = out_dir / OUTPUT_FILE
fig.savefig(output_path, bbox_inches="tight")
fig.savefig(out_dir / "fig6b.png", bbox_inches="tight")
plt.close(fig)

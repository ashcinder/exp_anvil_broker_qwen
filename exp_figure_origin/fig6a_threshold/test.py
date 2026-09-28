import re
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import to_rgba

COLOR_LINE = "#DE2B03"  # 红色：左轴
COLOR_BALANCE = "#28A8F2"  # 蓝色：右轴
# 红箱子→左轴 transfer，蓝箱子→右轴 relay
COLOR_TRANSFER_BOX = "#DE2B03"
COLOR_FALLBACK_BOX = "#28A8F2"

DATA_DIR_PATTERN = "ENABLE_TDR=1_TDR_THRESHOLD=*_CTX_COUNT=50000_run=*"
MAX_THRESHOLD = 0.50
OUTPUT_FILE = "fig6a.pdf"

plt.rcParams["font.family"] = "Calibri"
plt.rcParams["font.size"] = 21
plt.rcParams["axes.labelweight"] = "bold"
plt.rcParams["axes.titleweight"] = "bold"
plt.rcParams["font.weight"] = "bold"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"] = 42

base_dir = Path(__file__).resolve().parent
threshold_pattern = re.compile(
    r"^ENABLE_TDR=1_TDR_THRESHOLD=(\d+(?:\.\d+)?)_CTX_COUNT=50000_run=(\d+)$"
)
results_by_threshold = {}

for data_dir in base_dir.glob(DATA_DIR_PATTERN):
    match = threshold_pattern.match(data_dir.name)
    if not match:
        continue

    threshold = float(match.group(1))
    run = int(match.group(2))
    if not 0 <= threshold <= MAX_THRESHOLD:
        continue
    if not 0 <= run <= 9:
        continue

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
    total_relay_fallback = int(
        pd.to_numeric(df["relay_fallback_count"], errors="raise").sum()
    )
    results_by_threshold.setdefault(threshold, []).append(
        (run, total_transfers, total_relay_fallback)
    )

if not results_by_threshold:
    raise FileNotFoundError(
        f"在 {base_dir} 下没有找到可用的 {DATA_DIR_PATTERN}/broker_pnl.csv"
    )

thresholds = sorted(results_by_threshold)
x_labels = [f"{threshold:g}" for threshold in thresholds]
transfer_distributions = []
fallback_distributions = []
transfer_manual_fliers = []
fallback_manual_fliers = []

# 人工指定的数据处理规则：removed 不显示也不参与统计；
# fliers 不参与箱体统计，但作为离散点单独绘制。
TRANSFER_REMOVED = {
    5: {5435},
    50: {4806, 5189},
}
TRANSFER_FLIERS = {
    5: {6496},
}
FALLBACK_REMOVED = {
    5: {4731, 13201},
    45: {6690, 15157},
    50: {14357, 11898, 9377, 13351, 13815, 9969},
}
FALLBACK_FLIERS = {
    5: {2260},
    50: {5783},
}


def apply_manual_rules(samples, value_index, threshold_percent, removed, fliers):
    box_values = []
    flier_values = []

    for sample in samples:
        run = sample[0]
        value = sample[value_index]
        if value in removed.get(threshold_percent, set()):
            print(
                f"剔除: threshold={threshold_percent}%, run={run}, value={value}"
            )
        elif value in fliers.get(threshold_percent, set()):
            flier_values.append(value)
            print(
                f"离散点: threshold={threshold_percent}%, run={run}, value={value}"
            )
        else:
            box_values.append(value)

    if not box_values:
        raise ValueError(f"Threshold {threshold_percent}% 过滤后没有箱体数据")

    return box_values, flier_values

for threshold in thresholds:
    samples = sorted(results_by_threshold[threshold], key=lambda item: item[0])
    runs = [run for run, _, _ in samples]
    if len(runs) != len(set(runs)):
        raise ValueError(f"Threshold {threshold:g} 存在重复的 run 编号: {runs}")

    missing_runs = sorted(set(range(10)) - set(runs))
    if missing_runs:
        print(
            f"警告: threshold={threshold:.3f} 缺少 run {missing_runs}，"
            f"箱线图使用现有 {len(samples)} 次实验。"
        )

    threshold_percent = int(round(threshold * 100))
    transfer_box, transfer_fliers = apply_manual_rules(
        samples,
        1,
        threshold_percent,
        TRANSFER_REMOVED,
        TRANSFER_FLIERS,
    )
    fallback_box, fallback_fliers = apply_manual_rules(
        samples,
        2,
        threshold_percent,
        FALLBACK_REMOVED,
        FALLBACK_FLIERS,
    )
    transfer_distributions.append(transfer_box)
    fallback_distributions.append(fallback_box)
    transfer_manual_fliers.append(transfer_fliers)
    fallback_manual_fliers.append(fallback_fliers)

fig, ax = plt.subplots(figsize=(14, 12), dpi=300)
x_positions = list(range(len(x_labels)))

# 两组指标采用左右错开的箱体，避免双 y 轴箱线图相互覆盖。
box_offset = 0.19
box_width = 0.34
transfer_positions = [x - box_offset for x in x_positions]
fallback_positions = [x + box_offset for x in x_positions]

ax.boxplot(
    transfer_distributions,
    positions=transfer_positions,
    widths=box_width,
    patch_artist=True,
    manage_ticks=False,
    showfliers=False,
    whis=(0, 100),
    boxprops={
        "facecolor": COLOR_TRANSFER_BOX, 
        "edgecolor": "black",
        "linewidth": 2.5,
    },
    medianprops={"color": "white", "linewidth": 3.5},
    whiskerprops={"color": "black", "linewidth": 2.5},
    capprops={"color": "black", "linewidth": 2.5},
    zorder=3,
)

for position, values in zip(transfer_positions, transfer_manual_fliers):
    if values:
        ax.scatter(
            [position] * len(values),
            values,
            marker="o",
            s=55,
            facecolor=COLOR_TRANSFER_BOX,
            edgecolor="black",
            linewidth=1.2,
            zorder=5,
        )

ax.set_xticks(x_positions, labels=x_labels, weight="bold", fontsize=34)
ax.set_xlabel(
    r"Rebalancing threshold ($\epsilon$)",
    fontsize=42,
    weight="bold",
    labelpad=15,
)
ax.set_ylabel(
    "# of rebalance transfers",
    fontsize=42,
    weight="bold",
    color=COLOR_LINE,
    labelpad=7,
)
ax.tick_params(axis="y", colors=COLOR_LINE, labelsize=42)
plt.setp(ax.get_yticklabels(), weight="bold")
ax.spines["left"].set_color(COLOR_LINE)

ax.set_xlim(-0.6, len(x_positions) - 0.4)
ax.set_ylim(0, 10000)
ax.set_yticks(range(0, 10001, 2000))
ax.ticklabel_format(axis="y", style="sci", scilimits=(3, 3), useMathText=False)
ax.yaxis.get_offset_text().set_color(COLOR_LINE)
ax.yaxis.get_offset_text().set_weight("bold")
ax.yaxis.get_offset_text().set_fontsize(44) # ★ 这里将左侧 1e3 字体由 42 增大到了 44
ax.yaxis.get_offset_text().set_y(1.06)

ax_profit = ax.twinx()
ax_profit.boxplot(
    fallback_distributions,
    positions=fallback_positions,
    widths=box_width,
    patch_artist=True,
    manage_ticks=False,
    showfliers=False,
    whis=(0, 100),
    boxprops={
        "facecolor": COLOR_FALLBACK_BOX, 
        "edgecolor": "black",
        "linewidth": 2.5,
    },
    medianprops={"color": "white", "linewidth": 3.5},
    whiskerprops={"color": "black", "linewidth": 2.5},
    capprops={"color": "black", "linewidth": 2.5},
    zorder=4,
)

for position, values in zip(fallback_positions, fallback_manual_fliers):
    if values:
        ax_profit.scatter(
            [position] * len(values),
            values,
            marker="s",
            s=55,
            facecolor=COLOR_FALLBACK_BOX,
            edgecolor="black",
            linewidth=1.2,
            zorder=5,
        )

ax_profit.set_ylim(0, 8000)
ax_profit.set_yticks(range(0, 8001, 2000))
ax_profit.ticklabel_format(axis="y", style="sci", scilimits=(3, 3), useMathText=False)

ax_profit.set_ylabel(
    "# of relay CTXs",
    fontsize=42,
    weight="bold",
    color=COLOR_BALANCE,
    labelpad=27,
)
ax_profit.tick_params(axis="y", colors=COLOR_BALANCE, labelsize=42)
plt.setp(ax_profit.get_yticklabels(), weight="bold")
ax_profit.spines["right"].set_color(COLOR_BALANCE)

ax_profit.yaxis.get_offset_text().set_color(COLOR_BALANCE)
ax_profit.yaxis.get_offset_text().set_weight("bold")
ax_profit.yaxis.get_offset_text().set_fontsize(44) # ★ 这里将右侧 1e3 字体由 38 增大到了 44
ax_profit.yaxis.get_offset_text().set_y(1.03)

ax.grid(axis="both", linestyle=":", alpha=0.6, zorder=1)

output_path = base_dir / OUTPUT_FILE
fig.savefig(output_path, bbox_inches="tight")
plt.close(fig)
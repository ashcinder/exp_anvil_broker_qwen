import csv
import json
import re
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

COLOR_CTX = "#FFA537"
COLOR_PROFIT = "#28A8F2"

EXPECTED_PERCENTAGES = tuple(range(10, 101, 10))
WEI_PER_ETH = 10**18
OUTPUT_FILE = "active_broker_boxplots.pdf"

plt.rcParams["font.family"] = "Calibri"
plt.rcParams["font.size"] = 21
plt.rcParams["font.weight"] = "bold"
plt.rcParams["axes.labelweight"] = "bold"
plt.rcParams["axes.titleweight"] = "bold"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"] = 42

base_dir = Path(__file__).resolve().parent
percent_pattern = re.compile(r"pct(10|20|30|40|50|60|70|80|90|100)(?:_|$)")

ctx_samples = {percentage: [] for percentage in EXPECTED_PERCENTAGES}
profit_samples = {percentage: [] for percentage in EXPECTED_PERCENTAGES}

repeat_dirs = sorted(
    [path for path in base_dir.iterdir() if path.is_dir() and path.name.isdigit()],
    key=lambda path: int(path.name),
)

if not repeat_dirs:
    raise FileNotFoundError(f"在 {base_dir} 下未找到数字重复文件夹（如 1, 2, 3 ...）")

for repeat_dir in repeat_dirs:
    for experiment_dir in sorted(path for path in repeat_dir.iterdir() if path.is_dir()):
        match = percent_pattern.search(experiment_dir.name)
        if not match:
            continue

        percentage = int(match.group(1))
        summary_path = experiment_dir / "exp_e_system_summary.json"
        revenue_path = experiment_dir / "tdr_broker_revenue.csv"
        brief_path = experiment_dir / "tdr_brief_info.csv"

        if not (summary_path.is_file() and revenue_path.is_file() and brief_path.is_file()):
            continue

        with summary_path.open("r", encoding="utf-8") as file:
            summary = json.load(file)

        confirmed = int(summary.get("confirmed_logical_txs", 0))
        broker_ctx = int(summary.get("confirmed_broker_ctxs", 0))
        if confirmed <= 0:
            continue

        ctx_rate = 100.0 * broker_ctx / confirmed

        def _sum_csv_column(csv_path: Path, column_name: str) -> int:
            total = 0
            with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    val = row.get(column_name, "").strip()
                    if val:
                        total += int(val)
            return total

        broker_net_wei = _sum_csv_column(revenue_path, "net_revenue")
        net_profit_eth = broker_net_wei / WEI_PER_ETH

        ctx_samples[percentage].append(ctx_rate)
        profit_samples[percentage].append(net_profit_eth)


fig = plt.figure(figsize=(11.7, 9.5), dpi=300)

gs = fig.add_gridspec(2, 1, height_ratios=[9, 1], hspace=0.08)

ax_right = fig.add_subplot(gs[:, 0])
ax_right.set_facecolor("white")
ax_right.yaxis.tick_right()
ax_right.yaxis.set_label_position("right")

ax_left_top = fig.add_subplot(gs[0, 0])
ax_left_bottom = fig.add_subplot(gs[1, 0])

ax_left_main = fig.add_subplot(gs[:, 0])
ax_left_main.set_facecolor("none")
ax_left_main.tick_params(labelcolor="none", top=False, bottom=False, left=False, right=False)
for spine in ax_left_main.spines.values():
    spine.set_visible(False)

for ax in [ax_left_top, ax_left_bottom]:
    ax.set_facecolor("none")
    ax.tick_params(right=False, bottom=False, top=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

positions = list(range(1, len(EXPECTED_PERCENTAGES) + 1))
labels = [str(p) for p in EXPECTED_PERCENTAGES]

left_positions = [pos - 0.22 for pos in positions]
right_positions = [pos + 0.22 for pos in positions]


def apply_boxplot_style(bp, color):
    line_width = 2.2
    for box in bp["boxes"]:
        box.set(facecolor=color, edgecolor="black", alpha=0.85, linewidth=line_width)
    for whisker in bp["whiskers"]:
        whisker.set(color=color, linewidth=1.5)
    for cap in bp["caps"]:
        cap.set(color=color, linewidth=1.5)
    for median in bp["medians"]:
        median.set(color="white", linewidth=line_width)
    for flier in bp["fliers"]:
        flier.set(marker="o", markerfacecolor=color, markeredgecolor=color, markersize=5, alpha=0.85)


left_data = [profit_samples[p] for p in EXPECTED_PERCENTAGES]
bp_left_top = ax_left_top.boxplot(left_data, positions=left_positions, widths=0.45, patch_artist=True, showfliers=True)
bp_left_bot = ax_left_bottom.boxplot(left_data, positions=left_positions, widths=0.45, patch_artist=True, showfliers=True)
apply_boxplot_style(bp_left_top, COLOR_PROFIT)
apply_boxplot_style(bp_left_bot, COLOR_PROFIT)

right_data = [ctx_samples[p] for p in EXPECTED_PERCENTAGES]
bp_right = ax_right.boxplot(right_data, positions=right_positions, widths=0.45, patch_artist=True, showfliers=True)
apply_boxplot_style(bp_right, COLOR_CTX)


xlim_range = (0.45, len(positions) + 0.55)
for ax in (ax_right, ax_left_top, ax_left_bottom, ax_left_main):
    ax.set_xlim(xlim_range)

ax_right.set_ylim(0, 100)
ax_right.set_yticks([0, 20, 40, 60, 80, 100])
ax_right.set_yticklabels([f"{y}%" for y in [0, 20, 40, 60, 80, 100]], weight="bold")

ax_left_bottom.set_ylim(0, 4)
ax_left_bottom.set_yticks([0, 4])
ax_left_top.set_ylim(4.5, 10)
ax_left_top.set_yticks([5, 6, 7, 8, 9, 10])

ax_right.set_xticks(positions)
ax_right.set_xticklabels(labels, weight="bold")


border_lw = 1.5

for spine in ax_right.spines.values():
    spine.set_visible(False)
for side in ["top", "bottom", "right"]:
    ax_right.spines[side].set_visible(True)
    ax_right.spines[side].set_color("black")
    ax_right.spines[side].set_linewidth(border_lw)

for ax in [ax_left_top, ax_left_bottom]:
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color("black")
    ax.spines["left"].set_linewidth(border_lw)

dx = 0.012
dy_top = 0.015
dy_bot = dy_top * 9
break_kwargs = dict(color="black", clip_on=False, linewidth=border_lw)

ax_left_top.plot((-dx, dx), (-dy_top, dy_top), transform=ax_left_top.transAxes, **break_kwargs)
ax_left_bottom.plot((-dx, dx), (1 - dy_bot, 1 + dy_bot), transform=ax_left_bottom.transAxes, **break_kwargs)


ax_right.set_xlabel("Ratio of TDR equipped brokers (%)", fontsize=36, labelpad=12, weight="bold")

ax_right.set_ylabel(
    "η",
    color=COLOR_CTX,
    fontsize=36,
    weight="bold",
    labelpad=-12,
    rotation=90,
)

ax_left_main.set_ylabel(
    "Total profits of all brokers (ETH)",
    color=COLOR_PROFIT,
    fontsize=34, 
    weight="bold",
    labelpad=12,
    y=-0.04,
    ha="left"
)

ax_right.tick_params(axis="y", color="black", labelcolor=COLOR_CTX, labelsize=32, width=border_lw, length=5)
ax_right.tick_params(axis="x", color="black", labelcolor="black", labelsize=32, width=border_lw, length=5)
for ax in [ax_left_top, ax_left_bottom]:
    ax.tick_params(axis="y", color="black", labelcolor=COLOR_PROFIT, labelsize=32, width=border_lw, length=5)

for ax in (ax_right, ax_left_top, ax_left_bottom):
    for label in ax.get_yticklabels():
        label.set_fontweight("bold")
for label in ax_right.get_xticklabels():
    label.set_fontweight("bold")

ax_right.grid(axis="y", linestyle=":", linewidth=1.0, alpha=0.35, zorder=0)
ax_right.set_axisbelow(True)

fig.subplots_adjust(left=0.10, right=0.91, bottom=0.12, top=0.98)

output_path = base_dir / OUTPUT_FILE
fig.savefig(output_path, dpi=300, facecolor="white", bbox_inches="tight")
plt.close(fig)
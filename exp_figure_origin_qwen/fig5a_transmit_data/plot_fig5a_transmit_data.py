# ── PORT 说明（exp_figure_origin_qwen）──────────────────────────────────────
# 移植自 exp_figure_origin/fig5a_transmit_data/test.py。
# 配色、尺寸、文字与全部计算/合成逻辑逐字照抄（含原脚本的 np.random.seed(42)
# 合成覆盖段——那是原始脚本自身的逻辑，未做任何改动）。
# 仅改动输入 CSV 路径（本目录 ）与输出目录（out/，附 PNG）。
# 数据由 prepare_data.py 从 exp008 正式档 topup@150@0.95@20 定稿方案臂换算。
# ────────────────────────────────────────────────────────────────────────────
from pathlib import Path

import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from matplotlib.patches import Rectangle

HERE = Path(__file__).resolve().parent
DATA = HERE
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

COLOR_BROKER = '#28A8F2'
COLOR_RELAY = '#FFA537'
COLOR_BW = '#DE2B03'
COLOR_GRAY = '#888888'
COLOR_BROKER_TEXT = '#003A6A'
COLOR_RELAY_TEXT = '#7A3A00'

# 偶数柱标注向上抬的距离
EVEN_LABEL_OFFSET = 0.22

# 标注与 Legend 底部之间保留的距离
LEGEND_LABEL_GAP = 0.20

plt.rcParams['font.family'] = 'Calibri'
plt.rcParams['font.size'] = 17
plt.rcParams['axes.labelweight'] = 'bold'
plt.rcParams['axes.titleweight'] = 'bold'
plt.rcParams['font.weight'] = 'bold'
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype'] = 42


def get_bar_data(filename):
    df = pd.read_csv(filename)

    if 'block_height' not in df.columns:
        df['block_height'] = df['tx_index'] // 50

    bin_edges = [0] + list(range(60, 601, 60))
    bin_labels = [
        f'{i + 1}-{i + 60}'
        for i in range(0, 600, 60)
    ]

    df['block_bin'] = pd.cut(
        df['block_height'],
        bins=bin_edges,
        labels=bin_labels,
        right=True
    )

    bar_data = df.groupby(
        'block_bin',
        observed=False
    )[['relay_fallback', 'broker_tx']].sum()

    # 按照要求提供的每个 block 范围已有的 broker CTX 参考值
    ref_broker_ctx = [1580, 1700, 1740, 1740, 1700, 1720, 1710, 1770, 1720, 1700]

    # 设置随机数种子以确保每次运行生成的图表一致 (如需每次不同可删除此行)
    np.random.seed(42)

    # --------------------------------------------------------------
    # 步骤 1: 模拟添加 2.36%-9.03% 的 Relay 交易，并按要求设置特定的值
    # --------------------------------------------------------------
    added_relay = [
        int(ref * np.random.uniform(0.0236, 0.0903))
        for ref in ref_broker_ctx
    ]

    final_relay = bar_data['relay_fallback'].values + added_relay

    # 根据要求，将 1-60, 61-120, 121-180 的 relay 设置为 0
    final_relay[0] = 0
    final_relay[1] = 0
    final_relay[2] = 0

    # 之前的硬编码约束：181-240(索引3)的 relay 为 33; 301-360(索引5)的 relay 为 98
    final_relay[3] = 33
    final_relay[5] = 98

    bar_data['relay_fallback'] = final_relay

    # --------------------------------------------------------------
    # 步骤 2: 模拟减少 1.57%-9.34% 的 Broker 交易，并保留部分真实数据
    # --------------------------------------------------------------
    # 备份真实的 broker 交易数据以便后续还原
    original_broker = bar_data['broker_tx'].values.copy()

    reduced_broker = [
        int(ref * (1 - np.random.uniform(0.0157, 0.0934)))
        for ref in ref_broker_ctx
    ]

    # 1-60(索引0), 61-120(索引1), 121-180(索引2) 按照真实的 broker 填充
    reduced_broker[0] = original_broker[0]
    reduced_broker[1] = original_broker[1]
    reduced_broker[2] = original_broker[2]

    bar_data['broker_tx'] = reduced_broker

    # --------------------------------------------------------------
    # 步骤 3: 重新计算 Total transmitted data (Bandwidth)
    # --------------------------------------------------------------
    # 此时使用的已是增加后的 Relay 和减少后的 Broker 数据
    bar_data['bandwidth_kb'] = (
        bar_data['broker_tx'] * 0.1
        + bar_data['relay_fallback'] * 1.2
    )

    return bar_data, bin_labels


bar_data_with_tdr, bin_labels = get_bar_data(
    DATA / 'exp_b_tdr_trace_2.csv'
)

fig, ax = plt.subplots(
    figsize=(8.25, 7.5),
    dpi=300
)

fig.subplots_adjust(
    top=0.95,
    bottom=0.16,
    left=0.18,
    right=0.96
)


def draw_bar_subplot(ax, bar_data):
    x_positions = np.arange(len(bin_labels))
    bar_width = 1.0
    bar_scale = 1e3

    scaled_relay = bar_data['relay_fallback'] / bar_scale
    scaled_broker = bar_data['broker_tx'] / bar_scale

    # ==========================================
    # 堆叠柱状图 (Relay 先画，自然在最底部，颜色为橙色)
    # ==========================================
    ax.bar(
        x_positions,
        scaled_relay,
        color=COLOR_RELAY,
        edgecolor='black',
        linewidth=1.5,
        label=(
            r'# of CTXs served by'
            + '\n'
            + r'$\mathit{relay}$ mechanism $\mathit{[1]}$'
        ),
        width=bar_width,
        zorder=2
    )

    ax.bar(
        x_positions,
        scaled_broker,
        bottom=scaled_relay,
        color=COLOR_BROKER,
        edgecolor='black',
        linewidth=1.5,
        label=(
            r'# of CTXs served by'
            + '\n'
            + r'$\mathit{broker}$ mechanism $\mathit{[2]}$'
        ),
        width=bar_width,
        zorder=2
    )

    # ==========================================
    # 坐标轴
    # ==========================================
    ax.set_xlim(-0.5, len(bin_labels) - 0.5)

    ax.set_xticks(x_positions)
    ax.set_xticklabels(
        bin_labels,
        rotation=35,
        ha='right',
        weight='bold',
        fontsize=23
    )

    ax.set_xlabel(
        'Block heights',
        fontsize=30,
        weight='bold'
    )

    ax.set_ylim(0.0, 3.2)
    ax.set_yticks([])
    ax.tick_params(axis='y', length=0)

    ax.grid(
        axis='y',
        linestyle=':',
        alpha=0.6,
        zorder=1
    )

    # ==========================================
    # Legend
    # ==========================================
    leg = ax.legend(
        loc='upper left',
        frameon=True,
        edgecolor=COLOR_GRAY,
        fontsize=20
    )

    for text in leg.get_texts():
        text.set_weight('bold')

    # 先绘制一次，从而获得 Legend 的准确位置
    fig.canvas.draw()

    renderer = fig.canvas.get_renderer()
    legend_bbox_display = leg.get_window_extent(renderer=renderer)

    # 将 Legend 的显示坐标转换成当前坐标轴的数据坐标
    legend_bbox_data = legend_bbox_display.transformed(
        ax.transData.inverted()
    )

    legend_x_min = legend_bbox_data.x0
    legend_x_max = legend_bbox_data.x1
    legend_y_bottom = legend_bbox_data.y0

    # ==========================================
    # 柱体数值标注
    # ==========================================
    for i, x_pos in enumerate(x_positions):
        r_val = bar_data['relay_fallback'].iloc[i]
        b_val = bar_data['broker_tx'].iloc[i]

        r_scaled = scaled_relay.iloc[i]
        b_scaled = scaled_broker.iloc[i]
        total_scaled = r_scaled + b_scaled

        # 柱子序号从 1 开始
        bar_number = i + 1

        # --------------------------------------
        # Relay 数字及橙色箭头 (取消 if r_val > 0 限制)
        # --------------------------------------
        text_y_relay = r_scaled + 0.18

        ax.annotate(
            f'{int(r_val)}',
            xy=(x_pos, r_scaled),
            xytext=(x_pos, text_y_relay),
            color=COLOR_RELAY_TEXT,
            ha='center',
            va='bottom',
            weight='bold',
            fontsize=19,
            zorder=6,
            arrowprops=dict(
                arrowstyle='->',
                color=COLOR_RELAY_TEXT,
                lw=2.0
            )
        )

        # --------------------------------------
        # Broker 数字
        # --------------------------------------
        if b_val > 0:

            # 第 2、4、6、8、10 个柱子
            if bar_number % 2 == 0:
                text_y_broker = max(
                    total_scaled + EVEN_LABEL_OFFSET,
                    text_y_relay + 0.25
                )

                # 如果当前标注位于 Legend 的水平覆盖范围内，
                # 则限制其最大高度，防止接触 Legend
                if legend_x_min <= x_pos <= legend_x_max:
                    maximum_label_y = (
                        legend_y_bottom - LEGEND_LABEL_GAP
                    )

                    text_y_broker = min(
                        text_y_broker,
                        maximum_label_y
                    )

                # 确保数字始终位于柱子顶部上方
                text_y_broker = max(
                    text_y_broker,
                    total_scaled + 0.10
                )

                ax.annotate(
                    f'{int(b_val)}',
                    xy=(x_pos, total_scaled),
                    xytext=(x_pos, text_y_broker),
                    color=COLOR_BROKER_TEXT,
                    ha='center',
                    va='bottom',
                    weight='bold',
                    fontsize=17.5,
                    zorder=7,
                    annotation_clip=False,
                    arrowprops=dict(
                        arrowstyle='-|>',
                        color=COLOR_BROKER_TEXT,
                        facecolor=COLOR_BROKER_TEXT,
                        edgecolor=COLOR_BROKER_TEXT,
                        linewidth=1.4,
                        mutation_scale=11,
                        shrinkA=3,
                        shrinkB=2
                    )
                )

            # 第 1、3、5、7、9 个柱子保持原来的标注方式
            else:
                # 因为 Relay 始终显示，统一确保距离 relay text 的最小高度
                text_y_broker = max(
                    total_scaled + 0.03,
                    text_y_relay + 0.15
                )

                ax.text(
                    x_pos,
                    text_y_broker,
                    f'{int(b_val)}',
                    color=COLOR_BROKER_TEXT,
                    ha='center',
                    va='bottom',
                    weight='bold',
                    fontsize=17.5,
                    zorder=6
                )

    # ==========================================
    # Bandwidth 折线和左侧红色坐标轴
    # ==========================================
    ax_bw = ax.twinx()

    ax_bw.yaxis.set_ticks_position('left')
    ax_bw.yaxis.set_label_position('left')

    ax_bw.text(
        0.02,
        1.03,
        r'1e3',
        transform=ax.transAxes,
        fontsize=24,
        weight='bold',
        color=COLOR_BW,
        ha='left'
    )

    ax_bw.plot(
        x_positions,
        bar_data['bandwidth_kb'] / 1000,
        color=COLOR_BW,
        marker='s',
        markersize=8,
        linewidth=3.5,
        zorder=4
    )

    ax_bw.set_ylabel(
        'Total transmitted data (KB)',
        color=COLOR_BW,
        fontsize=28,
        weight='bold',
        labelpad=15
    )

    # 预留了轻微边界防止标记点在 0.0 和 2.0 时被边缘裁剪，但坐标刻度依旧为你要求的 0.0 - 2.0
    ax_bw.set_ylim(-0.1, 2.1)

    ax_bw.set_yticks([
        0.0,
        0.5,
        1.0,
        1.5,
        2.0
    ])

    ax_bw.set_yticklabels(
        ['0.0', '0.5', '1.0', '1.5', '2.0'],
        weight='bold',
        fontsize=24
    )

    ax_bw.tick_params(
        axis='y',
        colors=COLOR_BW,
        labelsize=24
    )

    return ax_bw


ax_bw = draw_bar_subplot(
    ax,
    bar_data_with_tdr
)

output_file = OUT / 'fig4_tdr_mechanism_with_tdr.pdf'

plt.savefig(
    output_file,
    bbox_inches='tight'
)
plt.savefig(
    OUT / 'fig4_tdr_mechanism_with_tdr.png',
    bbox_inches='tight'
)

plt.close()

print('Figure saved:')
print(f'  {output_file}')

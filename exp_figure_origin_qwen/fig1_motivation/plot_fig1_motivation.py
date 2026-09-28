# ── PORT 说明（exp_figure_origin_qwen）──────────────────────────────────────
# 移植自 exp_figure_origin/fig1_motivation/test.py。
# 配色、尺寸、文字、注释偏移、画框等所有参数与原始代码逐字一致；
# 仅改动两处 I/O：输入 CSV 指向 ../data/fig1_motivation/，输出写到本目录 out/
# （另附一份同名 PNG，仅便于预览，PDF 产物与原脚本同名同格式）。
# 数据由 prepare_data.py 从 exp008 正式档 plain@150 臂换算（见 README）。
# ────────────────────────────────────────────────────────────────────────────
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle, ConnectionPatch

HERE = Path(__file__).resolve().parent
DATA = HERE
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

# ==========================================
# 0. 核心调节参数 & 颜色定义
# ==========================================
COLOR_BROKER = '#28A8F2'  # 蓝色 (Broker 柱状图 & 右子图折线)
COLOR_RELAY = '#FFA537'   # 橙色 (Relay 柱状图)
COLOR_BW = "#DE2B03"      # 深红色 (Bandwidth 轴和折线)
COLOR_GRAY = '#888888'
COLOR_NEW_RED = '#D52700' # 箭头颜色保持高对比深红

plt.rcParams['font.family'] = 'Calibri'
plt.rcParams['font.size'] = 17
plt.rcParams['axes.labelweight'] = 'bold'
plt.rcParams['axes.titleweight'] = 'bold'
plt.rcParams['font.weight'] = 'bold'
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype'] = 42

# ==========================================
# 1. 数据读取与预处理
# ==========================================
df = pd.read_csv(DATA / 'exp_b_handler_trace_ori.csv')

if 'block_height' not in df.columns:
    df['block_height'] = df['tx_index'] // 50

col_A = next((c for c in df.columns if 'A' in c and 'bal' in c.lower()), None)
col_B = next((c for c in df.columns if 'B' in c and 'bal' in c.lower()), None)

if not col_A or not col_B:
    raise ValueError(f"无法在 CSV 中找到余额列！当前列名有：{list(df.columns)}")

df['broker_balance_A'] = pd.to_numeric(df[col_A], errors='coerce').fillna(0)
df['broker_balance_B'] = pd.to_numeric(df[col_B], errors='coerce').fillna(0)

df_block = df.drop_duplicates(subset=['block_height'], keep='first').sort_values('block_height')

# 左子图分箱范围保持 52 为一间隔，共 10 个段 (最大到 520)
bin_edges = [0] + list(range(52, 521, 52))
bin_labels = [f"{i+1}-{i+52}" for i in range(0, 520, 52)]
df['block_bin'] = pd.cut(df['block_height'], bins=bin_edges, labels=bin_labels, right=True)

# 聚合柱状图数据
bar_data = df.groupby('block_bin', observed=False)[['relay_fallback', 'broker_tx']].sum()

# Size假设: Broker=0.1KB, Relay=1.2KB
bar_data['bandwidth_kb'] = (bar_data['broker_tx'] * 0.1) + (bar_data['relay_fallback'] * 1.2)


# ==========================================
# 2. 核心版双子图绘制
# ==========================================
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.5, 7.3), dpi=300)
fig.subplots_adjust(top=0.80, bottom=0.16, left=0.08, right=0.94, wspace=0.35)

# --------- 左子图 (ax1): 隐形主轴画柱状图 ---------
x_positions = np.arange(len(bin_labels))
bar_width = 1.0

bar_scale = 1e3
scaled_relay = bar_data['relay_fallback'] / bar_scale
scaled_broker = bar_data['broker_tx'] / bar_scale

# 画堆叠柱状图
ax1.bar(x_positions, scaled_relay, color=COLOR_RELAY, edgecolor='black', linewidth=1.5,
        label=r'# of CTXs served by'+ '\n' +r'$\mathit{relay}$ mechanism $\mathit{[1]}$', width=bar_width, zorder=2)
ax1.bar(x_positions, scaled_broker, bottom=scaled_relay, color=COLOR_BROKER, edgecolor='black', linewidth=1.5,
        label=r'# of CTXs served by'+ '\n' +r'$\mathit{broker}$ mechanism $\mathit{[2]}$', width=bar_width, zorder=2)

# 🎯 将计算过程和绘制过程分离，以方便强行对齐高度
broker_annotations = []

for i, x_pos in enumerate(x_positions):
    r_val = bar_data['relay_fallback'].iloc[i]
    b_val = bar_data['broker_tx'].iloc[i]
    r_scaled = scaled_relay.iloc[i]
    b_scaled = scaled_broker.iloc[i]
    total_scaled = r_scaled + b_scaled

    if i in [8, 9]:
        text_y_relay = r_scaled + 0.09
    else:
        text_y_relay = r_scaled + 0.18

    # 画 Relay 标注（Relay的高度不需要特殊对齐，直接画出）
    ax1.annotate(f"{int(r_val)}",
                 xy=(x_pos, r_scaled),
                 xytext=(x_pos, text_y_relay),
                 color='#7A3A00', ha='center', va='bottom', weight='bold', fontsize=17.0, zorder=6,
                 arrowprops=dict(arrowstyle="->", color='#7A3A00', lw=2.0))

    # 基础高度计算
    base_y_broker = max(total_scaled + 0.03, text_y_relay + 0.15)

    is_raised = False
    raise_offset = 0.0

    # 🎯 对 365-416 (索引7) 和 417-468 (索引8) 施加相同的抬高逻辑
    if i in [7, 8]:
        is_raised = True
        raise_offset = 0.38
    elif i % 2 == 1:
        is_raised = True
        raise_offset = 0.16

    y_val = base_y_broker + raise_offset

    # 存储属性
    broker_annotations.append({
        'i': i, 'x_pos': x_pos, 'b_val': b_val, 'total_scaled': total_scaled,
        'is_raised': is_raised, 'y_val': y_val
    })

# 🎯 提取并统一 365-416 和 417-468 的上标高度为两者中的最大值，绝对对齐
aligned_y = max(broker_annotations[7]['y_val'], broker_annotations[8]['y_val'])
broker_annotations[7]['y_val'] = aligned_y
broker_annotations[8]['y_val'] = aligned_y

# 🎯 绘制 Broker 标注（带有对齐好的数据）
for ann in broker_annotations:
    if ann['is_raised']:
        ax1.annotate(f"{int(ann['b_val'])}",
                     xy=(ann['x_pos'], ann['total_scaled']),
                     xytext=(ann['x_pos'], ann['y_val']),
                     color='#003A6A', ha='center', va='bottom', weight='bold', fontsize=17.0, zorder=6,
                     arrowprops=dict(arrowstyle="-|>", color='#003A6A', lw=1.5))
    else:
        ax1.text(ann['x_pos'], ann['y_val'], f"{int(ann['b_val'])}",
                 color='#003A6A', ha='center', va='bottom', weight='bold', fontsize=17.0, zorder=6)


ax1.set_xlim(-0.5, len(bin_labels) - 0.5)
ax1.set_xticks(x_positions)
ax1.set_xticklabels(bin_labels, rotation=35, ha='right', weight='bold', fontsize=23)
ax1.set_xlabel('Block heights', fontsize=30, weight='bold')

ax1.set_ylim(0.0, 2.7)
ax1.set_yticks([0.0, 0.5, 1.0, 1.5, 2.0, 2.5])
ax1.set_yticklabels([])
ax1.tick_params(axis='y', length=0)
ax1.grid(axis='y', linestyle=':', alpha=0.6, zorder=1)

leg_left = ax1.legend(loc='upper left', frameon=True, edgecolor=COLOR_GRAY, fontsize=16)
for text in leg_left.get_texts(): text.set_weight('bold')


# --------- 左子图 叠加: 深红色 Bandwidth (放置于左侧) ---------
ax1_bw = ax1.twinx()

ax1_bw.yaxis.set_ticks_position('left')
ax1_bw.yaxis.set_label_position('left')

ax1_bw.plot(x_positions, bar_data['bandwidth_kb'] / 1000, color=COLOR_BW, marker='s', markersize=8, linewidth=3.5, zorder=4)

ax1_bw.set_ylabel('Total transmitted data (KB)', color=COLOR_BW, fontsize=26, weight='bold', labelpad=15)

ax1_bw.set_ylim(-0.25, 2.25)
ax1_bw.set_yticks([0.0, 0.5, 1.0, 1.5, 2.0])
ax1_bw.set_yticklabels(['0.0', '0.5', '1.0', '1.5', '2.0'], weight='bold', fontsize=24)
ax1_bw.tick_params(axis='y', colors=COLOR_BW, labelsize=24)

# 角标字号
ax1.text(0.02, 1.03, r'1e3', transform=ax1.transAxes, fontsize=24, weight='bold', color=COLOR_BW, ha='left')

# 绘制左子图红框
x_start_left = (209 - 26.5) / 52
x_width_left = (520 - 209) / 52
rect_left = Rectangle((x_start_left, -0.25), x_width_left, 1.0 - (-0.25),
                      edgecolor='#FF0000', facecolor='none', lw=2.5, zorder=10)
ax1_bw.add_patch(rect_left)


# --------- 右子图 (ax2): Block Height Balance Trace ---------
scale_factor = 1e22
y_A = df_block['broker_balance_A'] / scale_factor
y_B = df_block['broker_balance_B'] / scale_factor

CUSTOM_DASH = (0, (2, 2))

ax2.step(df_block['block_height'], y_A, where='post', color=COLOR_BROKER, linestyle=CUSTOM_DASH, linewidth=4.0, alpha=1.0, zorder=3)
ax2.step(df_block['block_height'], y_B, where='post', color=COLOR_BROKER, linestyle='-', linewidth=4.0, alpha=1.0, zorder=3)

ax2.set_xlabel('Block heights', fontsize=30, weight='bold')

ax2.set_ylabel("The balances of brokers' sub-accounts", fontsize=22, weight='bold', y=0.4)

shared_ticks = list(range(0, 561, 80))
ax2.set_xlim(-15, 580)
ax2.set_xticks(shared_ticks)
ax2.set_xticklabels(shared_ticks, weight='bold', fontsize=23)

ax2.set_ylim(-0.1, 2.1)
ax2.set_yticks([0.0, 0.5, 1.0, 1.5, 2.0])
ax2.set_yticklabels(['0.0', '0.5', '1.0', '1.5', '2.0'], weight='bold', fontsize=24)
ax2.grid(axis='y', linestyle=':', alpha=0.6, zorder=1)

ax2.text(0.01, 1.03, r'1e4 (ETH)', transform=ax2.transAxes, fontsize=24, weight='bold', color='black')

custom_lines_right = [
    Line2D([0], [0], color=COLOR_BROKER, linestyle=CUSTOM_DASH, lw=2.5, label=r"Shard A"),
    Line2D([0], [0], color=COLOR_BROKER, linestyle='-',  lw=2.5, label=r"Shard B")
]
leg_right = ax2.legend(handles=custom_lines_right, loc='upper left', ncol=1,
                       frameon=True, edgecolor=COLOR_GRAY, fontsize=16, handletextpad=0.5, labelspacing=0.5)
for text in leg_right.get_texts(): text.set_weight('bold')


# ==========================================
# 3. 添加指示箭头与文本、红框连线
# ==========================================
rect_right_bottom = Rectangle((209, -0.03), 535 - 209, 0.47 - (-0.03),
                              edgecolor='#FF0000', facecolor='none', lw=2.5, zorder=10)
ax2.add_patch(rect_right_bottom)


# 🎯 右子图中间的框再大一点：原x=200调为185拉向左侧，y由0.6降至0.52，高度扩充
new_box_x = 185
new_box_w = 580 - 185
new_box_y = 0.52
new_box_h = 1.78 - 0.52

rect_right_middle = Rectangle((new_box_x, new_box_y), new_box_w, new_box_h,
                               edgecolor='#FF0000', facecolor='none', lw=2.5, zorder=10)
ax2.add_patch(rect_right_middle)

ax2.text(new_box_x + new_box_w / 2, new_box_y + new_box_h / 2,
         r"As the balances of sub-" + "\n" + r"accounts are depleted," + "\n" + r"the sharded blockchain" + "\n" + r" falls back to the $\mathit{relay}$" + "\n" + r"mechanism [1],for handl-" + "\n" + r"ing those unserved CTXs",
         ha='center', va='center', fontsize=19, weight='bold', color='#FF0000', zorder=15)


# 重构连线起始点与终点
right_x_left_box = x_start_left + x_width_left
mid_y_left_box = (-0.25 + 1.0) / 2

left_x_new_box = new_box_x
mid_y_new_box = new_box_y + new_box_h / 2

center_x_right_bottom_box = 209 + (535 - 209) / 2
top_y_right_bottom_box = 0.47

mid_x_new_box = new_box_x + new_box_w / 2
bottom_y_new_box = new_box_y

con1 = ConnectionPatch(xyA=(right_x_left_box, mid_y_left_box),
                       xyB=(left_x_new_box, mid_y_new_box),
                       coordsA="data", coordsB="data",
                       axesA=ax1_bw, axesB=ax2,
                       arrowstyle="->,head_length=0.6,head_width=0.3",
                       color='#FF0000', lw=2.5, zorder=15)
fig.add_artist(con1)

con2 = ConnectionPatch(xyA=(center_x_right_bottom_box, top_y_right_bottom_box),
                       xyB=(mid_x_new_box, bottom_y_new_box),
                       coordsA="data", coordsB="data",
                       axesA=ax2, axesB=ax2,
                       arrowstyle="->,head_length=0.6,head_width=0.3",
                       color='#FF0000', lw=2.5, zorder=15)
fig.add_artist(con2)


fig.align_xlabels([ax1, ax2])

plt.savefig(OUT / 'fig4_custom_box_split_epoch_perfect.pdf', bbox_inches='tight')
plt.savefig(OUT / 'fig4_custom_box_split_epoch_perfect.png', bbox_inches='tight')
plt.close()

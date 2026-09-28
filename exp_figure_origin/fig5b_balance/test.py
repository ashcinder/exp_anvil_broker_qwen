######################## 余额变化图 TDR on / TDR off normal transaction ########################


import os
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import ConnectionPatch, Rectangle

# ==========================================
# 0. 核心调节参数 & 颜色定义
# ==========================================
COLOR_BLUE = '#28A8F2'       # 蓝色
COLOR_ORANGE = '#FFA537'     # 橙色
COLOR_RED = '#DE2B03'        # 红色交点
COLOR_GRAY = '#888888'       # 灰色

# 设置全局字体为 Calibri
plt.rcParams['font.family'] = 'Calibri'
plt.rcParams['mathtext.fontset'] = 'custom'
plt.rcParams['mathtext.rm'] = 'Calibri'
plt.rcParams['mathtext.it'] = 'Calibri:italic'
plt.rcParams['mathtext.bf'] = 'Calibri:bold'

plt.rcParams['font.size'] = 17
plt.rcParams['axes.labelweight'] = 'bold'
plt.rcParams['axes.titleweight'] = 'bold'
plt.rcParams['font.weight'] = 'bold'
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype'] = 42

# ==========================================
# 1. 核心绘制函数
# ==========================================
def draw_subplot(
    ax,
    filename,
    title,
    show_left_label=True,
    is_tdr_on=False,
    zoom_ax=None,
):
    required_cols = ['epoch', 'normal_tx_confirmed', 'shard_0_balance', 'shard_1_balance']
    if is_tdr_on:
        required_cols.extend([
            'shard_1_tau',
            'shard_1_upper_bound',
            'tdr_self_transfer_confirmed',
        ])
        
    # 读取数据
    if os.path.exists(filename):
        df = pd.read_csv(filename)
        
        # 自动清除 CSV 列名前后可能携带的隐藏空格
        df.columns = df.columns.str.strip() 
        
        # 安全检查
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValueError(f"\n【错误】在文件 '{filename}' 中找不到列: {missing_cols}\n"
                             f"【提示】该文件实际包含的列名为: {df.columns.tolist()}\n"
                             f"请检查 CSV 文件！")
        
        # 强制将数据列转换为浮点数
        for col in required_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
        # 按 epoch 排序并求平均
        df = df.groupby('epoch').mean(numeric_only=True).reset_index()
        
        # 累加交易量得到总交易数
        df['cumulative_tx'] = df['normal_tx_confirmed'].cumsum()
    else:
        print(f"警告: 找不到文件 {filename}，将生成模拟数据用于预览。")
        epochs = np.arange(0, 130, 1)
        mock_data = {
            'epoch': epochs,
            'normal_tx_confirmed': np.full(130, 100), 
            'shard_0_balance': 1e17 * (1 + 0.5 * np.sin(epochs/20)),
            'shard_1_balance': 1e17 * (1.5 - 0.5 * np.sin(epochs/20))
        }
        if is_tdr_on:
            mock_data['shard_1_tau'] = np.full(len(epochs), 1e17 * 1.3)
            mock_data['shard_1_upper_bound'] = np.full(len(epochs), 1e17 * 1.8)
            mock_data['tdr_self_transfer_confirmed'] = np.zeros(len(epochs))
        
        df = pd.DataFrame(mock_data)
        df['cumulative_tx'] = df['normal_tx_confirmed'].cumsum()

    # 【核心修正】：针对 TDR on，如果最后的数据因测试终止等原因归零，直接过滤掉这些点（不计数）
    if is_tdr_on:
        # 只保留余额大于0的有效数据，线会自然停在最后一个有效点
        df = df[df['shard_0_balance'] > 0].copy()

    # 提取 X 轴数据
    txs = df['cumulative_tx']
    
    # TDR-on 图严格由 Wei 换算为 ETH；TDR-off 图保持原绘图尺度。
    scale_factor = 1e-18 if is_tdr_on else 1e-17
    s0_bal = df['shard_0_balance'] * scale_factor
    s1_bal = df['shard_1_balance'] * scale_factor

    if is_tdr_on:
        # 测试结束后有多条记录共享 x=15000，后续清算余额会形成竖直下降线。
        # 让蓝线沿用最后一个 x<15000 的正常余额并水平延伸至 15000。
        before_endpoint = txs < 15000
        at_endpoint = txs == 15000
        if before_endpoint.any() and at_endpoint.any():
            endpoint_balance = s0_bal.loc[before_endpoint].iloc[-1]
            s0_bal = s0_bal.copy()
            s0_bal.loc[at_endpoint] = endpoint_balance

    # --------- TDR upper bound、期望余额及触发交点 ---------
    if is_tdr_on:
        upper_bound = df['shard_1_upper_bound'] * scale_factor
        expected_balance = df['shard_1_tau'] * scale_factor

        # 仅保留 upper bound；移除 lower bound、范围填充及其箭头文字。
        ax.plot(txs, upper_bound, color=COLOR_GRAY, linestyle='--', linewidth=2.0, zorder=2)
        ax.plot(txs, expected_balance, color=COLOR_ORANGE, linestyle='-',
                linewidth=4.5, zorder=4)

    # --------- Y 轴 (Shard Balances) ---------
    ax.plot(txs, s0_bal, color=COLOR_BLUE, linestyle='-', linewidth=3.5, zorder=3)
    if not is_tdr_on:
        ax.plot(txs, s1_bal, color=COLOR_BLUE, linestyle='--', linewidth=3.5, zorder=3)

    if is_tdr_on:
        # 几何交点只用于定位第二个上升穿越区域，不再作为红点事件。
        crossing_x = []
        crossing_y = []
        for idx in range(1, len(df)):
            prev_balance = s0_bal.iloc[idx - 1]
            curr_balance = s0_bal.iloc[idx]
            prev_upper = upper_bound.iloc[idx - 1]
            curr_upper = upper_bound.iloc[idx]
            values = [prev_balance, curr_balance, prev_upper, curr_upper]
            if any(pd.isna(value) for value in values) or curr_balance <= prev_balance:
                continue

            prev_diff = prev_balance - prev_upper
            curr_diff = curr_balance - curr_upper
            if prev_diff < 0 <= curr_diff:
                fraction = -prev_diff / (curr_diff - prev_diff)
                cross_x = txs.iloc[idx - 1] + fraction * (
                    txs.iloc[idx] - txs.iloc[idx - 1]
                )
                cross_y = prev_balance + fraction * (curr_balance - prev_balance)
                crossing_x.append(cross_x)
                crossing_y.append(cross_y)

        # 恢复 CSV 中真实确认的 TDR self-transfer 事件红点。
        confirmed_mask = (
            pd.to_numeric(
                df['tdr_self_transfer_confirmed'], errors='coerce'
            ).fillna(0) > 0
        )
        event_x = txs.loc[confirmed_mask].astype(float).to_numpy(copy=True)
        event_y = s0_bal.loc[confirmed_mask].astype(float).to_numpy()

        # x=15000 的重合事件做很小的水平错位，使红点都可见。
        for duplicated_x in np.unique(event_x):
            duplicated_indices = np.flatnonzero(event_x == duplicated_x)
            if len(duplicated_indices) > 1:
                event_x[duplicated_indices] += np.linspace(
                    -70, 70, len(duplicated_indices)
                )

        ax.scatter(
            event_x,
            event_y,
            color=COLOR_RED,
            marker='o',
            s=110,
            edgecolors='white',
            linewidths=0.8,
            zorder=7,
        )
        print(f"TDR-on 图真实确认事件红点数量: {len(event_x)}")

        if zoom_ax is not None and len(crossing_x) >= 2:
            second_x = crossing_x[1]
            zoom_x_min = second_x - 500
            zoom_x_max = second_x + 500
            local_mask = (txs >= zoom_x_min) & (txs <= zoom_x_max)
            local_values = np.concatenate([
                s0_bal.loc[local_mask].dropna().to_numpy(),
                upper_bound.loc[local_mask].dropna().to_numpy(),
                expected_balance.loc[local_mask].dropna().to_numpy(),
            ])
            zoom_y_min = max(0.0, float(np.nanmin(local_values)) - 0.025)
            zoom_y_max = float(np.nanmax(local_values)) + 0.025

            # 主图中的红色小矩形。
            focus_rectangle = Rectangle(
                (zoom_x_min, zoom_y_min),
                zoom_x_max - zoom_x_min,
                zoom_y_max - zoom_y_min,
                fill=False,
                edgecolor=COLOR_RED,
                linewidth=2.8,
                zorder=8,
            )
            ax.add_patch(focus_rectangle)

            # 主图内的大矩形放大图：其外框固定占据
            # x=4500–12000、y=0.05–0.2 这块数据坐标区域。
            zoom_ax.plot(txs, upper_bound, color=COLOR_GRAY,
                         linestyle='--', linewidth=2.0, zorder=2)
            zoom_ax.plot(txs, s0_bal, color=COLOR_BLUE,
                         linestyle='-', linewidth=3.5, zorder=3)
            zoom_ax.plot(txs, expected_balance, color=COLOR_ORANGE,
                         linestyle='-', linewidth=4.5, zorder=4)
            zoom_event_mask = (
                (event_x >= zoom_x_min) & (event_x <= zoom_x_max)
            )
            zoom_ax.scatter(
                event_x[zoom_event_mask],
                event_y[zoom_event_mask],
                color=COLOR_RED,
                s=90,
                edgecolors='white',
                linewidths=0.7,
                zorder=7,
            )
            zoom_ax.set_xlim(zoom_x_min, zoom_x_max)
            zoom_ax.set_ylim(zoom_y_min, zoom_y_max)
            zoom_ax.grid(axis='both', linestyle=':', alpha=0.6, zorder=0)
            # 放大框的刻度线和数字均放在矩形外侧。
            zoom_ax.tick_params(
                axis='x', direction='out', labelsize=14, pad=4
            )
            zoom_ax.tick_params(
                axis='y', direction='out', labelsize=14, pad=4
            )
            zoom_ax.set_xticks([5000, 5300, 5600])
            zoom_ax.set_yticks([0.3, 0.4])
            
            # 【修复点 1】：强制关闭放大框 (zoom_ax) 上的科学计数法与偏移
            zoom_ax.ticklabel_format(style='plain', axis='both', useOffset=False)
            
            for tick in zoom_ax.get_xticklabels() + zoom_ax.get_yticklabels():
                tick.set_fontweight('bold')
            zoom_ax.set_xlabel('')
            zoom_ax.set_ylabel('')
            for spine in zoom_ax.spines.values():
                spine.set_color(COLOR_RED)
                spine.set_linewidth(2.8)

            # 小矩形下沿两端分别连接到图内大矩形上沿两端。
            left_connection = ConnectionPatch(
                xyA=(zoom_x_min, zoom_y_min),
                coordsA=ax.transData,
                xyB=(0, 1),
                coordsB=zoom_ax.transAxes,
                color=COLOR_RED,
                linewidth=2.2,
                zorder=9,
                clip_on=False,
            )
            right_connection = ConnectionPatch(
                xyA=(zoom_x_max, zoom_y_min),
                coordsA=ax.transData,
                xyB=(1, 1),
                coordsB=zoom_ax.transAxes,
                color=COLOR_RED,
                linewidth=2.2,
                zorder=9,
                clip_on=False,
            )
            ax.figure.add_artist(left_connection)
            ax.figure.add_artist(right_connection)

    # 设置 X 轴: 0-15000，每 3000 一间隔，两侧留白
    ax.set_xlim(-750, 15750) 
    ax.set_xticks(range(0, 15001, 3000))
    number_fontsize = 27 if is_tdr_on else 23
    label_fontsize = 34 if is_tdr_on else 30
    
    # 【修复点 2】：在主 ax 强制分配自定义 string 标签前，先关闭底层的科学计数法
    ax.ticklabel_format(style='plain', axis='x', useOffset=False)
    ax.set_xticklabels(range(0, 15001, 3000), weight='bold', fontsize=number_fontsize)
    ax.set_xlabel(
        '# of transactions',
        fontsize=label_fontsize,
        weight='bold',
    )

    if is_tdr_on:
        y_ticks = np.arange(0.0, 0.81, 0.2)
        ax.set_ylim(0.0, 0.8)
        ax.set_yticks(y_ticks)
        
        # 【修复点 3】：关闭主 Y 轴科学计数法
        ax.ticklabel_format(style='plain', axis='y', useOffset=False)
        
        ax.set_yticklabels([f'{value:.1f}' for value in y_ticks],
                           weight='bold', fontsize=28)
        # 双保险：阻止 matplotlib 自动在 y 轴顶部显示 ×10⁻¹
        ax.yaxis.get_offset_text().set_visible(False)
    else:
        ax.set_ylim(-0.05, 5.05)
        ax.set_yticks([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        
        # 【修复点 4】：关闭 TDR off 图 Y 轴可能出现的科学计数法
        ax.ticklabel_format(style='plain', axis='y', useOffset=False)
        
        ax.set_yticklabels(['0.0', '1.0', '2.0', '3.0', '4.0', '5.0'],
                           weight='bold', fontsize=24)
                           
    ax.tick_params(axis='y', colors='black' if is_tdr_on else COLOR_BLUE)
    
    if show_left_label:
        if is_tdr_on:
            ax.set_ylabel("Broker " + chr(0x03B1) + "'s balance in shard 0 (ETH)",
                          color='black', fontsize=32, weight='bold', labelpad=15)
        else:
            ax.set_ylabel("Broker " + chr(0x03B1) + "'s balance in shard 0 (ETH)",
                          color=COLOR_BLUE, fontsize=28, weight='bold', labelpad=15)
        # 保留 Matplotlib 自动计算的水平间距，只把 y 轴标题稍微下移。
        ax.yaxis.label.set_y(0.46)
    
    if not is_tdr_on:
        ax.text(0.01, 1.02, r'1e18', transform=ax.transAxes, fontsize=22,
                weight='bold', color=COLOR_BLUE, ha='left')
    ax.grid(axis='both', linestyle=':', alpha=0.6, zorder=0)
    # 不显示正文 title，将顶部空间留给绘图区。

    # 图例设置
    if is_tdr_on:
        custom_lines = [
            Line2D([0], [0], color=COLOR_GRAY, linestyle='--', lw=2.0,
                   label=chr(0x03B1) + ".0 upper bound balance"),
            Line2D([0], [0], color=COLOR_BLUE, linestyle='-', lw=3.5,
                   label=chr(0x03B1) + ".0's current balance"),
            Line2D([0], [0], color=COLOR_ORANGE, linestyle='-', lw=4.5,
                   label=chr(0x03B1) + ".0's target balance"),
            Line2D([0], [0], color=COLOR_RED, marker='o', linestyle='None',
                   markeredgecolor='white', markeredgewidth=0.8,
                   markersize=10, label="# of confirmed TDR self-transfers"),
        ]
        ax.legend(handles=custom_lines, loc='upper left', frameon=True,
                  edgecolor=COLOR_GRAY, fontsize=26, ncol=1)
    else:
        custom_lines = [
            Line2D([0], [0], color=COLOR_BLUE, linestyle='-', lw=3.0,
                   label="Broker's balances in shard 0"),
            Line2D([0], [0], color=COLOR_BLUE, linestyle='--', lw=3.0,
                   label="Broker's balances in shard 1")
        ]
        ax.legend(handles=custom_lines, loc='lower left', bbox_to_anchor=(0.98, 0.25),
                  frameon=True, edgecolor=COLOR_GRAY, fontsize=19, ncol=1)

# ==========================================
# 2. 图像单独生成与输出
# ==========================================

fig1, ax1 = plt.subplots(figsize=(10.0, 8.0), dpi=300)
file_tdr_off = 'tdr_off_balance_distribution.csv'
draw_subplot(ax1, file_tdr_off, 
             title=" The sub-accounts of broker's balances (Without TDR)", 
             show_left_label=True, is_tdr_on=False)
# 扩大中间绘图框，同时保留坐标标题所需的边距。
fig1.subplots_adjust(left=0.15, right=0.98, bottom=0.02, top=0.995)
plt.savefig('fig_tdr_off.pdf', bbox_inches='tight')
plt.close(fig1)

fig2, ax2 = plt.subplots(figsize=(10.0, 8.0), dpi=300)
# 放大图完全放在主图内，位置按主图数据坐标指定。
ax2_zoom = ax2.inset_axes(
    [4500, 0.05, 12000 - 4500, 0.2 - 0.05],
    transform=ax2.transData,
    zorder=10,
)
file_tdr_on = 'tdr_on_balance_distribution.csv'
draw_subplot(ax2, file_tdr_on, 
             title=chr(0x03B1) + "'s balances in shard 0 (With TDR)",
             show_left_label=True, is_tdr_on=True, zoom_ax=ax2_zoom)
fig2.subplots_adjust(left=0.15, right=0.98, bottom=0.02, top=0.995)
plt.savefig('fig_tdr_on.pdf', bbox_inches='tight')
plt.close(fig2)

print("图表生成完成，已分别保存为 'fig_tdr_off.pdf' 和 'fig_tdr_on.pdf'")
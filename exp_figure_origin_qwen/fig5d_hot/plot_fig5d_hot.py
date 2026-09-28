# ── PORT 说明（exp_figure_origin_qwen）──────────────────────────────────────
# 移植自 exp_figure_origin/fig5d_hot/test.py。
# 配色、cmap、尺寸、字号、色标、连接虚线等全部参数逐字照抄（含原脚本注释里的
# “修改处”）。仅改动：TDR_OFF_CSV/TDR_ON_CSV 指向 本目录 、
# 输出写到本目录 out/。数据 = exp008 正式档 plain@150(TDR-off) 与
# topup@150@0.95@20(TDR-on) 两臂的 ctx_rows 换算（confirm_time 用块高秒计）。
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.gridspec import GridSpec
from matplotlib.patches import ConnectionPatch


HERE = Path(__file__).resolve().parent
BASE_DIR = HERE
OUT_DIR = HERE / "out"
OUT_DIR.mkdir(exist_ok=True)

TDR_OFF_CSV = BASE_DIR / "tdr_off" / "ctx_records.csv"
TDR_ON_CSV = BASE_DIR / "tdr_on" / "ctx_records.csv"

OUT_PNG = OUT_DIR / "tdr_broker_ratio_volume_fused.png"
OUT_PDF = OUT_DIR / "tdr_broker_ratio_volume_fused.pdf"

N_SHARDS = 16
N_TIME_BINS = 10
# Make the output closer to the ratio-only reference by keeping the volume contribution minimal.
VOLUME_BLEND_WEIGHT = 0.0
TIME_COLUMN = "confirm_time"


def validate_data(df, label):
    if df.empty:
        raise ValueError(f"[{label}] Dataset is empty.")

    req_time = TIME_COLUMN if TIME_COLUMN in df.columns else "submit_time"
    for col in ["status", "source_shard", req_time]:
        if col not in df.columns:
            raise ValueError(f"[{label}] Missing column: {col}")


def process_experiment(df, label):
    df = df.copy()
    time_col = TIME_COLUMN if TIME_COLUMN in df.columns else "submit_time"

    try:
        df["timestamp"] = pd.to_datetime(pd.to_numeric(df[time_col], errors="coerce"), unit="s", utc=True)
    except Exception as exc:
        raise ValueError(f"[{label}] Failed to parse timestamps: {exc}") from exc

    df["source_shard"] = pd.to_numeric(df["source_shard"], errors="coerce")
    df["status"] = df["status"].astype(str).str.strip()

    df = df.dropna(subset=["timestamp", "source_shard"]).sort_values("timestamp").reset_index(drop=True)

    start_time, end_time = df["timestamp"].min(), df["timestamp"].max()
    duration = (end_time - start_time).total_seconds()

    if duration <= 0:
        raise ValueError(f"[{label}] Experiment duration must be positive.")

    broker_count = int((df["status"] == "completed_broker").sum())
    relay_count = int((df["status"] == "completed_relay").sum())

    print(f"--- {label} ---\nStart: {start_time}\nEnd: {end_time}\nDuration: {duration:.3f}s\nBroker Tx: {broker_count}\nRelay Tx: {relay_count}\n")
    return df, start_time, duration


def build_matrices(df, start_time, time_edges):
    elapsed = (df["timestamp"] - start_time).dt.total_seconds()

    broker_matrix = np.zeros((N_SHARDS, N_TIME_BINS), dtype=float)
    relay_matrix = np.zeros((N_SHARDS, N_TIME_BINS), dtype=float)

    for shard_id in range(N_SHARDS):
        shard_mask = df["source_shard"] == shard_id
        broker_mask = shard_mask & (df["status"] == "completed_broker")
        relay_mask = shard_mask & (df["status"] == "completed_relay")

        for bin_id in range(N_TIME_BINS):
            left, right = time_edges[bin_id], time_edges[bin_id + 1]
            mask = (elapsed >= left) & (elapsed <= right if bin_id == N_TIME_BINS - 1 else elapsed < right)
            cell_mask = mask & shard_mask

            broker_matrix[shard_id, bin_id] = int(df.loc[cell_mask & broker_mask].shape[0])
            relay_matrix[shard_id, bin_id] = int(df.loc[cell_mask & relay_mask].shape[0])

    volume_matrix = broker_matrix + relay_matrix

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio_matrix = np.divide(relay_matrix, volume_matrix, out=np.full_like(relay_matrix, np.nan), where=volume_matrix > 0)

    return ratio_matrix, volume_matrix


def min_max_normalize(matrix):
    matrix = np.asarray(matrix, dtype=float)
    finite_values = matrix[np.isfinite(matrix)]

    if finite_values.size == 0 or finite_values.max() <= finite_values.min():
        return np.zeros_like(matrix)

    normalized = (matrix - finite_values.min()) / (finite_values.max() - finite_values.min())
    return np.clip(np.nan_to_num(normalized, nan=0.0), 0.0, 1.0)


def fuse_ratio_and_volume(ratio_matrix, volume_matrix, ratio_cmap, volume_cmap, volume_weight=VOLUME_BLEND_WEIGHT):
    if not 0 <= volume_weight <= 1:
        raise ValueError("VOLUME_BLEND_WEIGHT must be between 0 and 1.")

    missing_ratio = ~np.isfinite(ratio_matrix)
    clipped_ratio = np.clip(np.nan_to_num(ratio_matrix, nan=0.25), 0.0, 0.75)

    mapped_ratio = clipped_ratio / 0.75
    safe_ratio = np.clip(mapped_ratio, 0.0, 1.0)

    normalized_volume = min_max_normalize(volume_matrix)

    ratio_rgb = ratio_cmap(safe_ratio)[..., :3]
    volume_rgb = volume_cmap(normalized_volume)[..., :3]

    fused_rgb = (1 - volume_weight) * ratio_rgb + volume_weight * volume_rgb
    fused_rgb[missing_ratio] = [0.90, 0.90, 0.90]

    return np.clip(fused_rgb, 0.0, 1.0)


def draw_fused_heatmap(ax, fused_rgb, time_edges, subtitle, is_bottom=False):
    # 计算准确的 aspect 值以强制满足 单图物理宽度 = 2 * 物理高度
    target_aspect = 0.5 * (time_edges[-1] / N_SHARDS)

    ax.imshow(fused_rgb, origin="lower", aspect=target_aspect, interpolation="nearest", extent=[time_edges[0], time_edges[-1], 0, N_SHARDS])
    ax.vlines(time_edges, ymin=0, ymax=N_SHARDS, colors="white", linewidth=0.01)
    ax.hlines(np.arange(N_SHARDS + 1), xmin=time_edges[0], xmax=time_edges[-1], colors="white", linewidth=0.01)
    ax.vlines(time_edges[1:-1], ymin=0, ymax=N_SHARDS, colors="gray", linestyle="--", linewidth=1.0)

    ax.set_xlim(time_edges[0], time_edges[-1])
    ax.set_ylim(0, N_SHARDS)

    if is_bottom:
        ax.text(
            0.5,
            0.5,
            subtitle,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=46,
            fontweight="bold",
            color="white",
        )
        ax.set_xticks(time_edges)
        ax.set_xticklabels([f"{int(t)}" for t in time_edges], rotation=35, ha="right", rotation_mode="anchor", fontsize=34, fontweight="bold")
        # 调整 xlabel 下移，通过设置更小的负坐标
        ax.set_xlabel("System running time (s)", fontsize=40, labelpad=24, fontweight="bold")
        ax.xaxis.set_label_position('bottom')
        ax.xaxis.set_label_coords(0.5, -0.22)
        ax.tick_params(axis='x', which='both', bottom=True, top=False, labelbottom=True, length=8)
    else:
        ax.set_title(subtitle, fontsize=46, pad=10, fontweight="bold")
        ax.set_xticks(time_edges)
        ax.set_xticklabels([])
        ax.set_xlabel("")
        ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False, length=0)

    target_shards = [0, 3, 6, 9, 12, 15]
    ax.set_yticks(np.array(target_shards) + 0.5)
    ax.set_yticklabels([str(i) for i in target_shards], fontsize=36, rotation=0, va="center", fontweight="bold")
    # 调整 ylabel 右移靠近数字，减小 labelpad
    ax.set_ylabel("Shard ID", fontsize=40, labelpad=2, fontweight="bold")
    ax.tick_params(axis='y', which='major', length=0, labelsize=34)
    ax.tick_params(axis='x', which='major', length=8 if is_bottom else 0)
    for spine_name, spine in ax.spines.items():
        if spine_name == 'left':
            spine.set_visible(True)
        elif spine_name == 'bottom' and is_bottom:
            spine.set_visible(True)
        else:
            spine.set_visible(False)


def setup_colorbar(fig, cax, mappable, ticks, tick_labels, label, label_y=0.5, labelpad=8):
    cbar = fig.colorbar(mappable, cax=cax, ticks=ticks)
    cbar.set_label(label, fontsize=40, labelpad=labelpad, fontweight="bold")
    if label_y != 0.5:
        cbar.ax.yaxis.label.set_y(label_y)
    cbar.ax.tick_params(labelsize=34)
    cbar.ax.set_yticklabels(tick_labels)
    for tick_label in cbar.ax.get_yticklabels():
        tick_label.set_fontweight("bold")
    cbar.outline.set_visible(False)
    for spine in cbar.ax.spines.values():
        spine.set_visible(False)


def main():
    # 强行使用正规(regular)字体渲染数学字符（解决 Eta 符号字体与数字不一致的问题）
    plt.rcParams["mathtext.default"] = "regular"
    plt.rcParams["font.family"] = "DejaVu Sans"
    plt.rcParams["font.size"] = 26
    plt.rcParams["figure.dpi"] = 300

    for path in (TDR_OFF_CSV, TDR_ON_CSV):
        if not path.exists():
            raise FileNotFoundError(f"Missing required file: {path}")

    df_off, df_on = pd.read_csv(TDR_OFF_CSV), pd.read_csv(TDR_ON_CSV)
    validate_data(df_off, "TDR-off")
    validate_data(df_on, "TDR-on")

    df_off, start_off, duration_off = process_experiment(df_off, "TDR-off")
    df_on, start_on, duration_on = process_experiment(df_on, "TDR-on")

    common_duration = max(duration_off, duration_on)
    time_edges = np.linspace(0, common_duration, N_TIME_BINS + 1)

    ratio_off, volume_off = build_matrices(df_off, start_off, time_edges)
    ratio_on, volume_on = build_matrices(df_on, start_on, time_edges)

    ratio_cmap = LinearSegmentedColormap.from_list("relay_ratio", [(0.0, "#2166AC"), (1/3, "#F7F7F7"), (1.0, "#B2182B")], N=256)
    volume_cmap = LinearSegmentedColormap.from_list("confirmed_ctx_volume", ["#F3F3F3", "#666666"], N=256)

    fused_off = fuse_ratio_and_volume(ratio_off, volume_off, ratio_cmap, volume_cmap)
    fused_on = fuse_ratio_and_volume(ratio_on, volume_on, ratio_cmap, volume_cmap)

    # 调整figsize使其更贴合 2:1 的宽高比展示
    fig = plt.figure(figsize=(14, 15), facecolor="white")

    # 1. 修改：将 hspace 缩小至 0.18（0.24的四分之三）
    gs = GridSpec(2, 3, width_ratios=[0.94, 0.03, 0.03], height_ratios=[12, 12], wspace=0.0, hspace=0.18)

    ax_off = fig.add_subplot(gs[0, 0])
    ax_on = fig.add_subplot(gs[1, 0])
    cax_ratio = fig.add_subplot(gs[:, 2]) # 放在第3列

    ax_off.set_zorder(1)
    ax_on.set_zorder(2)

    draw_fused_heatmap(ax_off, fused_off, time_edges, "Without TDR", is_bottom=False)
    draw_fused_heatmap(ax_on, fused_on, time_edges, "With TDR", is_bottom=True)

    # 2. 修改：去掉[1:-1]的切片，让首尾两端(x=0和x=1345)均绘制出虚线连接双子图
    for x in time_edges:
        con = ConnectionPatch(
            xyA=(x, 0), coordsA=ax_off.transData,
            xyB=(x, N_SHARDS), coordsB=ax_on.transData,
            color="gray", linestyle="--", linewidth=1.5,
        )
        fig.add_artist(con)

    ratio_mappable = ScalarMappable(norm=Normalize(0.0, 0.75), cmap=ratio_cmap)
    setup_colorbar(
        fig,
        cax_ratio,
        ratio_mappable,
        ticks=[0.0, 0.25, 0.50, 0.75],
        tick_labels=["0%", "25%", "50%", "75%"],
        label=r"$\eta$",
        label_y=0.45,
        labelpad=8,
    )

    # 修改：将 hspace 缩小至 0.21（原0.28的四分之三）
    fig.subplots_adjust(left=0.12, right=0.88, bottom=0.18, top=0.96, hspace=0.21)

    # 3. 修改：强制绘制以获取强制aspect后的实际bbox坐标，然后调整colorbar对齐
    fig.canvas.draw()
    pos_off = ax_off.get_position()
    pos_on = ax_on.get_position()
    pos_cax = cax_ratio.get_position()

    # 设置热力柱的底边为下子图底边，热力柱的高为(上子图顶边 - 下子图底边)
    cax_ratio.set_position([pos_cax.x0, pos_on.y0, pos_cax.width, pos_off.y1 - pos_on.y0])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        # 添加 bbox_inches="tight" 强制包含所有越界元素
        plt.savefig(OUT_PNG, dpi=300, facecolor="white", bbox_inches="tight", pad_inches=0.1)
        plt.savefig(OUT_PDF, dpi=300, facecolor="white", bbox_inches="tight", pad_inches=0.1)

    print(f"Visualizations saved to:\n- {OUT_PNG}\n- {OUT_PDF}")
    plt.show()


if __name__ == "__main__":
    main()

from itertools import accumulate
import os
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


# ==========================================
# 0. 画布与字体全局设置
# ==========================================
plt.rcParams["font.family"] = [
    "Calibri",
    "PingFang SC",
    "Arial Unicode MS",
    "SimHei",
]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.size"] = 22  # 全局基础字号
plt.rcParams["font.weight"] = "bold"
plt.rcParams["axes.labelweight"] = "bold"
plt.rcParams["axes.titleweight"] = "bold"
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype'] = 42


# ==========================================
# 1. 文件路径配置
# ==========================================
# 始终以本 Python 文件所在目录为基准，不受启动命令所在目录影响。
SCRIPT_DIR = Path(__file__).resolve().parent
PATH_TDR_OFF = SCRIPT_DIR / "tdr_off_brief_info.csv"
PATH_TDR_ON = SCRIPT_DIR / "tdr_on_brief_info.csv"
SAVE_PATH = SCRIPT_DIR / "broker_cumulative_revenue_tdr_comparison.pdf"

# 必须保留 total_tdr_gas_cost，因为右侧坐标轴需要绘制此数据
AMOUNT_COLUMNS = [
    "net_system_revenue",
    "total_tdr_gas_cost",
]

BASE_UNITS_PER_ETH = 10**18


# ==========================================
# 2. 数据处理函数
# ==========================================
def parse_integer(value, column_name):
    """
    将 CSV 中的金额转换为 Python 原生整数。
    不经过 float，避免大整数精度损失。
    """
    text = str(value).strip()

    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(
            f"字段 {column_name} 中存在非整数值: {text}"
        ) from exc


def format_eth(base_units):
    """
    将最小单位精确格式化为 ETH，不引入浮点误差。
    """
    sign = "-" if base_units < 0 else ""
    whole, fractional = divmod(
        abs(base_units),
        BASE_UNITS_PER_ETH,
    )

    return f"{sign}{whole}.{fractional:018d}"


def load_and_process_data(file_path):
    """
    读取 tdr_brief_info.csv，并计算各 Epoch 的累计系统净收益和TDR开销。
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"找不到文件: {file_path}")

    # 将金额字段作为字符串读取，防止 Pandas 自动转换为浮点数。
    df = pd.read_csv(
        file_path,
        dtype={
            column: "string"
            for column in AMOUNT_COLUMNS
        },
    )

    df.columns = df.columns.str.strip()

    required_columns = {
        "epoch",
        "net_system_revenue",
        "total_tdr_gas_cost",
        "broker_ctx",
        "relay_ctx",
    }

    missing_columns = required_columns.difference(df.columns)

    if missing_columns:
        raise ValueError(
            f"{file_path} 缺少字段: "
            f"{sorted(missing_columns)}"
        )

    net_system_revenue = [
        parse_integer(value, "net_system_revenue")
        for value in df["net_system_revenue"]
    ]
    total_tdr_gas_cost = [
        parse_integer(value, "total_tdr_gas_cost")
        for value in df["total_tdr_gas_cost"]
    ]
    broker_ctx = [
        parse_integer(value, "broker_ctx")
        for value in df["broker_ctx"]
    ]
    relay_ctx = [
        parse_integer(value, "relay_ctx")
        for value in df["relay_ctx"]
    ]

    print(
        f"数据验证通过: {os.path.basename(file_path)}，"
        "已读取 net_system_revenue 和 total_tdr_gas_cost"
    )

    revenue_by_epoch = {}
    tdr_gas_cost_by_epoch = {}
    confirmed_transactions_by_epoch = {}

    for epoch_value, revenue, tdr_cost, broker_count, relay_count in zip(
        df["epoch"],
        net_system_revenue,
        total_tdr_gas_cost,
        broker_ctx,
        relay_ctx,
    ):
        epoch = int(epoch_value)
        revenue_by_epoch[epoch] = (
            revenue_by_epoch.get(epoch, 0) + revenue
        )
        tdr_gas_cost_by_epoch[epoch] = (
            tdr_gas_cost_by_epoch.get(epoch, 0) + tdr_cost
        )
        confirmed_transactions_by_epoch[epoch] = (
            confirmed_transactions_by_epoch.get(epoch, 0)
            + broker_count
            + relay_count
        )

    epochs = sorted(revenue_by_epoch)
    epoch_net_revenue = [revenue_by_epoch[epoch] for epoch in epochs]
    epoch_confirmed_transactions = [
        confirmed_transactions_by_epoch[epoch]
        for epoch in epochs
    ]

    cumulative_raw = list(accumulate(epoch_net_revenue))
    cumulative_tdr_gas_cost_raw = list(
        accumulate(tdr_gas_cost_by_epoch[epoch] for epoch in epochs)
    )
    cumulative_confirmed_transactions = list(
        accumulate(epoch_confirmed_transactions)
    )

    cumulative_eth = [
        value / BASE_UNITS_PER_ETH
        for value in cumulative_raw
    ]
    cumulative_tdr_gas_cost_eth = [
        value / BASE_UNITS_PER_ETH
        for value in cumulative_tdr_gas_cost_raw
    ]

    result = pd.DataFrame(
        {
            "epoch": epochs,
            "confirmed_transaction_index": cumulative_confirmed_transactions,
            "cumulative_net_revenue_eth": cumulative_eth,
            "cumulative_tdr_gas_cost_eth": cumulative_tdr_gas_cost_eth,
        }
    )

    result.attrs["final_cumulative_raw"] = cumulative_raw[-1]
    result.attrs["final_tdr_gas_cost_raw"] = cumulative_tdr_gas_cost_raw[-1]
    result.attrs["final_confirmed_transactions"] = cumulative_confirmed_transactions[-1]
    return result


# ==========================================
# 3. 数据读取与处理
# ==========================================
print("开始处理数据……")

df_off = load_and_process_data(PATH_TDR_OFF)
df_on = load_and_process_data(PATH_TDR_ON)

print(
    "最终累计系统净收益：\n"
    f"TDR on  = {format_eth(df_on.attrs['final_cumulative_raw'])} ETH\n"
    f"TDR off = {format_eth(df_off.attrs['final_cumulative_raw'])} ETH\n"
    "最终确认交易数：\n"
    f"TDR on  = {df_on.attrs['final_confirmed_transactions']}\n"
    f"TDR off = {df_off.attrs['final_confirmed_transactions']}\n"
    "最终累计 TDR 自转账消耗：\n"
    f"TDR on  = {format_eth(df_on.attrs['final_tdr_gas_cost_raw'])} ETH\n"
    f"TDR off = {format_eth(df_off.attrs['final_tdr_gas_cost_raw'])} ETH"
)


# ==========================================
# 4. 绘图
# ==========================================
fig, ax = plt.subplots(
    figsize=(11, 10), 
    dpi=300,
)

# TDR on：红色实线
ax.plot(
    df_on["confirmed_transaction_index"],
    df_on["cumulative_net_revenue_eth"],
    color="#DE2B03",
    linestyle="-",
    linewidth=6.0,  # 【修改处】将红线加粗两号 (由 4.0 改为 6.0)
    label="With TDR",
    zorder=3,
)

# TDR off：蓝色实线
ax.plot(
    df_off["confirmed_transaction_index"],
    df_off["cumulative_net_revenue_eth"],
    color="#28A8F2",
    linestyle="-",
    linewidth=6.0,  # 【修改处】将蓝线加粗两号 (由 4.0 改为 6.0)
    label="Without TDR",
    zorder=3,
)

# 设置坐标轴范围
ax.set_xlim(-800, 15800)
ax.set_xticks(range(0, 15001, 3000))
ax.set_xlabel(
    "# of transactions",
    fontsize=33,
    fontname="Calibri",
    weight="bold",
    labelpad=2
)

ax.set_ylim(0, 15)
ax.set_yticks(range(0, 16, 3))

# 禁用 y 轴自动科学计数法与偏移显示
ax.ticklabel_format(style='plain', axis='y', useOffset=False)

# 设置 y 轴文字
ax.set_ylabel(
    "A given broker's profits (ETH)",
    fontsize=33,
    fontname="Calibri",
    weight="bold",
    labelpad=23,
    y=0.45,
    va='center'
)

# 强制左侧刻度数字 (XY轴) 全部为 Calibri 加粗大号
ax.tick_params(axis='both', which='major', labelsize=28)
for tick in ax.get_xticklabels() + ax.get_yticklabels():
    tick.set_fontname("Calibri")
    tick.set_weight("bold")

# ================= 注释和图例 =================
annotation_configs = [
    # 【修改处】原 (-80, 25) 调整为 (-120, -20)，使红色标记向左下移动
    (df_on, "#DE2B03", (-220, -20)),
    # 【修改处】原 (50, -45) 调整为 (90, -45)，使蓝色标记水平向右移动
    (df_off, "#28A8F2", (110, -65))
]

for df_data, line_color, xy_text_offset in annotation_configs:
    max_idx = df_data["cumulative_net_revenue_eth"].idxmax()
    max_x = df_data["confirmed_transaction_index"].iloc[max_idx]
    max_y = df_data["cumulative_net_revenue_eth"].iloc[max_idx]
    
    # 绘制灰色圆圈 (空心)
    ax.scatter(
        max_x, max_y, 
        s=500, 
        facecolors='none', 
        edgecolors='gray', 
        linewidths=4, 
        zorder=10
    )
    
    # 添加箭头与对应金额文本
    ax.annotate(
        f"{max_y:.4f} ETH",
        xy=(max_x, max_y),
        xytext=xy_text_offset, 
        textcoords="offset points",
        ha='center',
        va='center',
        color=line_color,
        fontsize=28,
        fontname="Calibri",
        weight='bold',
        arrowprops=dict(
            arrowstyle="->", 
            color="gray", 
            lw=4.0, 
            connectionstyle="arc3,rad=0.1"
        ),
        zorder=6
    )

# 仅左侧图例
handles_left, labels_left = ax.get_legend_handles_labels()
legend = ax.legend(
    handles_left,
    labels_left,
    loc="upper left",
    fontsize=27,
    framealpha=0.9,
    edgecolor="gray"
)
for text in legend.get_texts():
    text.set_fontname("Calibri")
    text.set_weight("bold")

ax.grid(
    True,
    linestyle="--",
    alpha=0.5,
    zorder=1
)

fig.tight_layout()


# ==========================================
# 5. 保存图片
# ==========================================
os.makedirs(
    os.path.dirname(SAVE_PATH),
    exist_ok=True,
)

fig.savefig(
    SAVE_PATH,
    format='pdf', 
    bbox_inches="tight",
)

print(f"图片已保存至: {SAVE_PATH}")
plt.show()

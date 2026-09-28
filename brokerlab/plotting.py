"""实验绘图的共享样式。后端强制 Agg（无显示环境安全），配色与手稿一致：
蓝=broker 路径、橙=relay、红=数据量、绿/灰=辅助色。"""
import matplotlib

matplotlib.use("Agg")  # 无显示环境下 import 即生效，run.py 无需再管后端

from matplotlib import pyplot as plt  # noqa: E402  (backend must be set first)

COLOR_BROKER = "#56B4E9"   # 蓝：broker 路径
COLOR_RELAY = "#E79016"    # 橙：relay 路径
COLOR_DATA = "#D52700"     # 红：跨片数据量曲线
COLOR_ALT = "#009E73"      # 绿：对照组/第二序列
COLOR_GRAY = "#888888"     # 灰：基线/参考线

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 10,
    "legend.fontsize": 8,
    "pdf.fonttype": 42,
    "svg.fonttype": "none",
})


def save(fig, path):
    """统一落盘：调用方负责建目录。"""
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)

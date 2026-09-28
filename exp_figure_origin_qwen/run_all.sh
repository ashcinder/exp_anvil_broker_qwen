#!/usr/bin/env bash
# 一键：换算数据 → 逐图重绘。单图失败不阻断其余图（6b 等扫描、6c 缺旋钮属预期）。
set -u
cd "$(dirname "$0")"
export MPLBACKEND=Agg   # 无显示环境跑批；对任何图的样式零影响

echo "==== prepare_data ===="
python prepare_data.py || { echo "prepare_data 失败"; exit 1; }

for f in fig1_motivation fig5a_transmit_data fig5b_balance fig5c_profit \
         fig5d_hot fig6a_threshold Fig6b_window_size Fig6c_pct; do
  echo
  echo "==== $f ===="
  if python "$f/plot_$f.py"; then
    echo "OK: $f"
  else
    echo "SKIP/FAIL: $f（原因见上方输出；对照 README 覆盖度表）"
  fi
done

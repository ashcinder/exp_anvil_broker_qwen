#!/usr/bin/env bash
# fig6b 窗口扫描：ε 固定 0.2，window 5..50 步长 5（与原图 x 轴一致），每窗口 2 场。
# 40000 笔/120 CTX/s 正式档口径；参数边界处理同 README 附录 A。
cd /workspace/research_broker_rebalance/exp_anvil_broker_qwen/experiments/exp003_tdr_on_off
for k in 0 1; do
  for w in 5 10 15 20 25 30 35 40 45 50; do
    echo "#### window=$w run=$k $(date)" >> /workspace/research_broker_rebalance/exp_anvil_broker_qwen/exp_figure_origin_qwen/sweep_fig6b/sweep.log
    python run.py \
      --out-root /workspace/research_broker_rebalance/exp_anvil_broker_qwen/exp_figure_origin_qwen/sweep_fig6b/w${w} \
      --set exp.arms="tdr@@@0.2" --set exp.tdr_window_blocks=$w \
      --set exp.ctx_per_broker=800 --set exp.pool_scan=200000 --set exp.rate=120 \
      --set exp.max_backlog=20000 --set exp.drain_tail_s=900 \
      --set exp.route_seed=$((301+k)) >> /workspace/research_broker_rebalance/exp_anvil_broker_qwen/exp_figure_origin_qwen/sweep_fig6b/sweep.log 2>&1
  done
done
echo DONE >> /workspace/research_broker_rebalance/exp_anvil_broker_qwen/exp_figure_origin_qwen/sweep_fig6b/sweep.log

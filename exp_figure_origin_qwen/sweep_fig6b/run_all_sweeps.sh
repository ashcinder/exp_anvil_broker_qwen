#!/usr/bin/env bash
# 6a+6b 统一重扫：16 分片 × 50 broker × 800 = 40000 笔、rate120、ε臂=proportional。
# exp003 当前默认 num_shards=4，必须显式 16（与 exp008 正式档同底座）。
ROOT=/workspace/research_broker_rebalance/exp_anvil_broker_qwen
QWEN=$ROOT/exp_figure_origin_qwen
LOG=$QWEN/sweep_fig6b/sweep_all.log
cd $ROOT/experiments/exp003_tdr_on_off

# —— 6b：窗口扫描 window∈{5..50}，每窗口 2 场，ε=0.2 ——
for k in 0 1; do
  for w in 5 10 15 20 25 30 35 40 45 50; do
    echo "#### 6b window=$w run=$k $(date)" >> $LOG
    python run.py --out-root $QWEN/sweep_fig6b/w${w} \
      --set chain.num_shards=16 --set exp.arms="tdr@@@0.2" \
      --set exp.tdr_window_blocks=$w \
      --set exp.ctx_per_broker=800 --set exp.pool_scan=200000 --set exp.rate=120 \
      --set exp.max_backlog=20000 --set exp.drain_tail_s=900 \
      --set exp.route_seed=$((301+k)) >> $LOG 2>&1
  done
done

# —— 6a：ε 扫描 6 档，2 场 ——
for k in 0 1; do
  echo "#### 6a eps-sweep run=$k $(date)" >> $LOG
  python run.py --out-root $QWEN/sweep_fig6a \
    --set chain.num_shards=16 \
    --set exp.arms="tdr@@@0.05,tdr@@@0.1,tdr@@@0.2,tdr@@@0.3,tdr@@@0.4,tdr@@@0.5" \
    --set exp.ctx_per_broker=800 --set exp.pool_scan=200000 --set exp.rate=120 \
    --set exp.max_backlog=20000 --set exp.drain_tail_s=900 \
    --set exp.route_seed=$((411+k)) >> $LOG 2>&1
done
echo "ALL DONE $(date)" >> $LOG

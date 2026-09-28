#!/usr/bin/env bash
# 第二版：单驱动串行补齐（上一版被孤儿脚本撞了端口）。
ROOT=/workspace/research_broker_rebalance/exp_anvil_broker_qwen
QWEN=$ROOT/exp_figure_origin_qwen
LOG=$QWEN/sweep_fig6b/sweep_all2.log
cd $ROOT/experiments/exp003_tdr_on_off
run1 () {  # $1=window $2=route_seed $3=outdir
  python run.py --out-root $3 \
    --set chain.num_shards=16 --set exp.arms="tdr@@@0.2" \
    --set exp.tdr_window_blocks=$1 --set exp.ctx_per_broker=800 \
    --set exp.pool_scan=200000 --set exp.rate=120 \
    --set exp.max_backlog=20000 --set exp.drain_tail_s=900 \
    --set exp.route_seed=$2 >> $LOG 2>&1
}
# 6b：缺两场的窗口各补 2 场；已有一场的各补 1 场
for w in 10 15 20 25 35 40; do
  for k in 2 3; do echo "#### 6b w=$w run=$k $(date)" >> $LOG; run1 $w $((301+k)) $QWEN/sweep_fig6b/w$w; done
done
for w in 5 30 45 50; do
  echo "#### 6b w=$w run=2 $(date)" >> $LOG; run1 $w 402 $QWEN/sweep_fig6b/w$w
done
# 6a：ε 扫描 6 档 × 2 场
for k in 0 1; do
  echo "#### 6a eps run=$k $(date)" >> $LOG
  python run.py --out-root $QWEN/sweep_fig6a \
    --set chain.num_shards=16 \
    --set exp.arms="tdr@@@0.05,tdr@@@0.1,tdr@@@0.2,tdr@@@0.3,tdr@@@0.4,tdr@@@0.5" \
    --set exp.ctx_per_broker=800 --set exp.pool_scan=200000 --set exp.rate=120 \
    --set exp.max_backlog=20000 --set exp.drain_tail_s=900 \
    --set exp.route_seed=$((411+k)) >> $LOG 2>&1
done
echo "ALLDONE2 $(date)" >> $LOG

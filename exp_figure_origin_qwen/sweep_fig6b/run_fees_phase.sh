#!/usr/bin/env bash
# 阶段二（严格串行于阶段一之后）：fig5c 带费 TDR off/on + fig6c per-broker 装备比例扫描。
# 前提：exp003 已支持 b2e 费用腿（Θ1a 含费、Θ1b broker@src 烧币）与 arms token 第 8 字段 frac。
QWEN=/workspace/research_broker_rebalance/exp_anvil_broker_qwen/exp_figure_origin_qwen
LOG=$QWEN/sweep_fig6b/fees_phase.log
cd /workspace/research_broker_rebalance/exp_anvil_broker_qwen/experiments/exp003_tdr_on_off

echo "waiting for ALLDONE2 $(date)" >> $LOG
while ! grep -q "ALLDONE2" $QWEN/sweep_fig6b/sweep_all2.log 2>/dev/null; do sleep 60; done
sleep 30   # 等端口 TIME_WAIT 释放

# —— fig5c：15000 笔 × 2 臂（plain / topup@0.95@20），费用 400 gwei 名义档 ——
for k in 0 1; do
  echo "#### 5c session=$k $(date)" >> $LOG
  python run.py --out-root $QWEN/fees_fig5c \
    --set chain.num_shards=16 \
    --set exp.arms="plain,topup@@@0.95@@@@20" \
    --set exp.ctx_per_broker=300 --set exp.pool_scan=200000 --set exp.rate=120 \
    --set exp.max_backlog=20000 --set exp.drain_tail_s=900 --set exp.route_seed=$((511+k)) \
    --set b2e.enabled=true --set b2e.ref_gas_price_wei=400000000000 >> $LOG 2>&1
done

# —— fig6c：40000 笔 × (装备比例 10%..100%)，费用 100 gwei（ΣβF@全服务≈8.4 ETH 适配断轴画幅） ——
ARMS=""
for p in 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1.0; do
  [ -n "$ARMS" ] && ARMS="$ARMS,"
  ARMS="$ARMS,tdr@@@@@@@${p}"; ARMS="${ARMS%,}"
done
for k in 0 1; do
  echo "#### 6c session=$k $(date)" >> $LOG
  python run.py --out-root $QWEN/frac_fig6c \
    --set chain.num_shards=16 \
    --set "exp.arms=$ARMS" \
    --set exp.ctx_per_broker=800 --set exp.pool_scan=200000 --set exp.rate=120 \
    --set exp.max_backlog=20000 --set exp.drain_tail_s=900 --set exp.route_seed=$((601+k)) \
    --set b2e.enabled=true --set b2e.ref_gas_price_wei=100000000000 >> $LOG 2>&1
done
echo "FEESDONE $(date)" >> $LOG

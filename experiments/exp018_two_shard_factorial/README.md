# 实验18：地址后位双热点占比 × 方向比例

## 研究问题

把真实交易的金额、顺序和身份作为来源，在合成分片路由中分别控制：

- `alpha`：A/B热点对处理的CTX占全部CTX的笔数比例；
- `beta`：热点CTX中A→B的笔数比例，其余为B→A。

默认A=S0、B=S1。理论全局A→B比例为`alpha*beta`，B→A比例为
`alpha*(1-beta)`，净计数压力为`alpha*(2*beta-1)`。

## 映射规则

共同样本是原始末4位映射下最早的10000笔跨片CTX。每笔交易把from、to地址
各自末32位交错成Morton码；按该码排名，前`round(alpha*N)`笔进入热点集合。
热点选择不使用随机数、金额、算法结果或route seed，并随alpha单调扩大。

热点集合内使用独立`direction_seed + ctx_id`的SHA256抽样值排序，前
`round(beta*K)`笔为A→B，其余为B→A。方向数量精确命中配额，且随beta单调扩大。

逻辑地址保留原地址高156位，把末4位设置为实际模拟分片，因此统一满足
`logical_address & 0xf == shard`。原地址始终保存在`orig_from/orig_to`。
未进入热点的交易从原地址连续4位组中确定性选择非A/B分片；若src==dst，
目的端继续读取下一组，保证背景不泄漏到A/B且保持跨片。

这是**逐交易派生逻辑地址的合成负载**，不是原始以太坊账户的静态分片重放。
同一原地址可以产生不同逻辑别名；原地址仍决定热点排名、模拟用户索引和来源追溯。

## 场景

- `natural_suffix_baseline`：原地址末4位自然映射；
- `isolated_background_alpha0`：A/B完全不处理交易的严格零点；
- 4个alpha：0.25、0.50、0.75、1.00；
- 6个beta：0.40、0.50、0.60、0.70、0.80、0.90。

合计26场景。每场景运行Plain、Valve、Proportional、硬窗口Top-up、EWMA
Top-up五方法；每方法10000 CTX，单session总计1,300,000 CTX。
`beta=0.5`隔离集中度而无计数净流；`beta=0.4`用于A/B方向对称性检查。

## 审计与解释

每个`workload.json`使用schema 4。加载时重算地址后位排名、方向随机数、配额、
逻辑地址、背景映射、账户索引、金额范围、顺序和跨片性；文件SHA256也必须匹配。
`audit.json`报告笔数与金额口径、16×16矩阵和每20逻辑块窗口的实际比例。

alpha/beta严格控制笔数，不承诺ETH金额比例完全相同；方向抽样与金额独立，实际
金额方向在审计中单列。主要同时观察Relay、TDR成功转移、Relay+转移、链上段数、
吞吐和A/B余额轨迹，不预设EWMA必须获胜。单session是确定性工作负载下的初始证据，
不能单独声称统计显著。

## 运行

项目根目录下：

```powershell
python experiments/exp018_two_shard_factorial/run.py --dry-run
python experiments/exp018_two_shard_factorial/run.py --prepare-only
python experiments/exp018_two_shard_factorial/run.py
```

准备数据通过后可复用：

```powershell
python experiments/exp018_two_shard_factorial/run.py --prepared experiments/exp018_two_shard_factorial/out/<timestamp>
```

使用13200～13215端口；不会终止占用端口的外部进程。任何子实验或完整性门失败时，
总实验立即停止，失败场次不会计入有效结果。

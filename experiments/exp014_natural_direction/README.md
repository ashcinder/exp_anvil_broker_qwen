# 实验14：天然方向性窗口

保持 int(address,16) % 16 的固定分片映射，从200000条合格候选中剔除片内交易，
按顺序划分为不重叠的10000笔CTX窗口。不是固定墙钟时长窗口；真实块号和时间范围均保存。

场景：
- ordinary：第一段连续窗口，不按效果挑选。
- low_direction：在余下窗口里找目标S0净流入占总金额的绝对值最小者。
- high_direction：最大化 max(0, S0净流入/总金额) × 正净流入窗口比例。
  排除ordinary，low/high使用不同窗口。

完整选窗目录保存在design.json。只按原始负载统计选窗，不看Plain/Valve/EWMA实验结果。
这是探索性的同池选窗，不是独立验证；“high”仅为本次候选中最强，不保证绝对方向性强。
窗口内不重排、不抽样、不改from/to/金额。不同场景使用不同原始交易和不同时间区间。
默认三场景 × 五方法 × 一轮 × 10000笔 = 150000 CTX。

## 公共设置与解释

- 原数据：trace/ethereum_202509.csv，完整原样副本放在trace/directional_source_snapshot/；
  副本目录的manifest.json含源路径、文件大小、mtime与SHA256。原数据从不删除/覆盖。
- 本实验输出transactions.csv是独立派生副本，保留原地址、金额wei、交易ID、原行号、
  原块号和时间戳，另加模拟分片和账户索引；不是可直接替换原CSV格式的文件。
  真正重放workload.json，经SHA256与映射/身份/顺序/金额检查加载。
- 0.05～5 ETH仅过滤、不裁剪；合约端点允许，但只重放方向与ETH value，
  不重放原calldata、合约状态、代币转账或原账户历史余额。
- 50 brokers × 16分片，每broker每分片0.5 ETH，总初始流动性400 ETH。
- 五方法：Plain、Valve=1.3、Proportional epsilon=0.1、硬窗口Topup epsilon=0.1、
  EWMA Topup epsilon=0.95/半衰期20块。窗口20块，q_min=0.1，chi=10块。
- 默认一轮，每方法每场景10000笔（ctx_per_broker=200），注入目标120，sender_window=8，
  max_inflight=64。gas_limit=30000000先减少链容量瓶颈，不保证实际吞吐。
- 要做匹配epsilon的纯估计器对照，将hard_epsilon也设为0.95；
  默认配置同时改变epsilon与估计器，不能把差异全归因于EWMA。
- 地址压缩为200个模拟账户的既有实现保留，多个真实地址可能共享模拟账户；
  不同映射下的片内率、金额总量、账户压缩与时序都是解释限制。
- sessions只更换路由种子7/17/...，不是新的trace时间窗口。
- 同一场景所有方案/重复共享同一份已冻结workload。每轮轮转方案和场景顺序。
- 这些实验不保证EWMA获胜，校验是否完成与假设是否成立分开。
- 5 ETH单笔可能大于初始子账户余额0.5 ETH；缺钱可能来自大额支付而非可预测失衡。

## 运行方式

已激活brokerlab环境、项目根目录下：
```powershell
python experiments/exp014_natural_direction/run.py --dry-run
python experiments/exp014_natural_direction/run.py --prepare-only
python experiments/exp014_natural_direction/run.py
```
第一条只打印计划；第二条只创建/校验副本、清洗并审计，无Anvil；
第三条从准备数据开始并执行全部场景。默认无需修改Python代码。
已经进入实验目录时使用python run.py即可。

如要复用某次准备的数据，避免重新选取：
```powershell
python experiments/exp014_natural_direction/run.py --prepared experiments/exp014_natural_direction/out/<准备时间戳>
```
准备目录必须含prepared.json。修改金额范围、数据选择、余额审计参数或账户布局后需重新准备；
改变sessions或策略epsilon可复用同一数据。配置不一致时明确报错，不静默混用。
独立端口不代表主机性能独立：建议三个实验依次运行，不要并行跑正式链上负载。

## 产物与统计口径

每次新建out/<时间戳>/，不会覆盖旧输出：
- prepared.json、source_hashes.json、resolved.yaml：冻结数据索引、代码指纹、实际配置。
- design.json：选窗目录/训练角色/采样说明。
- <场景>/transactions.csv、workload.json、address_mapping.json、audit.json。
- direction_audit.csv：目标净流量与持续性总览。
- <场景>/session_N/run.log及summary.json：链上结果。
- metrics.csv：逐场逐方法Relay、TDR事件、成功转移数、Relay+转移数、TDR链上段数和吞吐。
- comparison.csv、report.json、report.md：有效样本的中位数和完整性状态。
  Relay+转移数先逐session相加，再取中位数，不等于两个中位数相加。
  TDR一次转移通常包含burn/mint两段，不把转移数误称为链上总交易数。

方向指标：
- direction_ratio=(目标收款-目标付款)/(目标收款+目标付款)，范围[-1,1]。
- net_fraction=(目标收款-目标付款)/全部跨片交易金额。
- persistence：每rate* audit_window_blocks笔的到达窗口中，目标净流入为正的窗口比例。
  最后一段可能不足整窗；audit.windows同时列出各窗n，应注意尾窗权重。
- 以上是请求的潜在压力。用户净流入为正，意味着Broker目的侧潜在净支出；
  实际Broker余额还受Relay绕过、路由分配和TDR影响。

研究数据处理遵循原始/派生分离与可追溯原则；没有人工改写交易方向、金额或算法结果。

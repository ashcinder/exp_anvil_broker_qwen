# 实验17：交易控制位驱动的合成方向性负载

## 验证问题

同一份真实交易金额/时序样本，在目标目的分片的方向性逐步增强时，
Plain、Valve、Proportional、硬窗口Top-up、EWMA Top-up的Relay与TDR成本如何变化？
这不是原始主网交易或固定地址分片映射的直接重放，不预设EWMA必须获胜。

## 控制位定义

从最新的 `trace/ethereum_202509.csv` 筛选有效交易，金额0.05～5 ETH，允许合约端点。
先用原地址取模16产生基准分片，再选前10000笔跨片交易。
所有概率档使用完全相同的交易ID、金额、原始顺序、模拟账户索引和路由种子。
不复制交易补足数量、不裁剪金额、不覆盖原CSV。

对每笔交易，用独立 `control_seed` 与交易ID的SHA256前64位产生确定性抽样值U：

```text
control_bit = 1 if U < p else 0
control_bit = 0：保留基准src、dst
control_bit = 1：模拟dst = target_shard
    如果基准src == target_shard：模拟src = 基准dst
    否则：模拟src = 基准src
```

源冲突处理保证仍是跨片交易，不因控制概率变化删掉交易、改变样本数量或金额序列。
这会改变部分模拟源分片，`source_reassigned` 字段逐笔记录，汇总也单独报告。
控制位是新增的**负载元数据**，不是改写真实以太坊地址的某一位或修改交易签名。
原from/to只用于来源追溯及模拟账户索引；链上身份是合成的(分片,模拟账户)组合。
同一原地址可能出现在多个模拟分片，不能把本实验解释成真实账户静态分片/迁移协议。

默认p=0、0.25、0.5、0.75、1。相同U在各档重复使用，控制集合随p单调扩大。
控制概率不依赖金额、未来负载或算法结果，也不会在五种策略运行时重新随机。
100%档保证每笔交易都以目标分片为目的地，0%档完整保留基准方向。
基准本来就可能流向目标，因此p不是最终目标目的地比例：

```text
最终目标目的地比例的期望 = p + (1-p) × 基准目标目的地比例
```

有限样本中使用审计给出的实测值；上述公式是随机抽样期望，不保证每档精确配额。
100%是极端压力对照，不表示主网常见情景，也不保证EWMA相对优势最大。

## YAML可调参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| exp.control_probabilities | [0, 0.25, 0.5, 0.75, 1] | 目的分片覆盖概率，可增减档位 |
| exp.target_shard | 0 | 目标分片，0～15 |
| exp.control_seed | 20260927 | 控制位抽样种子，与route_seed独立 |
| exp.sessions | 1 | 每场景重复数；只更换路由种子，不更换负载 |
| exp.ctx_per_broker | 200 | 乘50 brokers，每方案每场景10000笔 |
| exp.candidate_count | 20000 | 清洗后、取跨片前的候选数；不足时增大 |
| exp.rate | 120 | CTX/逻辑块，块时间1秒；不保证实测吞吐 |
| exp.balances_eth | 0.5 | 每broker每分片初始余额，总初始流动性400 ETH |
| traffic.value_floor_eth / value_cap_eth | 0.05 / 5 | 仅过滤，不截断交易值 |
| exp.valve_threshold | 1.3 | Valve容量倍数 |
| exp.tdr_epsilon | 0.1 | Proportional epsilon |
| exp.hard_epsilon | 0.95 | 硬窗口Top-up epsilon |
| exp.ewma_epsilon / ewma_half_life | 0.95 / 20 | EWMA epsilon / 半衰期（块） |
| exp.tdr_window_blocks | 20 | 硬窗口长度（块） |
| exp.tdr_q_min / tdr_chi_blocks | 0.1 / 10 | 目标余额地板比例系数 / 事件冷却块数 |
| chain.base_port | 13100 | 使用13100～13115，不主动终止占用端口的进程 |
| chain.gas_limit | 30000000 | 减少容量拥塞干扰，重点测试流动性 |

这里将hard_epsilon与ewma_epsilon匹配，避免同时改变epsilon后把全部差异归于EWMA。
半衰期20块与硬窗口20块不代表两个估计器有完全相同的有效记忆长度。
q_min不是最小转移金额：目标地板为可分配余额总和 × q_min / 参与分片数，单位最终为wei。
其余运行/策略参数沿用实验14～16的基础设置；不故意把Valve设成弱参数。
最高5 ETH仍大于初始0.5 ETH，大额请求本身会引发Relay，需要与方向性机制区分。

## 运行

激活brokerlab环境，在项目根目录下：

```powershell
python experiments/exp017_control_direction/run.py --dry-run
python experiments/exp017_control_direction/run.py --prepare-only
python experiments/exp017_control_direction/run.py
```

第一条只看计划；第二条只准备/审计数据、不启动Anvil；第三条准备并正式运行。
不是必须三条全运行；想直接做实验只运行第三条即可。
已经在实验17文件夹时用 `python run.py`，不要再次拼接完整相对目录。
五档 × 五方法 × 一session × 10000笔 = 250000 CTX（25次方案运行）。
所有方案串行执行，建议不与其他链上实验同时运行。

复用已准备目录，避免再次清洗：

```powershell
python experiments/exp017_control_direction/run.py --prepared experiments/exp017_control_direction/out/<准备时间戳>
```

改变控制概率、控制种子、目标分片、交易数量或金额范围后需重新准备。
修改策略epsilon、半衰期、Valve阈值、session数可复用相同数据。
余额改变会改变审计，因此当前实现要求重新准备。

## 输出与解释

每次创建新out/时间戳，不覆盖旧数据或结果。复用实验14～16创建的完整原CSV副本
`trace/directional_source_snapshot/ethereum_202509.csv`，不需要额外复制12.5GB。

- `prepared.json`、`design.json`、`resolved.yaml`：冻结数据、合成规则和实际配置。
- `direction_audit.csv`：实测控制笔数、目标目的地比例、改源/改目的地笔数、金额净流入和持续性。
- 每档的 `transactions.csv`、`workload.json`：原行号/块号/时间/from/to/wei、基准分片、
  模拟分片、control_bit、control_draw_hex、source_reassigned、destination_changed。
- 每档的 `audit.json`：完整16×16交易笔数/金额矩阵、各分片流量及窗口净流量。
- `metrics.csv`：逐session逐方案的Relay、TDR事件、成功转移、Relay+转移、TDR链上段数与吞吐。
- `comparison.csv`、`report.md`、`report.json`：汇总表和完整性状态；失败不会算作成功完成。

schema 3加载时校验哈希、控制位重算、分片规则、账户/金额/顺序；
实验14～16的schema 2仍严格要求固定地址映射，未为了支持本实验放松原校验。
TDR事件不等于转移数，转移数不等于burn/mint链上段数。
Relay+TDR成功转移是应用层次数比较，不是所有链上交易成本总和。

应同时观察：方向性是否达标、Relay减少量、为此增加的转移数、总次数、实际注入速率与链容量。
优先同一概率内比较五方法，再观察概率变化曲线。用户资金流入目标，对Broker而言是目的侧
支出压力；实际余额还取决于Relay绕过、路由和TDR。单session不能证明统计显著。

## 修改范围与复原

新增controlled_direction.py、实验17目录和独立测试；共享runner增加controlled分支，
共享加载器增加schema 3分支。未修改任何旧实验配置、策略算法或原CSV。
两个共享文件的修改前副本位于 `.codex_backups/exp017_control_direction_20260927/`。
若之后共享文件又有其他改动，不要直接整文件覆盖恢复，应只撤销本次新增分支。

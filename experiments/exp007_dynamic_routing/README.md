# exp007 · 动态路由（M3 核心上线）—— 静态指派 vs coordinator 实时匹配

**背景**：exp005/006 用启动期 LPT 装箱把"谁服务"固定成常量——那是控制变量手段，
不反映真实系统。用户裁定（2026-09-06）：真实系统的路由是实时的，且 TDR 上线后
余额分布端到端动态，路由输入本身就是变量。本实验把 PLAN_refactor_CN.md 锁定的
M3 coordinator 块循环真正跑起来，兼任 **M3 里程碑验收**。

**动态臂结构**（正式档）：
- 16 分片 anvil + 1 个 **routing coordinator 进程** + 50 个 `DynamicBrokerEngine` 进程。
- coordinator 每轮做五步：①消费 CreditRequest 代铸 mint 段（coordinator 账户单写者）；
  ②回收引擎回报（释放 sender 窗口、修正估计表、计数）；③读 min 分片块高推进到达
  （released = 块龄 × ctx_per_block，块驱动不用墙钟）；块变化时全量刷新余额估计表
  （50×16=800 次 RPC，由 32 个独立读手并行完成，耗时计入 refresh_ms）；④按到达序逐笔派发：估计表上
  `select_broker` 选 dst 余额够的 broker，无候选 ⇒ 随机指派 + 计 `no_qualified_est`；
  **每笔的 nonce 由 coordinator 串行预分配**随 CTX 下发；⑤收尾广播 END 哨兵。
- 引擎端本地终审：dst 账本够 ⇒ broker 路径；不够 ⇒ 自走 relay（burn 段用下发的
  nonce 签，mint 段请求代铸）。估计错配的代价**只是一次回退，不是资损**。
- 主臂 sender 窗口 `sender_window=2`：同一 (sender,src) 同时在途不超过 2 笔；
  H-D3 在相同 70 CTX/块下用 `window=1` 给出严格序列化下界。

**静态臂**：exp006 的完整路径（LPT 固定箱 + release 门 + mint coordinator），
同机同流同 seed，并与动态主臂共同使用 70 CTX/块探索档。

**当前参数（规模向 exp008 对齐，速率为待验证的探索档）**：每臂 40000 笔、16 分片、
50 Broker、200 用户、每 Broker 每分片 150 ETH、70 CTX/逻辑块、最大在途 24；主对照仅改变
“启动期静态指派”与“运行期动态路由”。

**判据（全实测；`summary.json → gates/hypotheses`）**：
- G1 全臂所有 CTX 成功落定；G2 动态臂 ctx 一一对应（无重无漏）；
  G3 逐 broker 链上 == 行账重算 + 全局净和 0 wei（**两臂都要过**——
  这是"估计-终审双层防线"的直接证据）；G4 动态臂回报完备（派发=落定=回报=接收）。
- **H-D1 动态化代价**：在 rate=70 上报告动态吞吐/静态吞吐比；原 0.8 门槛保留，
  即使未达到也应作为真实实验结论报告。
- **H-D2 两级判断差异**（不设阈）：分别报告 `no_qualified_est` 与
  `no_qualified_local`；前者是派发时无候选次数，后者是最终 Relay 回退次数。
  两个总数的差异不能直接当作逐笔估计错误率，后者需要另记逐笔预测与结果。
- **H-D3 窗口敏感性**（不设阈）：主臂 window=2 与探针 window=1 的吞吐差异。
- M3 验收附加：`<arm>/declared.csv` 的释放轨迹跨跑比对（回归门：同配置两连跑
  declared 序列一致）；`--set exp.max_backlog=50` 应触发看门狗中止且产物导出。

**运行**：
```bash
python -m brokerlab doctor --config experiments/exp007_dynamic_routing/config.yaml
python run.py --set exp.rates=25 --set exp.ctx_per_broker=4 --set exp.static_rates=   # 冒烟
python run.py                                            # 正式：静态/动态@30 + window=1 探针
python run.py --set exp.max_backlog=50                   # 看门狗演示（预期中止）
```
正式三臂顺序运行仅释放交易的理论下限约 29 分钟；但 70 档可能超过动态路由和
window=1 探针处理能力，触发积压或超时，不能把较短运行时间当成通过验收。

**恢复验证（2026-09-22）**：`run.py` 从 Downloads 项目源码恢复后，以 2 Broker、
2 分片、每臂 4 笔完成静态/动态/窗口探针冒烟测试；三臂均成功、所有 gate 通过。
此结果只证明脚本可运行，不能替代当前 40,000 笔正式配置的验收。

**产物**：每臂目录 `<arm_tag>/{ctx_rows.csv, coordinator.json, declared.csv, logs/}`；
顶层 `assignment.csv, summary.json, figs/exp007_dynamic.png`：

- 图 (a) `Throughput by Routing Strategy`：横轴为静态指派和动态路由，
  纵轴为每秒完成的跨片交易数；两者均使用 70 CTX/逻辑块。
- 图 (b) `Broker Availability and Relay Fallback`：横轴为 coordinator 估计和 Broker
  本地终审两个判断阶段，纵轴为交易数；前者表示估计中找不到合格 Broker 的次数，
  后者表示最终实际改走 Relay 的次数。两者是不同口径，不能相减当作错误笔数。
- 图 (c) `Released vs Completed`：横轴为相对实验起点经过的逻辑块数，
  纵轴为累计交易数；比较按块释放的输入与已经完成的交易，二者间距就是系统积压。

**120 档失败档案**：`out/20260921_130031/` 的动态臂只派发 18955/40000 笔，
其中 87 笔失败；积压峰值 20045 超过 20000 阈值后中止，G1/G2/G4 不通过。
静态臂同速率完成 40000 笔。原因是动态路由本轮约 39.87 CTX/s 的实际处理速度
不足以追上 120 CTX/逻辑块的释放速度；不应以调大 `max_backlog` 掩盖问题。

**效度声明**：
1. 估计表是"陈旧 ≤1 块的 RPC 读 + rep 增量修正"，全量刷新会瞬时丢弃在途派发笔的
   乐观修正——方向性后果只是多几次终审回退，由 G3 兜底。
2. coordinator 单进程五合一（路由+刷新+代铸+回收）：它本身就是 M3 设计里的委员会代位，
   refresh_ms 与派发预算暴露它的负载水位；若它先饱和，H-D1 归因写明。
3. 动态路由的决策**不再是速率不变量**（与 exp006 静态形成对照）：块内竞态使
   est 输入随落块历史变化——这是真实匹配的本征属性，不是 bug。
4. 6 核共享机：吞吐绝对值连档位声明；跨会话比较一律以同会话双臂为准。

**历史结果（旧参数，仅作记录，不得与新正式档混用）**：`out/20260907_005259/` +
`out/20260907_011522/`（4 分片、10000 笔、旧速率档，连跑两轮，
N=10000，主臂 window=2，探针 w=1；单轮 ≈ 23 分钟，双轮共 46 分钟）。

| 方案 | 墙钟 | 吞吐 | served/relay | 失败 | 净和 |
|---|---|---|---|---|---|
| stat_80 | 135.0/134.6 s | 74.1/74.3 CTX/s | 6962/3038 | 0 | 0 wei |
| stat_160 | 73.8/73.6 s | 135.6/135.9 CTX/s | 6962/3038 | 0 | 0 wei |
| dyn_40 (w2) | 340.7/341.9 s | 29.4/29.3 CTX/s | 7304/2696 | 0 | 0 wei |
| dyn_80 (w2) | 216.7/214.7 s | 46.1/46.6 CTX/s | 7280/2720 | 0 | 0 wei |
| dyn_160 (w2) | 168.3/159.9 s | 59.4/62.6 CTX/s | 7209/2767 | 0 | 0 wei |
| dyn_80 (w1) | — | 31.5/32.5 CTX/s | — | 0 | 0 wei |

- **M3 declared 回归门：PASS**（5 方案 × 2 轮：释放单调、整块×rate 对齐、放满 N；
  两轮中段逐元素一致）。到达轨迹与墙钟无关，审计 F1 的回归验证正式入账。
- **G1-G4 全过**：所有臂 fail 0、全局净和 0 wei、逐 broker 链上==行账；
  动态派发无重无漏（G2）、回报完备（G4）。
- **H-D2 终审防线有效**：约 27% 的 CTX 最终走 relay（nq_local≈2700，
  coordinator 代铸数与之相等）；这不等于 27% 都是估计误判，守恒仍为 0 wei。
- **H-D1 NOT MET（真结论，非 bug）**：动态/静态吞吐 = 0.62（rate80）、0.44（rate160），
  两轮复现。排除项：循环节流修复（块高每 0.15s 一读）、刷新并行化（8 读手）、
  window 2 解除 174-sender 结构上限——剩余损失归 **coordinator 单进程事件循环**
  （派发/回收/块高/代铸全汇于一环）与 relay 改判的额外往返。这正是 M3 设计里
  "委员会是聚合点"的镜像，也是 TDR（M4）要间接缓解的：dst 有钱 ⇒ 改判少 ⇒ 往返短。
- **对上述归因的后来修正（exp003，2026-09-07）**：同流补上 TDR 后 relay 27%→0.5%，
  吞吐却不升反微降（42.6 vs 46.1 CTX/s，0.92×）。⇒ relay 往返不是动态吞吐损失的主因，
  主因是 coordinator 单环本身。TDR 的价值在服务率（成交 7286→9947），不在吞吐。
  归因从"relay 往返 + coordinator 环"收窄为"coordinator 环"（详见 exp003 README）。
- **H-D3 窗口敏感性**：w2/w1 = 1.44-1.47——窗口确实兑换并发，但 w2 仍远低于静态，
  证明瓶颈在 coordinator 而非窗口。
- 看门狗试验：`--set exp.max_backlog=50` 触发中止、产物导出、无孤儿进程。
- 附注：本轮 summary 的 H-D1 字段抓错窗口（取 w1 探针算比值），已修代码并按
  上表离线重判；各方案原始测量未动。

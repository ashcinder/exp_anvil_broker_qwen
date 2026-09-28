# exp007 · 动态路由（M3 核心上线）—— 静态指派 vs coordinator 实时匹配

**背景**：exp005/006 用启动期 LPT 装箱把"谁服务"固定成常量——那是控制变量手段，
不反映真实系统。用户裁定（2026-09-06）：真实系统的路由是实时的，且 TDR 上线后
余额分布端到端动态，路由输入本身就是变量。本实验把 PLAN_refactor_CN.md 锁定的
M3 coordinator 块循环真正跑起来，兼任 **M3 里程碑验收**。

**动态臂结构**（每个速率档）：
- 4 分片 anvil + 1 个 **routing coordinator 进程** + 50 个 `DynamicBrokerEngine` 进程。
- coordinator 每轮做五步：①消费 CreditRequest 代铸 mint 段（coordinator 账户单写者）；
  ②回收引擎回报（释放 sender 窗口、修正估计表、计数）；③读 min 分片块高推进到达
  （released = 块龄 × ctx_per_block，块驱动不用墙钟）；块变化时全量刷新余额估计表
  （200 次 RPC，串行，耗时计入 refresh_ms）；④按到达序逐笔派发：估计表上
  `select_broker` 选 dst 余额够的 broker，无候选 ⇒ 随机指派 + 计 `no_qualified_est`；
  **每笔的 nonce 由 coordinator 串行预分配**随 CTX 下发；⑤收尾广播 END 哨兵。
- 引擎端本地终审：dst 账本够 ⇒ broker 路径；不够 ⇒ 自走 relay（burn 段用下发的
  nonce 签，mint 段请求代铸）。估计错配的代价**只是一次回退，不是资损**。
- sender 窗口 `sender_window=1`：同一 (sender,src) 同时在途 ≤1 笔——跨 broker 派发时
  消除 anvil mempool 的 nonce 缺号互卡。它是"一致性↔在途深度"的旋钮，
  H-D3 用 window=4 量化这个旋钮的代价/收益。

**静态臂**：exp006 的完整路径（LPT 固定箱 + release 门 + mint coordinator），
同机同流同 seed，同会话直接对照。

**判据（全实测；`summary.json → gates/hypotheses`）**：
- G1 全臂所有 CTX 成功落定；G2 动态臂 ctx 一一对应（无重无漏）；
  G3 逐 broker 链上 == 行账重算 + 全局净和 0 wei（**两臂都要过**——
  这是"估计-终审双层防线"的直接证据）；G4 动态臂回报完备（派发=落定=回报=接收）。
- **H-D1 动态化代价有界**：rate ∈ {75,150} 上 动态吞吐 ≥ 静态的 80%。
- **H-D2 陈旧性证据**（不设阈）：`no_qualified_local` > 0 证明估计确会被现实推翻，
  但 G3 在存在推翻时仍全 True。
- **H-D3 窗口敏感性**（不设阈）：window=4 对 window=1 的吞吐比。
- M3 验收附加：`<arm>/declared.csv` 的释放轨迹跨跑比对（回归门：同配置两连跑
  declared 序列一致）；`--set exp.max_backlog=50` 应触发看门狗中止且产物导出。

**运行**：
```bash
python -m brokerlab doctor --config experiments/exp007_dynamic_routing/config.yaml
python run.py --set exp.rates=25 --set exp.ctx_per_broker=4 --set exp.static_rates=   # 冒烟
python run.py                                            # 正式：静态2档+动态3档+窗口探针
python run.py --set exp.max_backlog=50                   # 看门狗演示（预期中止）
```
正式跑 ≈ 20–25 分钟（动态 rate=25 档最慢：10000 笔 ÷ 25/块 ≈ 400 块释放期）。

**产物**：每臂目录 `<arm_tag>/{ctx_rows.csv, coordinator.json, declared.csv, logs/}`；
顶层 `assignment.csv, summary.json, figs/exp007_dynamic.png`
（(a) 静态vs动态吞吐 (b) est/local 两级 no_qualified (c) declared 阶梯 vs 逐块落定曲线）。

**效度声明**：
1. 估计表是"陈旧 ≤1 块的 RPC 读 + rep 增量修正"，全量刷新会瞬时丢弃在途派发笔的
   乐观修正——方向性后果只是多几次终审回退，由 G3 兜底。
2. coordinator 单进程五合一（路由+刷新+代铸+回收）：它本身就是 M3 设计里的委员会代位，
   refresh_ms 与派发预算暴露它的负载水位；若它先饱和，H-D1 归因写明。
3. 动态路由的决策**不再是速率不变量**（与 exp006 静态形成对照）：块内竞态使
   est 输入随落块历史变化——这是真实匹配的本征属性，不是 bug。
4. 6 核共享机：吞吐绝对值连档位声明；跨会话比较一律以同会话双臂为准。

**最近结果**：`out/20260907_005259/` + `out/20260907_011522/`（连跑两轮，
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
- **H-D2 终审防线有效**：约 27% 的 CTX 被终审改判 relay（nq_local≈2700，
  coordinator 代铸数与之精确相等），改判洪流下守恒依然分毫不差。
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

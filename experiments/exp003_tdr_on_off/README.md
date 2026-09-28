# exp003 · TDR 开/关对照（M4 验收实验）

调度策略的完整搜索史见同目录 [EXPLORATION_log_CN.md](EXPLORATION_log_CN.md)。
它记录每次尝试、证据、结论与作废原因。新探索从那里续写。

**测什么**：TDR 第一次在真链上闭环。同一条对齐 exp008 的 40000 笔流、
同一个路由 coordinator，两个方案各起一条全新 16 分片链：

- `plain`：无 TDR —— dst 耗尽 → 终审改判 relay（exp007 量到 ~27%）。
- `tdr`：每个 broker 引擎挂 `TdrAgent` + `TdrDynamicEngine`：
  需求滑窗（w 块，路由时刻记账，被 relay 的也算——修复审计 F3 的需求盲区）
  → τ 比例目标（q_min 地板）→ excess_only 死区触发 → 转移两段落链：
  第一段 broker@src→BURN（自签），第二段 coordinator 代铸到 broker@dst。
  钱经"销毁+铸造"搬回枯竭分片，全程无 setBalance（审计 F2 红线）。
  事件互斥 + χ 块间隔 + 超时不重试（lost_in_transit 显式入账，论文假设 C4）。

**判据**：
- gates（两方案都要过）：G1 全落定零失败；G2 ctx 无重无漏；
  G3a 逐 broker 链上 == 本地账本（**期望值含 TDR 搬运项**）；
  G3b 全局净和 0 wei；G4 coordinator 派发/回报完备；
  burn≡mint 台账：ΔBURN == Σrelay v + Σtdr 已确认 burn（moves+lost）== minted_wei。
- **H-T1 TDR 有效**：事件数 > 0、搬运量 > 0、且 tdr 的终审改判数 < plain。
- **H-T2 守恒**：gates 全 True（TDR 洪流下依旧分毫不差）。
- **H-T3 代价**（证据不设阈）：吞吐比、e2e p95 比、coordinator 代铸增量。

**运行**：
```bash
python -m brokerlab doctor --config experiments/exp003_tdr_on_off/config.yaml
python run.py --set exp.ctx_per_broker=2 --set exp.rate=50 --set exp.pool_scan=1500   # 冒烟
python run.py                                        # 正式：两方案各 40000 笔，≈20–25 分钟
```

**产物**：每方案 `out/<ts>/<arm>/{ctx_rows.csv, tdr_moves.csv, coordinator.json, logs/}`；
顶层 `summary.json`、`figs/exp003_tdr.png`（(a) relay 回退数 (b) TDR 链上段数
(c) 端到端延迟 ECDF）。

**效度声明**：① 需求窗口在 settle 时按 Θ1 提交块高回看（内容等价于路由时刻，
过期滑出以锚点计）；② TDR 的 τ 基于本地 available（不含在途进账，保守）；
③ 两方案同流同 seed，唯一变量是引擎是否挂 TDR；④ 本机 6 核，吞吐绝对值连档位声明。

**吞吐的记账口径（本实验定稿）**：wall 拆两段——`wall_ctx`（全部 CTX 落定，
取引擎最后落定时刻）与 `tdr_close_tail_s`（TDR 搬运的收尾时长）。
CTX 吞吐比是"TDR 是否拖累主业务"的答案；收尾时长是"TDR 后台活动多持久"的答案。
把两者混成一个 wall 会得出假的"吞吐暴跌"（dev 阶段实际踩过并修正）。

**第二段超时的归属（设计决定）**：状态机不主动判 mint 段超时——回执必达
（coordinator 有 relay_timeout 兜底，最迟显式回 0）。若状态机抢先记失、
引擎后收到成功回执，同一笔钱会被双记（一次 done 一次 lost）——账本当场失真。
"超时"与"链上事实"分两层，谁观察谁记账。

**历史结果**（2026-09-07，旧验证档）：两方案各 10000 笔，rate=80、
4 分片、50 引擎进程、seed=42。下表不是新的 exp008 对齐参数结果。

| 指标 | plain | tdr |
|---|---|---|
| 成交(主链服务) | 7286 | 9947 |
| 终审改判 relay | 2714 (27.1%) | 53 (0.5%) |
| TDR 事件 / 搬运转账 | — | 773 / 2294 |
| TDR 搬运量 | — | 104764 ETH |
| CTX 吞吐 | 46.75/s | 42.63/s |
| e2e p95 | 2.079 s | 2.133 s |

**判据**：gates（两方案 G1-G4 + burn≡mint 台账）全 True；失败 0、拒答 0、净和 0 wei、
逐 broker 账实 0 失配；tdr 臂 transfers_lost=0、lost_in_transit=0。
**H-T1 有效**：改判从 27.1% 压到 0.5%，搬运量 >0。
**H-T2 守恒**：TDR 洪流（773 事件 / 10.5 万 ETH 过账）下守恒门依旧分毫不差。
**H-T3 代价**：CTX 吞吐比 0.912（=8.8% 让利，收尾时长 22.3 s）；e2e p95 比 1.026（+2.6%）。
判读：TDR 不拖累主业务路径太多，代价集中在收尾时长。
但吞吐没向 static 档恢复（≈0.57×static）——瓶颈在 coordinator 单环，不在 relay。

与 exp007 的串联：exp007 量到 27% 改判与动态 0.44–0.62× 代价，当时怀疑改判往返是主因。
此处同流补上 TDR：relay 27%→0.5%，吞吐却不升反微降（0.912×plain）。
⇒ 动态吞吐损失不是 relay 造成的，coordinator 单环就是瓶颈（印证 exp007"须扩环"判定）。
TDR 的价值在服务率：成交 7286→9947、relay 2714→53，守恒与账实一条不破。
"补液解决改判、扩环解决吞吐"——这是 M6 之前的两条独立线索。

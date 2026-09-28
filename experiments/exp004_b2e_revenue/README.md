# exp004 · B2E 手续费收入与守恒恒等式（off / on 对照）

**测什么**：B2E（Broker2Earn，broker 手续费收入机制）第一次在链上立账。
同一条真实 trace 多数方向流，两个方案各跑一遍：

- `b2e.off`：现状基线——费用不生效，机制层走 B2E 之前的旧路径（回归不变式）；
- `b2e.on`：用户每笔付全额 F（F = gas_units × 名义 gas 单价）。
  broker 服务时，βF 随 Θ1a 进 broker，(1−β)F 由一笔额外小额交易 Θ1b 烧掉。
  relay 回退时，v+F 全额进 BURN，broker 分文不进。

机制定义与决策记录见 [../../PLAN_b2e_CN.md](../../PLAN_b2e_CN.md)（v2 定案）。

**判据（全部链上实测；退出码 0 ⇔ 全部成立）**：

- I1 全局净和 = 0 wei（含 BURN；gasPrice=0 故要求精确）。
- I2 broker 链上总差分 ≡ Σ_served βF；I2b 逐 broker 同样成立（收入审计凭据）。
- I3 BURN 链上增量 ≡ Σ_relay v + Σ全程 fee_burn（燃烧台账闭环）。
- 费用完整性：served 行 βF+(1−β)F ≡ F；relay 行烧掉的正是全额 F。
- 段数：on 方案的 Θ1b 条数 == served 数；off 方案每行仍 2 段、费用列全零。
- 无失败 CTX。

**为什么单价提档**：默认 1 gwei 时 F=2.1e-5 ETH，收入小到不可读。
config 显式提到 100 gwei（F=2.1e-3 ETH）。机制与单价无关——
β 敏感性表（链上外推算术）在 summary 里一并给出。

**与 exp001/002 的对照口径**：off 方案即 exp002 构造的 4-broker 版
（匹配分流在这里第一次真正生效）。on 方案相对 off 方案的增量 =
每笔 served 多一条 src 本地小额交易。这条不进跨片通信账（跨片账单仍按
exp001 口径）。βF 留在 src 子账户 ⇒ 不改变 dst 侧耗尽动态，
两个方案的路由序列应一致——这本身是本实验的隐含检验。

**运行**：

```bash
python -m brokerlab doctor --config experiments/exp004_b2e_revenue/config.yaml
python run.py                                  # 两个方案合计 ≈ 4 分钟
python run.py --set exp.count=6 --set exp.strategies=on   # dev 冒烟
```

**产物**（`out/<时间戳>/`）：每方案 `b2e_trace.csv`（含 fee/legs/t1b_block 列）+
logs + pids.json；顶层 `summary.json`（三恒等式、per-broker 收入、
β 敏感性表、恒等式判定）；`figs/exp004_revenue.png`
（(a) 累计收入阶梯 (b) 逐 broker 收入/服务数 (c) 两方案链上段数对照）。

**最近结果**：`out/20260904_095954/`（2026-09-04，count=60，F=2.1e-3 ETH，β=0.10）。

60 笔定向流（S1→S0，合计 156.69 ETH；dst 资金池 4×30=120 ETH ⇒ 必然触发回退），
两方案全部判据通过、0 失败 CTX：

| 量 | b2e.off | b2e.on |
|---|---|---|
| served / relay | 50 / 10 | 50 / 10（逐笔路由序列一致） |
| 首次回退 i | 36 | 36 |
| broker 收入 ΣβF | 0 | **0.010500 ETH**（=50×0.00021） |
| BURN 增量 | 37.994110 ETH（=Σ_relay v） | 38.109610 ETH（+relay 段 0.021 +served 烧币 0.0945） |
| 链上段数 served 每笔 | 2 | 3（Θ1b 共 50 条，== served 数） |
| e2e 均值 | 1.209 s | 1.912 s（+0.70 s = 多等一个确认周期） |

- 恒等式：I1/I2/I2b/I3/费用完整性 两方案全 True；全局净和 0 wei。
- 隐含检验成立：费用不改变路由——两方案 served/relay 与首次回退逐笔相同
  （βF 只进 src 侧，匹配只看 dst 余额，机制与政策分层在此得到实证）。
- per-broker 均匀分流首测（num_brokers=4）：served 18/10/12/10，
  收入严格等于各自服务数 ×βF，四者链上差分 ≡ 行账收入（I2b）。
- 与 exp001 对照：跨片通信账单不变（Θ1b 是本分片小额交易，不进账单）；
  链上足迹每笔 served +1 段——B2E 的成本是本地段数与一个确认周期，
  不是跨片带宽。
- β 敏感性（同流外推，off-chain 算术）：β=0/0.10/0.25/0.50/1.0 ⇒ 收入
  0/0.0105/0.02625/0.0525/0.105 ETH。线性成立，floor 舍入误差 <1e-18 ETH。
- exp002 回归门：b2e 默认关闭下重跑 exp002（`out/20260904_100309/`），
  确定性列（ctx_id/route/broker_id/ok/amount/余额）与 024358 逐位一致；
  仅 block 号、e2e、xmit_bytes 三类天然抖动列浮动（签名的 RLP 长度随
  ECDSA 随机数变化，非行为改变）。C1–C4 判定全部再次通过。

# TDR 定稿方法（Final Method）· 设计文档与数学

**用途**：本文描述 exp008 定稿的调度方法，替代手稿
（`submit/main.tex` §IV Phase1-4 与 §V 部分小节）中已过时的算法描述。
记号与手稿一致：$\mathbb S$ 分片集、$n=|\mathbb S|$、$\beta_s$ 子账户余额、
$\rho=\sum_s\beta_s$、$\lambda_s$ 目的需求、$\tau_s$ 目标、$\varepsilon$ 死区、
$\chi$ 事件间隔、$\eta^t$ 服务率、relay 与 CTX 保留英文。
实验依据全部标注产物目录或探索档案（EXPLORATION_log_CN.md）章节。

**一句话总览**：定稿 = 手稿的"目标分配 τ"原样保留；
需求估计由滑窗改为 EWMA；执行规则由"全量重排"改为"深死区只补缺口"。

---

## 1 与手稿的差异清单

| 手稿位置 | 手稿内容 | 定稿 | 依据 |
|---|---|---|---|
| §IV-A Phase1 | 滑窗求和 λ_s = Σ v(R)（式 eq:demand） | EWMA 衰减率外推（本文 §3） | 档案 §5.1：同参数 relay 208→90、搬量减半 |
| §IV-B Phase2 | 比例目标 + q_min 地板 + Στ=ρ（式 eq:target…eq:minimum_target_adjustment） | **原样保留** | 与 Go 参考逐语义一致；档案 §18 证实 q_min 不可省 |
| §IV-C Phase3 | 双边触发 ε=0.1；触发后全部 gap 参与；执行后精确回到 τ | 深死区 ε=0.95；仅 D/E 两集参与；**部分填充**（性质 T3） | 档案 §10/§20；本文 §5 |
| §IV-C 末段 | 转账走"emulator 直连 relay 路由" | 两段落链：burn 自签 + coordinator 代铸（TDR ref 命名空间） | DESIGN 文档 §3/§6 |
| §IV-D 原型局限 | "延迟确认/重启/双路选同一资金尚未实现/共享" | **已实现**：账本单一视图、confirm-before-fail、迟到回执回冲、lost 显式入账 | tests 90 项；exp003 gates |
| §V-C 触发阈例 | 用 ε=0.1 双边例证明"只多必漏" | 例仍成立；但定稿的 ε 是**双重角色**（触发+资格），且 ε→1 有退化悬崖（T4） | 档案 §9 |
| §V-B 搬量式 | $M_e=\frac12\lVert\beta-\tau\rVert_1$（等号） | 定稿为**不等号上界**（T3），另有工作负载下界（T5） | 推导变化，见 §6 |
| §V-E 容量界 | Prop 1 公式 | **逐字保留**：每事件半交易 ≤2(n−1)、提交率 1/χ 均未被定稿放宽 | 性质 T2/T7 |
| §V-H 盈亏平衡 | κΔν > μν_r | 公式保留；μ 接入未实现（B2E 未挂动态管线），列为待做 | 报告 §11 缺口 2 |

---

## 2 方法总纲（每 broker、每块一次的循环）

```
观测：每笔经手 CTX（成交/revert/改判 relay 同计）
      → observe(submit 块高, dst, v)   进 EWMA
规划（状态机 IDLE 且 χ 满足时）：
      λ̂ ← EWMA 读出；  τ ← 分配(β, λ̂, q_min)   冻结本事件
      D, E ← 深死区集合(β, τ, ε)
      T  ← 贪心匹配(D, E)                |T| ≤ n−1
执行：每笔转移两段真实交易：
      第一段 broker@src → BURN（自签，先 reserve 后签）
      第二段 coordinator 代铸 → broker@dst（回执闭环）
记账：confirm/fail/timeout/迟到回冲；lost_in_transit 显式入账
```

全本地输入：链上余额查询 + 自身经手的 CTX 记录。无外部提示。

---

## 3 Phase 1′：EWMA 需求估计（替换滑窗）

维护每分片速率 $r_s$（ETH/块）。设观测发生在块 $b$，上次更新在 $b'$，
半衰期 $h_\ell$（块）：

$$r_s \leftarrow r_s \cdot 2^{-(b-b')/h_\ell} \;(\forall s),
\qquad r_{s_{\rm dst}} \mathrel{+}= v .$$

规划读出（$h$ 为当前块）：

$$\hat\lambda_s(h) = \bigl\lfloor r_s \cdot 2^{-(h-b')/h_\ell} \cdot h_\ell \bigr\rfloor .$$

要点：
- 与滑窗的本质区别：一笔需求的影响是**几何衰减**，不存在"滑出即清零"。
- $\hat\lambda$ 只以比例进入 τ（式 eq:target 归一化抵消量纲），
  故外推因子 $h_\ell$ 是尺度而非形状。
- 冷启动门槛：见过 ≥5 个不同块才允许规划（代替"满窗"）。
- 记账位置：按 CTX 的 Θ1 提交块高回填（路由时刻语义，含未服务需求）。
  这是审计 F3 修复后的口径，与手稿式 eq:demand 的集合 $\mathcal R_w^h$ 同语义。
- 整数域：$r_s$ 用 float、读出取整；确定性可复现（tests）。

**实验依据**：正式档同负载把"硬窗 ε0.95"换为"EWMA ε0.95"，
搬量 211k→164k（−22%）；relay 17→20（噪声带内）。矩阵档
硬窗 ε0.6→EWMA ε0.6：relay 208→90。抖动消除来自 τ 不再随窗口滑动突变。

---

## 4 Phase 2′：目标分配（与手稿相同，原样引用）

比例目标 $\widetilde\tau_s = \rho\,\lambda_s/\textstyle\sum\lambda$；
地板 $\tau_{\min}=q_{\min}\rho/n$ 及回收式 eq:minimum_target_adjustment；
整数化：向下取整、余数给最大 λ（平局取最小分片号）；
reconcile 残差归最大 τ。性质：

$$\sum_s \tau_s = \rho , \qquad \tau_s \ge \lfloor\tau_{\min}\rfloor , \qquad
\textstyle\sum\lambda=0 \Rightarrow \tau=\beta .$$

实现与 Go `pkg/tdr/allocation.go` 逐行对齐（BPS 定点），
交叉样例测试在 tests/test_tdr_policy.py。

**q_min 不可省（新增实验结论）**：$n$=16、25% 流量去冷分片。
q_min=0 使冷分片 τ=0、零缓冲，relay 由 20 恶化到 131；
q_min=0.05 居中（47）。手稿的地板设计在本负载下是**冷分片保险**，
不是可有可无的装饰。

---

## 5 Phase 3′：深死区只补缺口（topup，替换全量重排）

记 $\delta_s=\beta_s-\tau_s$。定稿定义两个**资格集合**（ε=0.95）：

$$D=\{s:\ \beta_s < (1-\varepsilon)\,\tau_s\}\quad(\text{深缺口，待补}),$$
$$E=\{s:\ \beta_s > (1+\varepsilon)\,\tau_s\}\quad(\text{深盈余，可出}).$$

匹配：D、E 各按 |gap| 降序，双指针取
$\Delta=\min(\beta_{E_i}-\tau_{E_i},\ \tau_{D_j}-\beta_{D_j})$，
逐笔转账、gap 清零即进位。每笔转账后至少一个头退出，故

$$|T|\ \le\ |D|+|E|-1\ \le\ n-1 \qquad(\text{Lemma 1 保留，T2}).$$

与手稿 Phase3 的三个差别：

1. **资格带**：手稿里 $|\delta|>\varepsilon\tau$ 只管触发，触发后全部 gap 参与；
   定稿里 ε 同时限定"谁有资格被补/被抽"。带内分片完全不动。
2. **只抽到 τ**：E 方最多出 $\beta-\tau$（不是 $\beta-\tau(1+\varepsilon)$），
   补完即停；D 方补到 τ 为止。**盈余闲置是特性不是缺陷**——它省掉
   proportional 的"把富余分片刮干净"造成的往返。
3. **部分填充**：E 不够时 D 只补一部分；执行后不保证 $\beta'=\tau$
   （手稿 Phase3 的"精确回到 τ"陈述对定稿不成立，须删改）。

**ε 的双重角色与数值证据**（矩阵档，EWMA 口径，档案 §9/§10）：

| ε | 0.2 | 0.5 | 0.7 | 0.9 | 0.95 | 0.98 | 1.0 |
|---|---|---|---|---|---|---|---|
| relay | 692 | 555 | 538 | 317 | 168 | 195 | 3987 |
| 搬量 | 93k | 81k | 78k | 54k | 35k | 30k | 0 |

三指标随 ε 单调改善直到悬崖。平台 [0.95, 0.98]，取 ε=0.95
（距失效点 1.0 留 0.05 余量，且无一项更差）。

---

## 6 性质与定理

**T1（封闭池守恒）** 每事件 τ 只重排 $\rho$：
$\sum_s\tau_s^{h}=\rho^{h}$；转账两段的销毁与铸造对每 broker 净值零和。
broker 总资金的变化只来自服务进出与手续费（若启用），不来自 TDR。

**T2（每事件转账数上界）** 同手稿 Lemma 1（证明逐字适用，
集合换成 D/E）：$|T|\le n-1$；每事件半交易 ≤$2(n-1)$。

**T3（搬量上界，替代手稿等式）** 事件搬量
$M_e=\sum_{\mathcal T_e}\Delta \le \min\!\bigl(\sum_{s\in D}(\tau_s-\beta_s),\ \sum_{s\in E}(\beta_s-\tau_s)\bigr) \le \tfrac12\lVert\beta-\tau\rVert_1$。
等号当且仅当 D/E 覆盖全部正负 gap（ε=0 退化回全量重排）。
**手稿式 eq:moved_volume 的等号对定稿不成立，须改为不等号**，
其"匹配顺序不改变 $M_e$"结论也随之失效——定稿的 $M_e$ 由资格带决定。

**T4（退化与等价端点）**
- ε ≥ 1：$D=\{s:\beta_s<0\}=\varnothing$（余额非负），永不触发，
  方法退化为 plain。实测悬崖：ε=1.0 时 relay 爆回 plain 水平（3987/10000）。
- ε = 0：D/E 覆盖全部缺口/盈余，topup 等价于全量重排（proportional 行为）。
  即手稿方案是本方法 ε=0 的端点特例——论文可正面利用这层关系。
- 推论：ε 的可用域是开区间 [0,1)，定稿取近右端点。

**T5（工作负载依赖的搬量下界，估计式非定理）** 单向流下
$M \gtrsim \underbrace{B\cdot(\tau_{8}-\beta_8^0)}_{\text{一次性建仓}} + \underbrace{\textstyle\sum_{h}(\text{dst8 净流出})}_{\text{持续排水}}$。
正式档数值 82k+82k≈164k ETH；定稿实测 163.7-170k ≈ 下界本身。
含义：搬量维度已无可优化空间；proportional 的 813k 是下界的 5 倍。

**T6（账本不变量，防双花）** 服务与 TDR 共用同一视图
$\text{available}=\text{confirmed}-\text{reserved}$；
在途进账不计入（手稿 §IV-D 指出的"两条路选同一钱"缺口在本实现已闭合：
reserve 原子性 + LedgerError 断言 + tests 回归 F5 场景）。
单写者：TDR 首段用 broker 本地 nonce；一箱一主。

**T7（系统容量界）** 手稿 Prop 1 与逐分片界（式 eq:overhead、
eq:per_shard_overhead）**原样成立**：其前提是 Lemma1 上界（T2 保留）
与每 broker 每 χ 块 ≤1 事件的提交率（状态机强制，实测事件 137-169 场 ≪ 上限）。
数字例可更新为实测口径：B=50、n=16、χ=10 ⇒ 系统均值界
$\le 2\cdot15/16\cdot 50/(\Gamma\cdot10)$，Γ=2000 时 ≈0.47%。

**T8（守恒恒等式，wei 级）** 每场运行满足
$\Delta\text{BURN} = \sum_{\rm relay} v + \sum_{\rm TDR\ 已确认} a = \sum_{\rm mint} a$，
且全局净和 = 0 wei、逐 broker 链上 == 本地账本（含搬运项）。
这是 gates G1-G4 + burn≡mint，正式档全 True。

---

## 7 事件生命周期（替换手稿 Phase 4 与"原型局限"段）

状态机：IDLE →(规划出 T≠∅ 且 χ 满足)→ EVENT_ACTIVE →(每笔：
首段提交→首段确认→请求代铸→回执)→ 全部终态 → IDLE。

- **互斥**：同 broker 至多一个活跃事件（手稿一致）。
- **χ 计时**从事件起点块高；实测远未 bind（正式档事件数 137-169）。
- **超时不重试**：首段超 deadline 判失，量记入 lost_in_transit_wei 显式入账。
- **第二段不设主动超时**：代铸回执必达（coordinator 兜底显式回 0）。
  状态机抢先记失会与成功回执双记，账本失真（设计决定，踩坑后定）。
- **confirm-before-fail**：判超时前最后探针一次，防把慢落定误判为缺失。
- **迟到回冲**：判失的 mint 若稍后落定，账本追链回冲（recover_lost）。
- 手稿中"原型未实现重启恢复/延迟确认"两句在本文平台**已由以上四点覆盖**，
  应改写为实现描述而非局限声明（重启持久化仍未做，保留为边界）。

块高来源：引擎轮询自身分片取 min 单调化（无全局时钟），
χ/滑窗/deadline 全部按块计——审计 F1（墙钟污染）结构性不可能。

---

## 8 定稿参数表

| 符号 | 值 | 数学角色 | 实验依据与敏感度 |
|---|---|---|---|
| $h_\ell$ | 20 块 | EWMA 半衰期（记忆尺度） | 12-40 弱敏感（relay 193→224）；取 20 |
| $\varepsilon$ | 0.95 | 资格带 + 触发深度 | 强敏感，[0.95,0.98] 平台，1.0 悬崖 |
| $q_{\min}$ | 0.10 | 冷分片地板 | 0 恶化 2.7 倍；0.1 合理 |
| $\chi$ | 10 块 | 事件最小间隔 | 实测不 bind；保留为安全阀 |
| timeout | 20 块 | 首段超时 | 全档 lost=0 |
| 冷启动 | ≥5 块观测 | EWMA ready | 防 τ 未成形先动 |

正式档最终成绩（40000 笔、16 分片、两场均值，exp008 report.json）：
relay 中位 20 [8..32]（0.05%）；搬运 1227 笔；搬量 163.7k ETH ≈ T5 下界；
TDR 段占比 3.1%。对照：plain 24848、valve 20863、proportional 110
（但搬量 813k、段占比 57%）。

---

## 9 手稿改稿映射（逐节）

- §IV-A：替换为本文 §3；eq:demand 改为 EWMA 更新式；
  删除"窗口大小权衡留待 §VI 分析"的悬空承诺（已做完）。
- §IV-B：保留公式块；补一段 q_min 的实验角色（T 冷分片保险，§4）。
- §IV-C：eq:two_sided_trigger 改为 D/E 资格集合定义；
  "全部 gap 参与/精确回到 τ"改为部分填充（§5 三点差别）；
  eq:moved_volume 等号改不等号（T3）；fig3 示例建议换新数值。
- §IV-D：删两段原型局限，替换为本文 §7 的四条机制。
- §V-C：触发阈例保留作"浅死区不足"的历史动机；新增 ε 悬崖与端点性质（T4）。
- §V-E/G：Prop 1 逐字保留；数字例可更新（T7 末段）；
  逐分片界与"报告最大单分片负载"的呼吁 → 由"段占比"指标部分兑现，
  blockscan ground-truth 仍是待做（报告 §11 缺口 1）。
- §V-H：break-even 公式保留；μ 接入是待做项（缺口 2），文字改"open"。
- §VI：Exp B/C/D/E 全部换成 exp003 探索 + exp008 生产数据（报告 §7/§8）；
  "单 broker 两分片初步"升格为 50 broker、16 分片、4 万笔、多场统计。
- 结论段：消融阶梯（T4 端点关系 + §5 证据）可作为核心贡献句：
  手稿方案是定稿 ε=0 端点特例；深死区+EWMA+topup 同负载下
  搬量降 80%、搬运次数降 95%、relay 再降 5 倍。

---

## 10 已知边界（必须随方法一起声明）

1. ε 贴崖：ε≥1 静默退化为 plain。部署参数留 ≥0.02 余量；
   工程上应对 ε 做输入校验（ε<1 否则报警）。
2. 优势场景依赖 dst 高集中 + 多分片 + 资本受限；
   低集中度（4 分片定向流）下傻瓜水位阀仍以 1/10 搬量占优。
3. 需求 regime-shift（热点换分片）未测；EWMA 跟踪速度与 q_min 缓冲
   在换向期的表现是下一个实验。
4. 搬运下界 T5 是**估计式**：依赖"单向流、无回流"的负载画像，
   双向流下会松。
5. break-even 未实证：TDR 尚未接入费用（μ 无常数），收益对比是缺项。
6. 逐分片区块占用是"段占比"代理，ground-truth 需 blockscan（M5 未做）。

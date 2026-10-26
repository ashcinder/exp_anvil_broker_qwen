# LAI(1)：替换 OPRE 的可核验分配基线

[正式会议论文](https://proceedings.mlr.press/v235/bhuyan24a.html)：Best of Both Worlds Guarantees for Smoothed Online Quadratic Optimization，Bhuyan、Mukherjee、Wierman，ICML 2024，PMLR 235:3850–3888。ICML 属于 [CCF 人工智能 A 类会议](https://www.ccf.org.cn/Academic_Evaluation/AI/zgjsjxhtjgjxshy/al/2017-04-25/592030.shtml)。2023 年 arXiv 初稿不作为发表年份；这里依据 2024 年正式长文。

正式版 39 页 PDF 保存为 `materials/lai_icml2024.pdf`，含完整证明及附录实验设置。原论文不是区块链论文，但“跟随变化需求、惩罚分配变动”适合做通用分配算法对照；领域贴合度低于 Horcrux。

## 原问题和决策式

论文第 3 页：需求目标为 v_t，分配为 x_t，固定正定矩阵 A。需求偏差代价是 (x_t-v_t)^T A(x_t-v_t)/2，调整代价是 ||x_t-x_(t-1)||²/2。

第 6 页 Algorithm 2：x_t = C_t x_(t-1) + (I-C_t)v_t；C_t^(-1)=2I+A-C_(t+1)。末轮每个特征值为 [1+a+a/2*((1+4/a)^(γ/2)-1)]^(-1)。

本实验使用论文数值实验的 γ=1，选择 A=aI、默认 a=1。此时系数是固定点 c=2/(a+2+sqrt(a²+4a))≈0.381966，故每次保留约38.2%旧分配、采用约61.8%新需求。不需要知道实验终止时刻。`lai_coefficients` 实现完整标量递推；`lai_step` 直接实现原更新式，测试核对 γ=0 末轮和 γ=1 固定点。

## 接入项目的部分——明确属于适配

1. 每个 broker 最近20逻辑块观测到的用户付款金额，按目的分片求和。包括转为 relay 的请求，因为它们仍代表需求；不读取未来观测。
2. 把金额归一化成比例，再混入5%均匀分配作为底仓。没有需求时不主动分配。参数由配置公开，未经结果调优。
3. 初始连续分配 x_0 为均匀比例。更新后的 x_t 保存在算法状态中，不拿实际链上余额重置它。A=aI 时插值本身保持非负、比例和为1，无需额外投影。
4. 以当前“已确认减预留”的资金总量乘比例，用最大余数法转换成整数 wei，确保总量精确守恒。从实际盈余搬到实际缺额，每事件最多 n-1 笔。
5. 使用与其他方法相同的冷却、活跃事件互斥、burn/mint 和确认逻辑。因此资金到达有延迟，实际持仓与连续动作可能不同。

原问题没有钱包交易消耗、固定每笔费用或链上确认延迟；不能把论文的最优性/竞争比直接套到本实验，也不能把其二次调整代价说成真实gas费。

## 原实验如何阅读和复现

先读正文第7–8页及附录G：10维连续动作，构造二次矩阵；比较 LAI、LAI(1) 和 ROBD，测试轻尾/重尾需求变化及混合随机—对抗环境。算法只需线性代数，不需神经网络训练。这里复现其决策规则，在统一分片环境重新实验；没有重跑论文全部原场景。源代码仓库尚未核验，不把本地代码称为作者原代码。

原文混合环境采用T=100、1000条样本轨迹，并固定对抗轮次集合。原文重复数与本项目的两轮pilot或三轮正式实验不同，不能混称。若要复核原论文图表，应使用附录G而非当前ETH金额及人工热点负载。

另一个候选为 [Fairness-Regularized Online Optimization with Switching Costs](https://papers.neurips.cc/paper_files/paper/2025/hash/1bd359b32ab8b2a6bbafa1ed2856cf40-Abstract-Conference.html)，NeurIPS 2025。FairOBD 全文公开，但还需定义长期公平正则、辅助变量及对偶更新；本项目并没有不同社会群体的公平目标。LAI 的需求/调整权衡更直接，原连续式无需求解器，因此优先采用LAI。

## 排除 OPRE

此前 OPRE 目标启发臂已从 `METHODS` 和任务配置中删除。旧检索材料保留在 `materials/opre_*.json`，旧笔记仅作排除依据；旧批次标记 SUPERSEDED，新五方法结果重新生成。

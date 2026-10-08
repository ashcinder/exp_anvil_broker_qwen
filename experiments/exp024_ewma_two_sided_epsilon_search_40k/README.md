# 实验 24：Clean 数据上的双侧 EWMA epsilon 搜索

本实验从实验 23 复制数据、负载、对照臂及其余固定参数，只把目标臂
EWMA Top-up 的 epsilon 改为双侧触发。19 个 epsilon 值为 0.05、0.10、…、0.95；
每值 10 轮，每轮每臂 40,000 CTX。结果单独写入本目录的 `out/`。

## 双侧触发定义

令 `actual[i]` 为分片子账户可用余额，`target[i]` 为该时刻 EWMA 目标余额。
同一个 epsilon 同时定义下界 `(1-epsilon)*target[i]` 和上界
`(1+epsilon)*target[i]`。任一子账户严格低于下界或严格高于上界，就可启动一次
Top-up 事件；仍需满足 EWMA 就绪、事件互斥和 10 块最小间隔。

搬运时优先使用越过下界的缺口账户与越过上界的盈余账户。若只有一侧越界，
另一侧可使用任何低于或高于目标的账户配对。只补选中的缺口，资金不足时允许
部分补足；不会调用 proportional 的全量再平衡函数。此开关只作用于 EWMA Top-up
目标臂，Plain、Valve、Proportional 和硬窗口 Top-up 对照臂仍按实验 23 执行。

数据从 `trace/ETH_cleaned.csv` 读取，排除失败、合约端点与非跨片转账，金额
限制在 0.01～10 ETH。固定 16 分片、50 brokers、每个 broker 的每个分片
150 ETH 初始余额、120 CTX/逻辑块。EWMA 半衰期及有限窗口均为 20 块，
最少预热 5 块，`q_min=0.10`。十轮使用同一批交易，仅路由种子依次为
7、17、…、97。

## 运行

在项目根目录执行：

```powershell
python .\experiments\exp024_ewma_two_sided_epsilon_search_40k\run.py
```

若中断，从对应输出目录继续：

```powershell
python .\experiments\exp024_ewma_two_sided_epsilon_search_40k\run.py --resume .\experiments\exp024_ewma_two_sided_epsilon_search_40k\out\<运行时间戳>
```

脚本会寻找可用的连续 16 个本地 Anvil 端口；建议等实验 23 结束后再运行，
以免两个大实验同时占用内存和 CPU。全部 19×10 轮完成后，汇总与
`figs/reference_style/fig6a_epsilon_exp024.png`、同名 PDF 自动生成。
实验 23 的结果采用单侧触发，不能并入实验 24 的双侧结果。

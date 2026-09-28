# exp008 第二轮 EWMA 参数候选

本轮目标不是降低 valve 的实现能力，而是在当前弱热点 trace 上恢复有区分度的
流动性压力，并让 EWMA 通过提前预测获得优势。

## 正式参数

| 参数 | 当前值 | 含义 |
|---|---:|---|
| 分片数 | 16 | 每个 broker 有 16 个分片子账户 |
| broker 初始余额 | 5 ETH/分片 | 每个 broker 初始总池 80 ETH |
| 单笔上限 | 3 ETH | 初始余额为最大单笔的 1.67 倍 |
| 硬窗口 | 12 块 | hard-window topup 与 proportional 的需求窗口 |
| EWMA 半衰期 | 8 块 | 等效指数窗口约 12.05 块，与硬窗口基本对齐 |
| topup 缺货 epsilon | 0.20 | actual < 0.8 × target 时成为缺货方 |
| topup 供货 epsilon | 0.10 | actual > 1.1 × target 时成为供货方 |
| 最小事件间隔 chi | 2 块 | 同一 broker 最快每 2 块开一次 TDR 事件 |
| EWMA q_min | 0.80 | 初始状态对应每片 4 ETH 目标地板 |
| topup 绝对安全库存 | 4 ETH/分片 | 运行时动态换算有效 q_min，覆盖 3 ETH 大额交易并留 1 ETH |
| 单分片目标上限 | broker 池的 20% | 初始约 16 ETH，避免短期噪声过度集中 |
| valve 阈值 | 1.3 × 初始余额 | 任一分片达到 6.5 ETH 才触发，触发后向低于 5 ETH 的分片补货 |
| 冷启动基础偏移 | 1 块 | 需求源 ready 后的基础等待 |
| broker 错峰范围 | 4 块 | 50 个 broker 分散提交，避免 mint 队列瞬时拥塞 |
| TDR 块轮询 | 0.5 秒 | 更及时发现新块和资金缺口 |
| TDR 超时 | 20 块 | burn/mint 事件超时保护 |
| 注入速率 | 120 ctx/s | 与前次正式实验一致 |
| 每场交易数 | 40,000 | 50 broker × 800 |
| 正式重复 | 5 场 | route seed 7/17/27/37/47 |

五个方案：

```text
plain@5
valve@5@1.3
tdr@5@@0.1
topup@5@@0.2@2
topup@5@@0.2@2@8@0.8
```

最后一项是本轮 EWMA 候选。

## 建议判定

只有同时满足以下条件，才应称为“EWMA 最好”：

1. 五场全部通过守恒、完整性、预算和 RPC gates；
2. EWMA 的 Relay 跨场中位数严格低于 valve；
3. 至少 4/5 个配对场次中 EWMA Relay 低于 valve；
4. 相对 plain 的配对 Relay 改善率中位数不低于 20%；
5. 按完整 `wall_s` 计算的吞吐损失中位数不高于 15%；
6. 若 EWMA 与 valve 的 Relay 打平，只有 EWMA 搬运量也更低时才判 EWMA 胜出。

如果第 2 或第 3 条失败，应报告为当前 trace 更适合 valve，不能只依据相对 plain
的 V2 通过就宣称 EWMA 优于 valve。

## 运行

在本目录执行：

```powershell
python -m brokerlab doctor --config config.yaml
python run.py
```

项目根目录执行时使用：

```powershell
python -m brokerlab doctor --config experiments/exp008_tdr_schedule_final/config.yaml
python experiments/exp008_tdr_schedule_final/run.py
```

# BrokerChain：学习与适配

[ToN论文](https://doi.org/10.1109/TON.2025.3550502)，2025，33(4):1930–1945。
[机构元数据](https://researchportal.hkust.edu.hk/en/publications/brokerchain-a-blockchain-sharding-protocol-by-exploiting-broker-a/)。
已取得[作者预印本](https://arxiv.org/abs/2412.07202)；请重点读账户分段与broker跨片交易部分。

## 原系统

同一账户可以在不同分片持有分别记录的状态和余额，broker通过跨片子账户承担跨片交易。
论文也涉及账户划分和分片性能，目标不是单独提出一种在线余额调拨控制器。
2025年是期刊扩展版，基础broker机制源于更早工作。
[BlockEmulator](https://github.com/HuangLab-SYSU/block-emulator)是公开实验平台；
论文出现在平台文献列表不能证明期刊版全部功能一键可复现。

## 本实验基线

保持当前工程的broker匹配、两阶段交易、资金、手续费与Relay兜底，无主动子账户调拨。
只有普通成功交易可以改变broker资金位置。该臂回答“余额空间失衡在本平台造成多少Relay与收入损失”。
全部其他方法沿用同一broker底层。代码方法名brokerchain，输出标签BrokerChain-core/No-Rebalance。
本实验不比较共识协议或完整账户划分，所以不能把这一臂说成整篇期刊论文的端到端系统性能。

新增路由修复：派发时扣目的可分派余额；不预先增加源收入；成功确认报告时加入源资金及留存费。
它消除了同一时刻把尚未到账收入用于下一次分派的乐观估计。所有方法采用同样修复，保证可比。

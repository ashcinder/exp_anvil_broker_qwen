# Horcrux：学习与适配

原文：[正式PDF](https://www.ndss-symposium.org/wp-content/uploads/2025-495-paper.pdf)。
NDSS 2025。先读摘要/引言，再看第12页实验中的fund redistribution，最后看协议构造。

## 论文原方法

PCN中同方向多跳支付会改变每个中间节点相邻通道内的资金分布。Horcrux使用虚拟通道和flow neutrality，
让中间节点在支付期间移动相邻通道中的资金，降低余额偏移。实验策略根据入侧已有余额与当前支付金额决定调拨。
原协议涉及多方联合控制、通道更新和冻结/解冻，不能把通道状态公式直接视为独立钱包资金守恒公式。

实验使用10529节点、38910通道的LN数据，比较支付成功率、成功金额、通道枯竭与运行成本，
并有10次重复。原实验不等同于分片Anvil实际交易。[配套代码](https://github.com/Anqi333/implementation-of-horcrux)
含basic_implementation与simulation_in_testnet；README说明是概念实现。

## 本实验明确的改编定义

broker完成一笔src→dst服务后，源子账户增加v、目的子账户减少v。
记补偿债务C[src,dst]+=v；反向成功服务先抵消已有反向债务。
每个决策块，从确认可用余额中执行min(C[src,dst],available[src])；同一来源总额不能超可用余额。
冷却、事件互斥、真实burn/mint和延迟与TDR共用。只有完成调拨后才减少债务。
这是一种交易补偿式资金策略；没有让原交易使用未到账的资金，也没有完整实现Horcrux虚拟通道。

示例：服务使余额由[100,100]变为[120,80]，补偿需求20；实际转移到账后恢复[100,100]。
多笔同方向服务可以聚合为一次转移，减少链上开销；等待期间仍可能发生资金不足。
代码入口：`ComparisonAgent.note_settled`、`tick`中的horcrux分支。

需自行检查：聚合降低成本但增加恢复延迟；共同冷却对原即时协议构成改动。
论文中可称“Horcrux-inspired wallet-local flow-neutrality baseline”，不能称原协议完整复现。

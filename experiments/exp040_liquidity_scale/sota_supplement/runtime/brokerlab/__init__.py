"""brokerlab —— 跑在 anvil 上的 BrokerChain/TDR 分片测试床（从零重构版）。

包内分层：config（参数）→ chain/identity/tx（链与账户底座）→
brokerchain/matching/real_data（机制与数据）→ broker_engine/procmon（引擎与观测）→
cli（demo 编排）。实验脚本在 experiments/ 下，一包一个实验。
"""
__version__ = "0.1.0"

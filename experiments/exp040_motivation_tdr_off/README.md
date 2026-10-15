# 实验40：Motivation 与 TDR_OFF 收益阴影折线图

固定关闭TDR，独立运行10轮，为Fig1提供带误差帽的broker/relay堆叠柱，以及未开启TDR的broker收益与潜在收益上限阴影折线。实验41单独执行TDR_ON资金/交易规模扫描。

## 参数

16分片、50broker、200用户；每个broker每个分片子账户45ETH；每轮40,000用户CTX，共10次、400,000CTX。1秒/块、释放120CTX/块、sender_window=2；route_seed依次7、17、…、97。每轮独立起链，实际引擎plain、tdr.enabled=false，搬运数必须为0。

从ETH_cleaned选出200k母工作流后取40k前缀，与实验41的基准输入相同；第12,000笔后每1,600笔在目的热点11/3间切换，避免同分片。真实trace选流加人工方向压力，不等于主网原始时间顺序。种子重复反映动态路由/执行差异，不是独立trace。

## 收益与图

固定手续费F=21,000,000wei。成功broker服务的实际收入为0.1F=2,100,000wei/笔；潜在收入上限为该broker所有指派用户CTX（含后来relay）数×0.1F。不按本金收费，内部搬款不计用户交易或上限。

- Fig1左图：各轮按照相同的100块区间，从原始submit_block统计broker/relay数量，再汇总均值与样本标准差。蓝色broker在下、橙色relay在上，左侧CTX轴，无通信量红线、无右轴。分界处误差帽是broker数的SD，柱顶误差帽是各轮总数的SD；后者直接计算，不把两部分SD相加。
- Fig1右图：预先固定broker19，绘制收入与上限的均值±样本SD阴影；同时导出全系统版。横轴是全局arrival_pos累计统计，不声称是同步墙钟收入。动态匹配导致相同编号broker不保证在ON/OFF中收到相同交易。
- 单轮不伪造误差棒；空区间计0，覆盖最长一轮全部交易，不裁掉排队尾部。

## 运行

从仓库根目录，安装requirements.txt及Foundry/anvil，准备被Git忽略的trace/ETH_cleaned.csv：

```powershell
python experiments/exp040_motivation_tdr_off/run.py --dry-run
python experiments/exp040_motivation_tdr_off/run.py --smoke
python experiments/exp040_motivation_tdr_off/run.py
```

smoke为2分片2broker、40CTX×2轮，标SMOKE；正式模式是10轮。可先用--prepare-only --no-plot只准备数据，或--max-tasks 1 --no-plot仅执行首轮。

```powershell
python experiments/exp040_motivation_tdr_off/run.py --resume '本实验完整运行目录'
python experiments/exp040_motivation_tdr_off/analyze.py --source '本实验完整运行目录'
```

续跑保留首次--smoke/--rounds/--set；源码、配置、工作流或数据变更拒绝续跑，失败重试另开attempt。未完成结果仅能通过--allow-partial生成显式标记的探索图。

产物在本目录out/<时间戳>/：独立manifest/report、每轮原始CTX/TDR台账、per_broker.csv、revenue_curve.csv、figs/fig1a_tdr_off.png/.pdf、逐轮分箱/均值SD CSV、fig1b_broker_19.png/.pdf及system版。

本入口共享相邻实验41的执行/记账代码，因此迁移时保留两个目录。两实验共享端口45940的串行锁；实验41运行时本实验拒绝启动，反之亦然。不要改锁端口绕过同机串行保护。

本仓库编号为40=OFF、41=ON。早期本地exp041_fig1_plain_repeats目录属于上传前命名，不要用该旧名替换当前入口或混合manifest。

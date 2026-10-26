# 拆分版交付验证（2026-10-15）

当前版本exp040-041-split-v2。用户明确只要求代码和小规模验证，正式170+10次没有启动。旧340次配对方案仅历史保留。

- 18项设计/记账测试通过：TDR-on无Plain、17格去重、170次/1020万CTX；独立off固定40k/45ETH、10次/40万CTX；有效参数、固定费用、守恒、搬运校验；误差帽直接计算逐轮总数标准差，覆盖总数恒定但路由份额波动、空区间、n=1等情形。
- 实验40真实Anvil小规模验证：out/20261015_223936_512754，2分片2broker，TDR-on的1/2ETH两档各40CTX，共2次/80CTX。全部通过，第二档完成2次真实TDR搬运。收入与净收益箱线PNG/PDF已生成，PNG已查看。
- 实验41真实Anvil小规模验证：../exp041_fig1_plain_repeats/out/20261015_224158_008401，40CTX×2轮，种子7/17，共80CTX。全部通过，搬运均0。
- Fig1真实重复图已查看：broker/relay堆叠，左侧CTX轴，无红线/右轴；分界处与柱顶为真实SD帽。[20,30)的broker均值4.5、SD .7071，总数均值10.5、SD .7071；[30,40)总数SD为0，broker SD仍.7071。每轮分箱总数均40。

## 正式数据准备完成，但未执行

- 实验40：out/20261015_224342_943315，170任务全部pending、无attempt，计划10,200,000用户CTX。
- 实验41：../exp041_fig1_plain_repeats/out/20261015_224401_224057，10任务全部pending、无attempt，计划400,000用户CTX。
- 两者均基于相同200k母流设计，40k基准文件SHA-256相同：22f6ff606dd94747fb5b757047b1435dfebf06bd830a57ea87d7f35be2a551c9。输入相同不保证动态匹配到同编号broker的交易集合相同。

后续若要求正式执行，可在C盘工程根目录串行运行以下命令（本次未执行）：

```powershell
python experiments/exp040_liquidity_scale/run.py --resume experiments/exp040_liquidity_scale/out/20261015_224342_943315
python experiments/exp041_fig1_plain_repeats/run.py --resume experiments/exp041_fig1_plain_repeats/out/20261015_224401_224057
```

源码或配置变更后应新建运行，不绕过续跑签名。

# exp005 · broker 执行基底基准（流水线引擎：串行轮转 / 线程 / 进程）

**测什么**：同一个流水线 tick 引擎（`brokerlab/broker_engine.py`，M3 broker
块循环的预演），同一个静态指派的 50 broker × K 笔真实 trace 负载，三种容器各跑一遍：

- `serial`：单进程，一个循环轮转 50 个引擎的 `tick_once()`（协作式事件循环）；
- `threads`：50 个线程，每线程一个引擎（各自独立的 NonceManager / Web3 连接）；
- `processes`：50 个 spawn 子进程，每进程一个引擎（服务全本地重建）。

差的是执行基底（承载引擎代码运行的容器：单进程 / 线程 / 进程），不是工作量。
回答的问题：M3 锁定"每 broker 一个独立进程"，
相对线程与串行，这个选择花多少钱、买回什么。

**单 broker 引擎（用户 2026-09-04 纠正的关键前提）**：协议只约束
① Θ2 等自己的 Θ1（逐笔两阶段承诺）、② 发 Θ1 前 dst 侧预留（available =
mirror − committed）。下一笔的 Θ1 不等上一笔——在途段数上限 `exp.max_inflight`。
exp001/002 的阻塞式 `BrokerChain.execute` 是深度 1 的特例，保留作**外部锚点**
（`exp.anchor_dir` 指向 exp001 产物，不重跑）。

**假设与判据**（公式在 run.py，全部实测）：

| 编号 | 内容 | 进退出码 |
|---|---|---|
| G1 | 每个方案所有 CTX 成功；无 error 信封；reserve_blocked 总计 0 | 是 |
| G2 | 三方案 ctx_id 多重集相同且 ≡ assignment.csv；route 全 broker | 是 |
| G3 | 逐 broker：链上 == 垫资+Σsrc−Σdst（0 wei 差）；全局净和 0 | 是 |
| G4 | 串行方案吞吐 ≥ 5× exp001 阻塞口径（流水线有效性） | 是 |
| H-B1 | threads 与 processes 对 serial 的墙钟加速 ≥ serial_speedup_min（2×） | 是 |
| H-B2 | p95(e2e)：threads / processes ≥ 1.2 预期（GIL 尾部） | 否，证据 |
| H-B3 | processes 的 t_ready ≤ 120 s；启动次序记录 | 是，上限 |
| H-B4 | 每方案 CPU 秒与峰值 RSS 成本表（/proc 采样，含子进程） | 否，证据 |

退出码：0 ⇔ 全部必过判据通过；1 ⇔ 判据不过；2 ⇔ 基建中止（RSS 看门狗 / worker 死亡 /
就绪超时）。dev 单方案跑 H-B1 判 N/A，不进退出码。

**nonce 所有权**：装箱按 (sender_idx, src_shard) 组（`group_key`），一个组只进
一个箱 ⇒ 每账户每分片单写者在三方案都结构性成立。跨 broker 共享 sender 被否决：
anvil mempool 的 nonce 缺号会同时卡死两个 broker，错误模式比竞争更糟。

**运行**：

```bash
python -m brokerlab doctor --config experiments/exp005_broker_substrate_benchmark/config.yaml
python run.py                                        # 全量，K=16 ⇒ 800 笔
python run.py --set exp.ctx_per_broker=2 --set exp.modes=serial      # dev 冒烟
python run.py --set exp.ctx_per_broker=2 --set exp.modes=threads,processes
```

资源口径：50 进程 ≈ 3–4 GB（看门狗上限 `exp.rss_cap_mb` 默认 6000 MB）；
默认 2 分片 1s 块，800 CTX ≈ 1600 段 ≪ 单块容量 ⇒ 链不是瓶颈，RPC 才是测量对象。

**产物**（`out/<时间戳>/`）：`assignment.csv`（钉死的工作负载）、
每个方案 `<mode>/ctx_rows.csv`（exp001 同列口径）+ `actor_summary.csv`、
`summary.json`（gates/speedup/H-B2 比值/H-B4 成本表/锚点对照）、
`figs/exp005_substrate.png`（(a) 各方案墙钟 (b) e2e ECDF 含 exp001 阻塞虚线 (c) 每
broker 箱大小×耗时散点）。

**效度威胁（如实声明）**：

1. anvil 是单节点 RPC，两种并行方案共享这一序列化点——若 threads ≈ processes，
   结论应读作"基底差异被 RPC 侧吸收"，不是"线程无罪"。分解证据：
   hops 分布相同而 t_secs 不同 ⇒ GIL；两个方案 t_secs 同步劣化 ⇒ RPC 饱和。
2. 静态指派看不到 M3 动态路由的反馈竞争——本基准只测基底，不测协议。
3. `probe_s=0.2` 轮询粒度给所有方案的 e2e 加了同向 ≤0.2 s 量化抬升（与 exp001 同）。
   串行方案的观测延迟还会叠加事件循环轮转成本——三方案同度量，互比有效。
4. 负载构造做了显式取舍：(sender,src) 组按"≤2×均值"过滤巨鲸、按组大小升序
   累加到 ≥N 后截断——箱均衡优先，重发送者的代表性偏低（exp002 的 drain
   实验不受此影响，那边不做组装箱）。
5. 6 核机器上 50 进程会排队 CPU；H-B4 报告实测 CPU 与墙钟，引用时连档位声明。

**最近结果**：`out/20260904_091106/`（seed=42 正式跑；`out/20260904_090639/` 为 seed=7 复跑，
模式逐位一致；更早的 090414 一轮因线程 CPU 记账缺陷被本轮取代——os.times 是进程级账本，
每线程各自差分 = 同一份账记 50 遍，已修正为进程总账摊分）。

负载 800 笔真实 trace（巨鲸过滤弃 6 组 863 笔；50 箱大小 15–19），三方案全部 G1/G2/G3 通过、
reserve_blocked=0、全局净和 0 wei：

| 方案 | 墙钟 | 吞吐 | CPU 合计 | 峰值 RSS | e2e 均值 | e2e p95 | 加速比 |
|---|---|---|---|---|---|---|---|
| serial | 33.3 s | 24.1 CTX/s | 30.3 s | 160 MB | 25.0 s | 28.7 s | 1× |
| threads | 40.6 s | 19.8 CTX/s | 42.1 s | 170 MB | 34.9 s | 39.7 s | **0.82×** |
| processes | 8.8 s | 91.5 CTX/s | 36.4 s | 3527 MB | 6.05 s | 6.89 s | **3.80×** |

判读（这是基准的设计目的——让数据裁决锁定决策，不是为决策背书）：

- **线程拿不到并行加速**：threads 方案进程总 CPU 42.1 s ≈ 墙钟 40.6 s ⇒ 全程只有约 1.04 核在转，
  GIL 把 50 线程的 RPC 解码/异常路径串行化了；再加唤醒风暴与上下文切换，比单进程轮转还慢 13%。
  H-B1 对 threads 判失败 ⇒ 本实验退出码为 1（H-B1 要求两个并行方案同时快于串行，线程方案不达标拖负整体判定，如实记录）。
- **进程兑现了并行**：36.4 s CPU / 8.8 s 墙钟 ⇒ 约 4.2 核活跃；3.8× 吞吐；
  每笔 e2e p95 比线程方案低 5.8 倍（H-B2=5.76，方向符合 GIL 假设）。
  成本：50 解释器 RSS 3.5 GB + 冷启动 6.95 s（预算 120 s 内，H-B3 过）。
- **每笔 CPU 三方案同量级**（38/53/46 ms）：活没变便宜，变的只是能不能摊到多核上。
- **流水线自身增益**（G4）：即使最差容器（串行 tick），24.1 CTX/s 也是 exp001 阻塞口径
  （0.89 CTX/s）的 27 倍——跨 CTX 的在途并行贡献了大头，基底差异是二阶的。
- 观测口径：hops 三方案均 ≈2.0 ⇒ 链上入块节奏与基底无关，墙钟/e2e 差异全在 Python 侧与 RPC 侧。

**结论**：M3"每 broker 一进程"被实证支持；线程方案被否决（不是"略差"，是"无增益且更差"）。
后续引用：吞吐与尾延迟类结论可引用（三跑稳定）；绝对墙钟随机器负载 ±10% 级浮动，连档位声明。

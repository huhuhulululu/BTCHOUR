# `research/` — 小时盘历史实验台

在这个目录出现之前，本仓库能看的最长一段带子是 `replay --hours 16`：**十六个小时**。所有的门
（005 的 $100、007 的不 hop、008 的不吃 taker）都是在几十张纸单和十七张真单上定的。

这里补的是缺的那一半：**`KXBTCD` 自己的全部公开记录**——写这行时是 1558 小时 / 66.9 天，**每天还在长**（跑 `--coverage` 看今天的数）。公开端点、可复跑、不入库。

> ## ⚠ 每天跑一次，否则永久丢失（ADR 022）
>
> Kalshi 只保留约 **66 天**的已结算市场记录。`/events` 仍然列出 8000 个事件、最早回到
> 2025-08，但其中 **6443 个的 `markets` 是空的**——没有 `result`、没有 `expiration_value`、
> 没有 candlesticks。边界卡死在 66 天（`26JUN2819` 空 / `26JUN2820` 有）。
>
> **这份数据集不是「一年历史里的一段」，它就是公开记录的全部。历史不可回填。**
>
> 这个脚本是增量的，所以它同时就是归档器。**断档超过 66 天，那一段再也补不回来。**
> `--coverage` 报已存范围、窗口内还没归档的、以及一周内将滚出窗口的时段。
>
> 016 的「≥130 天」重开条件只能**往前攒**，从 2026-09-03 起约 65 天后达到。

## 数据

```bash
ops/archive-hourly.sh                                  # 从 cron 跑这个（见脚本头部）
python3 research/pull_hourly.py --days 70 --workers 8  # 增量，已存的跳过
python3 research/pull_hourly.py --coverage             # 已存 / 未归档 / 快滚出窗口的
python3 research/pull_hourly.py --check                # 落后了就退出非零（给 cron 用）
```

**拉取成功但一条没拉到，退出码也是 0**——那正是 cron 永远不会告诉你的那种失败。
所以 `--check` 单独问一句「归档到底是不是当前的」，落后超过 `--max-lag-hours`（默认 6）
就退非零。`ops/archive-hourly.sh` 先拉后查，查不过就把 `--coverage` 打到 stderr 并退 1。
建议**每小时**跑而不是每天：拉取是增量的，多跑不花钱；
而机器在唯一那个每日时点上关机三天，就是三天再也补不回来。

写 `data/hourly.sqlite`（gitignored，约 700 MB / 66 天）。只用公开端点，不需要 key：

| 端点 | 拿到什么 |
| --- | --- |
| `/events?series_ticker=KXBTCD&status=settled` | 已结算的小时（列表可回溯到 2025-08，但秒级 BRTI 只有约 66 天） |
| `/events/{event_ticker}` | 整条行权价阶梯 + `result` + `expiration_value` |
| `/live_data/events/{event_ticker}?range=1h` | 秒级 BRTI（存成 10 秒一格） |
| `/series/KXBTCD/markets/{ticker}/candlesticks` | 每分钟 `yes_bid` / `yes_ask` 的开高低收 + 成交量 |

**结算真相用 `expiration_value`**，就是 Kalshi 实际结算的那个 60 秒 BRTI 均值，不是现货代理
（`catalog/rules/settlement.md`）。重跑是增量的：已经拉过的 event 会跳过。

## 口径

费用、GBM 概率、σ 缓冲全部**直接 import `btchour.fees` / `btchour.model`**，不另写一套更便宜的。
一条只有在更松的成本模型下才赢的规则不是规则。

- 一个**小时**是一个聚类。同一小时的各档一起动，所以 t 统计按 `event_ticker` 做 cluster-robust
  （15 分钟仓 LESSONS：每张一行的朴素 t 会被抬高 80%）。
- 在分钟末 `ts` 做的决定只能读那一分钟的 candle **收盘**值和更早的东西。
- `yes` 吃 `yes_ask_close`；`no` 吃 `1 − yes_bid_close`。
- 净额单位是**每张分**，已扣进场费（提前平的还扣一次出场费）。

## study 清单

| 脚本 | 问题 |
| --- | --- |
| `study_calibration.py` | 小时盘阶梯上，钱在哪个价带？（favorite–longshot 图） |
| `study_candidates.py` | 候选进场规则的网格 + Bonferroni + discovery/holdout |
| `study_coupon.py` | 现行默认 `impulse_wait` 25¢ 挂等到底赚不赚（015 的题面） |
| `study_rule.py` | 冻结一条规则，压力测试：滑点 / 流动性 / 拥挤 / 时段 / 打乱结算 / 风险 |
| `study_cushion_map.py` | 缓冲 × 卖一的**图**，而不是一个网格搜出来的格子 |
| `study_maker.py` | 挂偏强侧（maker 费 0）能不能躲开二次费——touch / cross 两种成交口径 |
| `study_density.py` | 阶梯的**隐含密度** vs 实现；两条腿合成的区间赌；60 秒 TWAP 压缩（`--audit` 见下） |
| `study_taker_plays.py` | `swing_t` / `impulse_t`：**直接调生产的门和出场栈**，不手抄（031） |
| `replay_db.py` | **整条 loop** 跑 1557 小时（不是 `--hours 16`）；同 bar 成交对照（032） |
| `study_twap_tail.py` | 用 10 秒网格验 019 的 `τ<T` 分支；现货网格最后两点是结算价（034） |
| `study_conditioning.py` | 跨小时 / 时段 / 波动区间；表头自带 Bonferroni（035） |

```bash
python3 research/study_calibration.py
python3 research/study_candidates.py --family final --split
python3 research/study_coupon.py --slice early      # 也跑 --slice late
python3 research/study_rule.py
python3 research/study_cushion_map.py --slice early
python3 research/study_maker.py --slice early
python3 research/study_density.py
python3 research/study_taker_plays.py
python3 research/study_density.py --audit    # 三个口径并排：见下
```

## `--audit`：发表前的强制对照（ADR 030）

五次自我更正（025 数重了 / 027 口径不一致 / 028 采样粗了 / 029 门槛松了 / 030 结构是采样
画出来的）指向同一句话：**汇总口径本身在做判断**。所以规矩是——

> **任何按分钟网格得到的表，发表前必须跟一张「全分辨率 + 去重 + 流动性分层」的对照表
> 一起出现。**

`--audit` 把三个口径并排打印，改动量直接可见。冷热的定义只有一份，在
`hourly_lab.rung_reference_volume()` / `liquidity_tier()`（本小时中位档量的 ½ / 2×）。
030 用它把第 6 节 A 表的「两侧符号相反」测没了，B 表从 t=−0.34 变成 t=−2.00。

每个 study 都接 `--slice early|late`：**按小时数中位切日历**，前半选形状、后半验证。
`cushion_hold` 就是死在这一步（前半 −，后半 +，全样本 t=0.62）。

结果与结论：[`catalog/research/hourly-backtest-2026-09-03.md`](../catalog/research/hourly-backtest-2026-09-03.md)。

## 借鉴自 `huhuhulululu/kalshi`（15 分钟仓，只读，未改动）

那边 48 个 phase 的产物里，对本仓库有用的是**方法**和**否定结论**，不是它的坐等姿态（GOALS 明说不抄）：

- `phase12b`：便宜侧 0.20–0.30 真实胜率比隐含低约 2pp；止盈在**每一个**价带都比持有差 1.3–2.7¢。
- `phase4 G1` / `phase13 adverse_selection`：盘中出场 11 条全负；挂单成交子集胜率低 5.5–6.3pp。
- `phase19`：走前协议本身会不稳；规则空间越大越像在最大化噪声；打乱结算做零假设。
- `phase1 microstructure`：唯一跨币种失效点是偏强 **0.85–0.95 被低估约 1.5–2pp**——这就是本仓库
  `cushion_hold` 的机制来源，但参数是在 `KXBTCD` 自己的带子上重新量的，不是抄参数。

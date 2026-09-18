# 研究框架（`python3 -m btchour research`）

这套东西不下单。它存在的理由只有一个：**让「这条策略赚钱」这句话带上样本量和置信区间**，而不是带一个手挑的时段。

`EV = p · b − (1 − p)` 给的是一笔单事前的分。研究框架给的是一条规则事后的分：多少笔、每笔多少、区间跨不跨零、样本外掉多少。

## 三个实验

```bash
python3 -m btchour research fill-model --hours 80 --seeds 8
python3 -m btchour research regime      --hours 80 --seeds 8
python3 -m btchour research calibration --hours 200 --seeds 3
```

| 实验 | 问的问题 | 为什么这个问题有错误答案 |
| --- | --- | --- |
| `fill-model` | 回放的挂单成交模型是不是在自己给自己发钱 | `fair` 世界里盘口报的就是真实公允值 + 价差。**任何人在这里赚到的钱都是回放付的，不是市场付的。** |
| `regime` | 换成诚实成交后，规则能不能在专门为它造的世界里赚到钱 | `underreact` 世界里盘口跟不上 3 分钟动量，顺动量就是真的正 EV。在这里都赚不到，就是机制坏了，不是信号不够 |
| `calibration` | 哪个结算模型真的预测得准 | 不牵涉盘口。每根近 ATM 档、每分钟，拿模型 p 去对真实结算。Brier 越低越好，0.25 = 全填 0.5 |

## 真 tape 到手之后的第一件事

```bash
python3 -m btchour sweep --hours 24        # 能连 Kalshi 的机器上跑
python3 -m btchour research archive        # 固化进 data/archive/
python3 -m btchour research baseline       # 模型 vs 市场中价
python3 -m btchour research oos --hours 48 # 老的一半调参，新的一半报数
```

`baseline` 是**决定性检验**，跑在归档上（归档为空时退回合成盘，只验证工具）。它拿 `digital_prob` 和盘口中价打同一批近 ATM 档、同一个真实结算，按两者的分歧分桶记 Brier。

**20% 门只在分歧 ≥5¢ 时开火**（见 [`ev.md`](ev.md)）。所以要看的不是整体校准，是那两行里谁更准：

- 中价更准 → 门开的是模型误差，不是市场错价。**任何 taker 类提案到此为止**，先换模型。
- 模型更准且样本够 → 才有资格谈入场规则。

## 合成盘是什么、不是什么

`btchour/research/sim.py` 造的一小时：现货是无漂移 GBM（鞅），结算走真的 60 秒均价，盘口按 `mid = 公允(现货 × (1 + k·r3)) + AR(1) 噪声`，再加一个近 ATM 1¢、尾部更宽的价差。`r3` 就是引擎自己那个 3 分钟 `impulse`。

**`k` 是唯一能造出优势的旋钮，它的符号说明优势在动量的哪一侧：**

| regime | `k` | 这个世界里谁赚钱 |
| --- | ---: | --- |
| `fair` | 0 | 没有人。对照组 |
| `underreact` | −0.45 | 顺着动量进的人 |
| `overreact` | +0.45 | 反着动量进的人 |

合成盘**证明不了**一条策略在真盘上赚钱。它只能证伪：在为你造的世界里都赚不到，真数据也救不回来。真盘结论仍然只能来自真 tape。

## 怎么读一行结果

`format_runs` 每行给：笔数、每小时笔数、胜率、总盈亏、每笔盈亏、**95% 自举区间**、**t 值**、最大回撤。

- 区间跨 0 = 没有结论，不管均值多好看。
- |t| < 2 = 噪声，不管符号。
- 6 笔 6 胜不是好结果，是**要去查成交模型**的信号。

`compare(..., train_fraction=0.5)` 按时间切：老的一半调参，新的一半报数。两边差得远 = 过拟合，不是运气差。

## 成交时的 edge

挂单成交那一刻，引擎会记下 `fill_model_p`（模型对我们这一侧的读数）、`fill_spot`、`fill_impulse`。

`maker_edge_at_fill` 报的是 `fill_model_p − 付出的成本`。**这个数为负，说明成交规则本身在挑分布里坏的那一半**，跟信号强弱无关，换任何入场理由都救不了。

相关：[`ev.md`](ev.md)、[`plays.md`](plays.md)、[`settlement.md`](settlement.md)。

## 已经跑完的判定（2026-09-18）

`baseline` 跑过了，跑在 92 小时真 tape 上。**中价赢，四个分歧桶全赢，区间不跨 0。**
所以上面那句「中价更准 → 任何 taker 类提案到此为止」不是假设，是已经发生的事。

细节见 [`../research/findings.md`](../research/findings.md) 和 `docs/decisions.md` 017。
重跑前先看那两处，不要把已经判死的方向再测一遍。

## 攒 tape

归档现在进 git（`.gitignore` 里 `data/*` + `!data/archive/`）。容器是临时的；归档不进
版本库，每个会话都从零开始。

```bash
python3 -m btchour sweep --hours 24     # 拉最近 24 小时
python3 -m btchour research archive     # 固化进 data/archive/
git add data/archive && git commit      # 这一步不能省
```

Kalshi 分钟 K 线大约只回溯 4 天，所以**一次补不出 300 小时**，只能往前持续攒。
C 方向（贴价挂）的前置就卡在这里。

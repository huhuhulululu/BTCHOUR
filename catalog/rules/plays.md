# 灵活编排（默认 playbook = `flex`）

默认不再只锁仓、也不只逆势刮头。`flex` 把几套玩法叠在一起，**先拿已经决定的 20%，再顺着 3 分钟动量做 T**。

| Play | 何时进 | 何时出 |
| --- | --- | --- |
| `lock_hold` | σ≥3.2、p≥99.8%、ask≤$0.82、费后 b 和 EV 都 ≥20% | 拿到结算；盘口若已锁 20% 也可以提前走。**不做 T、不在 TWAP 前 flatten** |
| `impulse_t` | 3 分钟 BRTI 至少动 **$100**，只做动量方向，ask $0.18–$0.52 | **10%–50%** 兑现带；**不翻仓**；亏了下一小时空仓 |
| `swing_t` | 小时/15m、距行权价 ≤$600、ask $0.18–$0.72、p≥55%、p−ask≥8% | 同一条 10%–50% 带 / 4¢ 回撤 / p 淡化 12 点；同一小时一张合约，不翻面 |
| `lock_wait` | 已经决定但卖一还贵：在 $0.83 挂等 | **不成交不算入账** |
| `hold_edge` / `markout_scalp` | 旧玩法，设 `BTCHOUR_PLAYBOOK=hold` 或 `scalp` | 见下 |

单独只用短线：`BTCHOUR_PLAYBOOK=swing`。单独只用锁仓：`BTCHOUR_PLAYBOOK=lock`（见 [`lock.md`](lock.md)）。做T细则见 [`swing.md`](swing.md)。

## 盘口锁定 / clip

买入成本 `C`（含 taker 费）。在买价 `P` 卖出（击买一 = taker）后：

```
round_trip_roi = (P − exit_fee − C) / C
```

`lock_exit_price` 是使该 ROI 达到目标的最低整分价格。taker 买在 $0.62，大约 **$0.73 锁 12%**、**$0.78 锁 20%**。深 ITM 的 $0.95–$0.99 做不出 T，也锁不住 20%。

## 默认门槛

- `BTCHOUR_PLAYBOOK=flex`
- 锁仓：σ 3.2 / p 99.8% / $0.82–$0.83（见 [`lock.md`](lock.md)）
- 做T：动量方向、ask $0.18–$0.52、兑现 **10%–50%**、不翻面（见 [`swing.md`](swing.md)）
- 失效：`INVALIDATE_P=0.40`（只作用于非锁仓仓位）
- 收盘前：`FLATTEN_SECONDS=40`
- `ALLOW_EARLY_EXIT=1`

灵活的意思是：**能锁 20% 就锁；有明确动量就做一笔 T；10%–50% 都算正常兑现；模型塌了就砍；不翻面。** 不是每笔保证 20%。没有票就空仓。

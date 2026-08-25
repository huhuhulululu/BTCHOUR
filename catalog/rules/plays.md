# 灵活编排（默认 playbook = `flex`）

默认不再只锁仓、也不只刮头。`flex` 把几套玩法叠在一起，**先拿已经决定的 20%，再做快窗口的 T**。

| Play | 何时进 | 何时出 |
| --- | --- | --- |
| `lock_hold` | σ≥3.2、p≥99.8%、ask≤$0.82、费后 b 和 EV 都 ≥20% | 拿到结算；盘口若已锁 20% 也可以提前走。**不做 T、不在 TWAP 前 flatten** |
| `swing_t` | 小时/15m、距行权价 ≤$600、ask $0.28–$0.72、p≥55%、p−ask≥8% | 20% 锁定 / 12% clip / 4¢ 回撤 / p 淡化 12 点；同一小时只做这一张合约，clip 后可翻对面，fade 后停手 |
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
- 做T：p 55%、缺口 8%、目标 12%、回撤 4¢（见 [`swing.md`](swing.md)）
- 失效：`INVALIDATE_P=0.40`（只作用于非锁仓仓位）
- 收盘前：`FLATTEN_SECONDS=40`
- `ALLOW_EARLY_EXIT=1`

灵活的意思是：**能锁 20% 就锁；ATM 有缺口就做 T；模型塌了就砍；同一分钟可以翻另一边。** 不是每笔保证 20%。做T 的 12% 是出货目标。没有票就空仓。

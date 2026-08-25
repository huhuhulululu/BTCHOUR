# 多种交易方式（flex playbook）

默认玩法是 **`flex`**。优势不在「拿到底」，而在同一小时里按盘口切换：

| Play | 何时进 | 何时出 |
| --- | --- | --- |
| `hold_edge` | 持有结算也能过门槛：`EV = p·b − (1−p) ≥ 20%`，且 `p ≥ 95%`、`b ≥ 20%` | 盘口已经锁住 20% 就卖；否则可拿到结算 |
| `markout_scalp` | 模型价差够大（默认 `p − ask ≥ 10%`），`p ≥ 60%`，买价 ≤ $0.80，并且存在能锁 20% 的卖价 | 对方买价够高就兑现；p 崩了就砍；最后约 40 秒（BRTI 60s TWAP）前平掉 |
| `maker_rest` | 仅当 `BTCHOUR_ALLOW_MAKER=1`：在 $0.83 挂单 | **不成交不算入账**。纸交易不会把挂单当成已成交 |
| `invalidate` / `flatten_time` / `lock_on_book` | 不是开仓方式 | 已有仓位的离场 |

## 盘口锁定 20%

买入成本 `C`（含 taker 费）。在买价 `P` 卖出（击买一 = taker）后：

```
round_trip_roi = (P − exit_fee − C) / C
```

`lock_exit_price` 是使该 ROI ≥ 20% 的最低整分价格。例如 taker 买在 $0.50，大约要卖到 **$0.64** 才锁住 20%。深 ITM 的 $0.95–$0.99 几乎锁不住，只能等结算或放弃。

## 默认门槛

- `BTCHOUR_PLAYBOOK=flex`
- 刮头：`SCALP_MIN_P=0.60`，`SCALP_MIN_GAP=0.10`，`SCALP_MAX_ENTRY=0.80`
- 失效：`INVALIDATE_P=0.40`
- 收盘前：`FLATTEN_SECONDS=40`
- `ALLOW_EARLY_EXIT=1`（`hold` 玩法也可以在盘口兑现后提前走）

这仍不是「每笔保证 20%」。刮头买在 $0.62、模型 p≈76% 的票，如果盘口不再给你 $0.78+ 的买价，拿到结算可能亏光。灵活的意思是：**能锁就锁，不能锁就按规则离场，而不是默认拿到到期。**

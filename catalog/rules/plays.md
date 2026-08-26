# 灵活编排（默认 playbook = `flex`）

默认不再只锁仓、也不只逆势刮头。`flex` 把几套玩法叠在一起，**先拿已经决定的 20%；砸盘里有 32–42¢ coupon 就挂 25¢。默认不吃 `impulse_t`。** 纸盘 10 笔完成里 7 笔 taker 把账打到 −4.46；目标是人手那种 dump NO clip，不是涨势 YES / skip 小时吃货。

| Play | 何时进 | 何时出 |
| --- | --- | --- |
| `lock_hold` | σ≥3.2、p≥99.8%、ask≤$0.82、费后 b 和 EV 都 ≥20% | 拿到结算；盘口若已锁 20% 也可以提前走。**不做 T、不在 TWAP 前 flatten** |
| `impulse_t` | **默认关**（`BTCHOUR_IMPULSE_TAKER=0`）。只留在 sweep 的 `flex_nowait` / cheap 对照。纸盘 skip 小时同向 taker `AUG2605`/`AUG2606` 止损 −1.89，涨势 YES `AUG2614` 止损 −0.65 | sweep 对照仍用 10%–50% 带 / −12% 止损 |
| `impulse_wait` | **dump coupon**。砸盘刚形成（动量 ≤−$40）且 NO ask 在 **$0.32–$0.42**、离现货 ≤$150 就**挂 $0.25**。**成交仍只要 impulse ≤−$100**。纸盘 `AUG2616` 15:21 ET T78299 已是 0.36，等 −$100 再挂时 live ask 已到 0.51。已经砸到 29¢ 的不挂（纸盘 `T78499` 那刀）。淡了不撤；反弹里不填；**淡了 ask==rest 也不填**（纸盘 `AUG2604` 03:41 ET）。纸盘 `AUG2608` 第一次打出人手那种 clip：`T78299` rest 0.25 under 0.34，砸盘里成交，`t_clip` +18% | 10%–50% 带；8 分钟没到 +10% 就 scratch；已经跑出 +10% 才用 −80% 扛反弹。涨势不挂 YES |
| `swing_t` | 小时/15m、距行权价 ≤$600、ask $0.18–$0.72、p≥55%、p−ask≥8% | 同一条 10%–50% 带 / 4¢ 回撤 / p 淡化 12 点；同一小时一张合约，不翻面 |
| `lock_wait` | 已经决定但卖一还贵：在 $0.83 挂等 | **不成交不算入账** |
| `hold_edge` / `markout_scalp` | 旧玩法，设 `BTCHOUR_PLAYBOOK=hold` 或 `scalp` | 见下 |

单独只用短线：`BTCHOUR_PLAYBOOK=swing`。单独只用锁仓：`BTCHOUR_PLAYBOOK=lock`（见 [`lock.md`](lock.md)）。做T细则见 [`swing.md`](swing.md)。

反复对照：`python3 -m btchour sweep --hours 16`。每个小时的 K 线只拉一次（缓存在 `data/replay-cache/`），再在同一段带子上跑 flex / swing / lock、skip 开/关，以及「便宜 NO / 低 p」对照。`--hours 16` 也会拉满 24 小时并同时报告两个窗口。便宜对照目前更亏，默认仍是 p≥52% / ask≤$0.52。

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

灵活的意思是：**能锁 20% 就锁；砸盘刚形成（≤−$40）且有 32–42¢ coupon 就挂 25¢，成交仍只要 −$100 砸盘；默认不吃 taker；亏了下一小时整小时不做 T（挂单和 taker 都停）；连续亏不叠坐下一小时；锁过这小时不再开 T；10%–50% 都算正常兑现；不翻面。** 不是每笔保证 20%。没有票就空仓。不要把便宜 NO 改成 taker——那条带子已经对照过，更亏。

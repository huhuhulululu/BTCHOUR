# Ticker 规则

## 系列

`KXBTCD`

## 事件（一小时一场）

```
KXBTCD-{YY}{MON}{DD}{HH}
```

- `{HH}` 是 **America/New_York** 整点，不是 UTC。
- 例：`KXBTCD-26AUG2514` = 2026-08-25 14:00 EDT = 18:00 UTC。
- `product_metadata.cadence = hourly`。
- 当前小时为 `status=open`（API 市场状态常为 `active`）；后续小时为 `unopened`，整点前开盘，窗口约 60 分钟。

## 市场（价格阶梯）

```
KXBTCD-{YY}{MON}{DD}{HH}-T{strike}
```

- 例：`KXBTCD-26AUG2514-T79199.99` =「BRTI 60 秒均价是否高于 79199.99」。
- 行权价步进常见 $100。`price_level_structure=linear_cent`，价格步进 $0.01。
- YES 买价 / NO 买价互为对手：买 YES @ p ≡ 卖 NO @ 1-p。V2 下单：`book_side=bid` 为 YES，`book_side=ask` 为 NO。

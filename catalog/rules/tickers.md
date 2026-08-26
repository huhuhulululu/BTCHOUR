# Ticker 规则

## 系列

`KXBTCD`

## 事件（一小时一场）

```
KXBTCD-{YY}{MON}{DD}{HH}
```

- `{HH}` 是 **America/New_York** 整点，不是 UTC。
- 例：`KXBTCD-26AUG2514` = 2026-08-25 14:00 EDT（对外报时用纽约时间；对照 UTC 是 18:00）。
- loop / supervisor 日志和跟用户说话一律用纽约时间。库内 `created_at` 仍存 UTC ISO。
- **小时盘 = 下一个整点收盘的那张盘。** 下午 4:13 做 5 点截止的 `KXBTCD-…17`，不是 15 分钟盘，也不是再等下一张还没开的 6 点盘。
- Kalshi 常把 5pm 这场标成 `cadence=daily`（同一 ticker、已开一整天、$250 阶梯）。标签和合约总时长都不改焦点。不要用 `cadence=hourly` 把它滤掉。
- 真 hourly 场次窗口约 60 分钟，`status=open`；后续整点多为 `unopened`，整点前开盘。

## 市场（价格阶梯）

```
KXBTCD-{YY}{MON}{DD}{HH}-T{strike}
```

- 例：`KXBTCD-26AUG2514-T79199.99` =「BRTI 60 秒均价是否高于 79199.99」。
- 行权价步进常见 $100。`price_level_structure=linear_cent`，价格步进 $0.01。
- YES 买价 / NO 买价互为对手：买 YES @ p ≡ 卖 NO @ 1-p。V2 下单：`book_side=bid` 为 YES，`book_side=ask` 为 NO。

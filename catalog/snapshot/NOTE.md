# 快照的有效期

`sweep.json` 和 `replay.json` 是 **2026-08-30** 跑的，用的是 016 之前的回放成交模型（分钟内 wick 判挂单成交）。

**里面的盈亏数字不能当证据。** 那套成交模型在一个报公允价的盘口上都能打出 90% 胜率 / t=7.09（[`../research/findings.md`](../research/findings.md)）。

重跑（需要能连 Kalshi 的机器）：

```bash
python3 -m btchour sweep --hours 16
```

新默认下 `BTCHOUR_REPLAY_WICK_FILL=0`，成交要求收盘报价落在 rest 上。想对照旧数字就临时设成 `1`，但别把它写进结论。

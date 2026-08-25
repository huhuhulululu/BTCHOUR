# BTC 小时盘目录（Kalshi）

本目录是 BTCHOUR 的唯一市场源。小时盘对应 Kalshi 系列 **`KXBTCD`**（Bitcoin price Above/below，`frequency=hourly`）。

实时同步：

```bash
python3 -m btchour sync
```

写入：

| 路径 | 内容 |
| --- | --- |
| `catalog/series/*.json` | 系列元数据、结算源、费率 |
| `catalog/rules/` | 结算、ticker、费率、**EV = p·b−(1−p)**、锁仓、**做T / 短线**、灵活编排 |
| `catalog/snapshot/latest.json` | 最近一次从 Kalshi 拉回的小时盘快照 |
| `data/catalog/latest.json` | 运行时副本（不入库） |

同一 `KXBTCD` 系列里还有 daily / weekly 场次（例如 5pm EDT）。小时盘以 `product_metadata.cadence=hourly` 为准。

相关但不作为小时盘主路径的系列：

- `KXBTC`：同一 BRTI 结算的区间盘（between / less / greater）
- `KXBTC15M`：15 分钟涨跌

公开 API 根：`https://external-api.kalshi.com/trade-api/v2`

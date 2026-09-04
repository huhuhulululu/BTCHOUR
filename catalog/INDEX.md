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
| `catalog/rules/` | 结算、ticker、费率、**EV = p·b−(1−p)**、锁仓、**做T / 短线**、灵活编排、**播报表格** |
| `catalog/snapshot/latest.json` | 最近一次从 Kalshi 拉回的小时盘快照 |
| `catalog/snapshot/replay.json` / `replay-swing.json` | 最近一次小时回放（flex / swing） |
| `catalog/snapshot/sweep.json` | 同一段 K 线上 flex/swing/lock、跳过亏损小时开/关的对照 |
| `catalog/research/learn.md` | 纸交易循环学到的拒单 / 空窗 |
| `catalog/research/manual.md` | 账户成交里守规矩的 10%–50% 和累了选错方向 |
| `catalog/research/hourly-backtest-2026-09-03.md` | **一年级回测** 1557 小时 / 66.8 天：价带图、coupon 出场栈、C1 判决、端到端（第 11 节）、费用门槛（10b） |
| `catalog/rules/cushion.md` | `cushion_hold` / `edge` playbook（对照，未接线，ADR 016） |
| `data/catalog/latest.json` | 运行时副本（不入库） |

小时盘 = 下一个整点收盘的 `KXBTCD-{YY}{MON}{DD}{HH}`。4:13 ET 做 5 点截止的盘。Kalshi 可能把这场标成 `cadence=daily`；不要因此改做 15 分钟盘或跳到下一张未开的 hourly。夜里只列出 5 点 daily 时，仍盯下一个整点（1:01 做 `AUG2702`），不要跳到 `AUG2717`。

相关但不作为小时盘主路径的系列：

- `KXBTC`：同一 BRTI 结算的区间盘（between / less / greater）
- `KXBTC15M`：15 分钟涨跌

公开 API 根：`https://external-api.kalshi.com/trade-api/v2`。每轮先读 `GET /exchange/status`；`exchange_active` 或 Crypto 的 `trading_active` 为假时不挂单、不成交。这不是切 live 的许可。

Agent 编制与改门流程：[`docs/TEAM.md`](../docs/TEAM.md)（角色卡在 `docs/roles/`）。不要从 15 分钟盘仓库抄 C1 的**参数**、日均美元阶段，或「没边就坐着」。**机制**可以借鉴，但必须在 `KXBTCD` 自己的带子上重量——2026-09-03 就是这么做的，结论是 C1 移植不通过（ADR 016），登记在 `catalog/research/hourly-backtest-2026-09-03.md`。小时盘阶梯操作权更高（decisions 011）。

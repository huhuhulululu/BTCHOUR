# BTCHOUR

Kalshi **BTC 小时盘**（系列 `KXBTCD`）目录 + 纸交易引擎。

默认策略是 **`flex`**：已经决定的合约先按稳健 20% 锁仓；小时盘上 3 分钟 BRTI 动量再做短线 T（**10%–50% 兑现带** / −12% 硬止损 / 不翻面 / 亏了下一小时只空反方向）。计分：`EV = p · b − (1 − p)`。

- 锁仓：[`catalog/rules/lock.md`](catalog/rules/lock.md)
- 做T：[`catalog/rules/swing.md`](catalog/rules/swing.md)
- 编排：[`catalog/rules/plays.md`](catalog/rules/plays.md)

```bash
python3 -m btchour ev --p 0.70 --b 0.50
python3 -m btchour sync
python3 -m btchour scan --playbook flex
python3 -m btchour probe --playbook swing
python3 -m btchour replay --hours 8 --playbook flex
python3 -m btchour replay --hours 8 --playbook swing
python3 -m btchour sweep --hours 16
python3 -m btchour run --once
python3 -m btchour loop
python3 -m btchour status
python3 -m btchour board
python3 -m btchour fills
```

默认 `BTCHOUR_MODE=paper`，`BTCHOUR_PLAYBOOK=flex`。$0.83 的锁仓等待单不成交不算入账。做T 的 10%–50% 是实测正常兑现带，不是保证。实盘密钥只放本机 `.env`，不入库。

只要锁仓：`--playbook lock`。只要短线：`--playbook swing`。

多角色编制（从 `huhuhulululu/kalshi` 客制，小时盘瘦编制）：[`docs/TEAM.md`](docs/TEAM.md)。目标只在 [`docs/GOALS.md`](docs/GOALS.md)。冻结门：[`docs/decisions.md`](docs/decisions.md)。

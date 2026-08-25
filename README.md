# BTCHOUR

Kalshi **BTC 小时盘**（系列 `KXBTCD`）目录 + 纸交易引擎。

默认策略是 **`flex`**：已经决定的合约先按稳健 20% 锁仓；小时盘 / 15 分钟盘上的 ATM 缺口再做短线 T。计分：`EV = p · b − (1 − p)`。

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
python3 -m btchour run --once
python3 -m btchour status
```

默认 `BTCHOUR_MODE=paper`，`BTCHOUR_PLAYBOOK=flex`。$0.83 的锁仓等待单不成交不算入账。做T 的 12% 是出货目标，不是保证。实盘需要 Kalshi RSA 密钥。

只要锁仓：`--playbook lock`。只要短线：`--playbook swing`。

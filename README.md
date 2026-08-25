# BTCHOUR

Kalshi **BTC 小时盘**（系列 `KXBTCD`）目录 + 纸交易引擎。默认策略是 **稳健锁仓**：只在合约已被价格决定、且费后还能赚 20% 时进场。

计分：`EV = p · b − (1 − p)`。见 [`catalog/rules/ev.md`](catalog/rules/ev.md) 和 [`catalog/rules/lock.md`](catalog/rules/lock.md)。

```bash
python3 -m btchour ev --p 0.998 --b 0.22
python3 -m btchour sync
python3 -m btchour scan --playbook lock
python3 -m btchour probe
python3 -m btchour replay --hours 8 --playbook lock
python3 -m btchour run --once
python3 -m btchour status
```

默认 `BTCHOUR_MODE=paper`，`BTCHOUR_PLAYBOOK=lock`。挂在 $0.83 的等待单不成交不算入账。实盘需要 Kalshi RSA 密钥。

旧的 `flex` / `scalp` 仍可用，但不再是默认：回放里它们不是稳健的 20%。

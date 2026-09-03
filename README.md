# BTCHOUR

Kalshi **BTC 小时盘**（系列 `KXBTCD`）目录 + 纸交易引擎。

默认策略是 **`flex`**：已经决定的合约先按稳健 20% 锁仓；小时盘上 3 分钟 BRTI 动量在 32–42¢ 活盘里挂 25¢ coupon，**成交后持到结算**（ADR 017）。`swing_t` / `impulse_t` 那套 10%–50% 兑现带 / −12% 止损仍在，但两者默认关。亏了下一小时只空反方向。计分：`EV = p · b − (1 − p)`。

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

默认 `BTCHOUR_MODE=paper`，`BTCHOUR_PLAYBOOK=flex`，`BTCHOUR_IMPULSE_WAIT_HOLD=1`。$0.83 的锁仓等待单不成交不算入账。10%–50% 兑现带只对默认关掉的 `swing_t` / `impulse_t` 有效——coupon 成交后拿到结算。实盘密钥只放本机 `.env`，不入库。

只要锁仓：`--playbook lock`。只要短线：`--playbook swing`。

**一年级回测（2026-09-03）**：`KXBTCD` 自己的 1544 小时 / 66.2 天，[`catalog/research/hourly-backtest-2026-09-03.md`](catalog/research/hourly-backtest-2026-09-03.md)，复跑见 [`research/`](research/README.md)。三条结论：整条阶梯 0.58 以下每格显著为负（ADR 018）；成交的 coupon 进场公道、**亏在出场栈**每张 3.28¢ —— 已按 ADR 017 改成**持到结算**（默认生效）；C1 移植 `cushion_hold` 未通过样本外、只作对照（ADR 016）。

多角色编制（从 `huhuhulululu/kalshi` 客制，小时盘瘦编制）：[`docs/TEAM.md`](docs/TEAM.md)。目标只在 [`docs/GOALS.md`](docs/GOALS.md)。冻结门：[`docs/decisions.md`](docs/decisions.md)。小时盘整条近 ATM 阶梯是工作盘，操作权高于 15 分钟研究仓的坐等（011）；`board` 必须报活档数。

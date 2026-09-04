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

只要锁仓：`--playbook lock`。只要短线：`--playbook swing`——**但先看这个数**：整条 `swing` 在 1557 小时上是每张 **−3.45¢、t=−5.52**、65 天 **−$202**、两个半样本都负（第 11 节）。它是本轮所有测量里最干净的负结果。`swing_t` / `impulse_t` 默认关就是这个原因。

**一年级回测（2026-09-03）**：`KXBTCD` 的全部公开记录 1557 小时 / 66.8 天，[`catalog/research/hourly-backtest-2026-09-03.md`](catalog/research/hourly-backtest-2026-09-03.md)，复跑见 [`research/`](research/README.md)。整条阶梯 0.58 以下每格显著为负（018）；成交的 coupon 进场公道、**亏在出场栈**，已按 017 改成**持到结算**（默认生效）；C1 移植 `cushion_hold` 未通过样本外（016）。**端到端**跑满 1557 小时、按正确口径每张 **+2.18¢、t=0.56**——统计上是零（032 / 第 11 节）。

> **动手改回测之前先看 [`docs/LESSONS.md`](docs/LESSONS.md)。** 那一轮撤回了自己七条结论，七条是同一个错（汇总或计时口径在替你做判断），每一次都伪装成发现。里面有一条止损线：**这条阶梯上任何 2pp 以上的校准边，先当成漏。**

多角色编制（从 `huhuhulululu/kalshi` 客制，小时盘瘦编制）：[`docs/TEAM.md`](docs/TEAM.md)。目标只在 [`docs/GOALS.md`](docs/GOALS.md)。冻结门：[`docs/decisions.md`](docs/decisions.md)。小时盘整条近 ATM 阶梯是工作盘，操作权高于 15 分钟研究仓的坐等（011）；`board` 必须报活档数。

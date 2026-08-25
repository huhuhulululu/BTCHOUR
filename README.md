# BTCHOUR

Kalshi **BTC 小时盘**（系列 `KXBTCD`）目录 + 自动扫描/纸交易引擎。

目录在 [`catalog/`](catalog/INDEX.md)。`python3 -m btchour sync` 会把 Kalshi 上当前小时阶梯、后续未开盘小时、BRTI live data 和系列规则拉到本仓库。

## 计分：EV = p · b − (1 − p)

详见 [`catalog/rules/ev.md`](catalog/rules/ev.md)。`p` 是模型胜率，`b` 是费后净赔率，`EV` 是每一美元本金的期望盈亏。持有结算的门票默认仍要 **EV ≥ 20%**，并且 `p ≥ 95%`、`b ≥ 20%`。

```bash
python3 -m btchour ev --p 0.99 --b 0.25
```

## 灵活策略

默认 `BTCHOUR_PLAYBOOK=flex`。不一定拿到结算：能在盘口锁住 20% 就走，模型失效就砍，最后约 40 秒避开 BRTI TWAP 彩票。见 [`catalog/rules/plays.md`](catalog/rules/plays.md)。

空仓是正确行为。这不是「每笔实盘保证赚 20%」。

## 命令

```bash
python3 -m btchour sync
python3 -m btchour scan --playbook flex
python3 -m btchour probe
python3 -m btchour replay --hours 8 --playbook flex
python3 -m btchour replay --hours 8 --playbook hold --no-early-exit
python3 -m btchour run --once
python3 -m btchour run
python3 -m btchour status
```

默认 `BTCHOUR_MODE=paper`。实盘需要 Kalshi RSA 密钥，并设 `BTCHOUR_MODE=live`。见 `.env.example`。

## 核实

```bash
python3 -m unittest discover -s tests -v
```

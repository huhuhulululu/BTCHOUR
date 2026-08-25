# BTCHOUR

Kalshi **BTC 小时盘**（系列 `KXBTCD`）目录 + 自动扫描/纸交易引擎。

目录在 [`catalog/`](catalog/INDEX.md)。`python3 -m btchour sync` 会把 Kalshi 上当前小时阶梯、后续未开盘小时、BRTI live data 和系列规则拉到本仓库。

## 计分：EV = p · b − (1 − p)

详见 [`catalog/rules/ev.md`](catalog/rules/ev.md)。`p` 是模型胜率，`b` 是费后净赔率，`EV` 是每一美元本金的期望盈亏。默认 **EV ≥ 20%**，并且 `p ≥ 95%`、`b ≥ 20%`。

```bash
python3 -m btchour ev --p 0.99 --b 0.25
```

空仓是正确行为。这不是「每笔实盘保证赚 20%」。

## 命令

```bash
python3 -m btchour sync    # 搬运 Kalshi 目录
python3 -m btchour scan    # 评估当前小时
python3 -m btchour probe   # 实盘 EV 表面 + 未过门槛的近失
python3 -m btchour replay --hours 8
python3 -m btchour run --once
python3 -m btchour run     # 纸交易循环（默认）
python3 -m btchour status
```

默认 `BTCHOUR_MODE=paper`。实盘需要 Kalshi RSA 密钥，并设 `BTCHOUR_MODE=live`。见 `.env.example`。

## 核实

```bash
python3 -m unittest discover -s tests -v
```

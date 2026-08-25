# BTCHOUR

Kalshi **BTC 小时盘**（系列 `KXBTCD`）目录 + 自动扫描/纸交易引擎。

目录在 [`catalog/`](catalog/INDEX.md)。`python3 -m btchour sync` 会把 Kalshi 上当前小时阶梯、后续未开盘小时、BRTI live data 和系列规则拉到本仓库。

## 20% 是过滤器，不是保证

Kalshi 二元合约结算 $1 或 $0。若猜对要净赚 20%，taker 最高大约买在 **$0.82**，maker **$0.83**（已扣二次费率）。

高效盘口上，高胜率合约通常已经报价 $0.95–$0.99，锁不住 20%。引擎默认：

1. 若猜对，净 ROI ≥ 20%
2. BRTI/GBM 模型 P(win) ≥ 95%
3. 期望 ROI ≥ 12%

不满足就空仓。空仓是正确行为，不是故障。无法保证每一笔实盘都赚 20%。

## 命令

```bash
python3 -m btchour sync    # 搬运 Kalshi 目录
python3 -m btchour scan    # 评估当前小时
python3 -m btchour run --once
python3 -m btchour run     # 纸交易循环（默认）
python3 -m btchour status
```

默认 `BTCHOUR_MODE=paper`。实盘需要 Kalshi RSA 密钥，并设 `BTCHOUR_MODE=live`。见 `.env.example`。

## 核实

```bash
python3 -m unittest discover -s tests -v
```

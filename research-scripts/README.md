# research-scripts

一次性的测量脚本，不是 `btchour` 包的一部分，没有接进 CLI。留在这里是为了**可复现**：
`catalog/research/findings.md` 里的每个数字都应该能被重跑出来。

## `kxbtc_fetch.py` / `kxbtc_measure.py`

`KXBTC` 区间盘（$100 分桶，蝶式）的 tape 抓取和测量。`btchour/replay.py` 的抓取写死了
`KXBTCD` 的 ticker 规则（`tickers.py: MARKET_RE`），认不了 `B<价>` 桶，所以另写一份。

```bash
python3 research-scripts/kxbtc_fetch.py 36      # 抓 36 小时到 data/kxbtc/
python3 research-scripts/kxbtc_measure.py       # 对比 KXBTC / KXBTCD 的价差与逆向选择
```

结论见 `catalog/research/findings.md` 第 12 节、`docs/decisions.md` 019。

**`kxbtc_measure.py` 里那个「要求成交 bar 有真实成交量」不是可选项。** 只按报价判成交，
在流动性差的盘口会凭空造出良性成交，把逆向选择压低 —— `KXBTC` 上有 65% 的代理成交是幻影，
足以把「每张 +0.46 分」变成「0」。

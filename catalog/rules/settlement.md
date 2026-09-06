# KXBTCD 结算

- 标的：CF Benchmarks **Bitcoin Real-Time Index (BRTI)**，每秒更新。
- 官方说明：到期前最后一分钟采集 **60 个 RTI 价格**，以其简单平均作为结算价。
- 合约问题：该结算价是否 **高于** 行权价（`strike_type=greater`，ticker 后缀 `T{strike}`）。
- `settlement_timer_seconds`：60。
- **建模含义（ADR 019）**：结算是**均值**不是端点，所以剩余方差比「距收盘秒数」小。
  有效方差时间 `τ − 2T/3`（τ ≥ T=60s）、`τ³/(3T²)`（τ < T）。见 `model.twap_variance_seconds`。
  实测 `(结算 − 现货)/σ` 的 sd：>30 分钟 1.05、10–30 分钟 0.96、**最后 10 分钟 0.82**。
- 禁止：数据源机构员工、持有标的重大非公开信息的人交易。
- 费率：`fee_type=quadratic`，`fee_multiplier=1`。

Kalshi 事件级 live data（无需鉴权）给出接近 BRTI 的秒级序列：

`GET /live_data/events/{event_ticker}?range=1h`

`live_data.type = crypto`，`details.timeseries[]` 为 `{t, v}`，`details.candlesticks` 含 `1M` / `15M`。

不要用 Google / Coinbase 当结算依据。Coinbase 只作 live data 失败时的现货兜底。

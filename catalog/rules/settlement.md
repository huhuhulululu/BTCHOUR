# KXBTCD 结算

- 标的：CF Benchmarks **Bitcoin Real-Time Index (BRTI)**，每秒更新。
- 官方说明：到期前最后一分钟采集 **60 个 RTI 价格**，以其简单平均作为结算价。
- 合约问题：该结算价是否 **高于** 行权价（`strike_type=greater`，ticker 后缀 `T{strike}`）。
- `settlement_timer_seconds`：60。
- 禁止：数据源机构员工、持有标的重大非公开信息的人交易。
- 费率：`fee_type=quadratic`，`fee_multiplier=1`。

Kalshi 事件级 live data（无需鉴权）给出接近 BRTI 的秒级序列：

`GET /live_data/events/{event_ticker}?range=1h`

`live_data.type = crypto`，`details.timeseries[]` 为 `{t, v}`，`details.candlesticks` 含 `1M` / `15M`。

不要用 Google / Coinbase 当结算依据。Coinbase 只作 live data 失败时的现货兜底。

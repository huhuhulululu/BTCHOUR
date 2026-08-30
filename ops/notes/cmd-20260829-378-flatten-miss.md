# CMD — 378 flatten 未成交、账平仓在（2026-08-29）

用户 17:35 EDT 问「我看怎么还在」。对的。

| 侧 | 当时 |
|---|---|
| sqlite 378 | closed `t_clip` 0.36 / +0.0938 |
| 交易所 | `position_fp=-1` T78099，只有进场 1 笔 fill |
| flatten IOC | `01a04f5d-…` status=canceled，fill 0 |

17:36:36 EDT CMD 补平：买 YES 0.50 / 卖 NO 0.50，费 0.0175。仓位归零。真账约 +0.2325。

根治（013 / `btchour/broker.py` + `engine.py`）：
1. flatten 按现价交叉，不按 clip 标记价。
2. `fill_count=0` 不 `paper_close`；账用交易所成交价。
3. 每轮对账残留 `live_one`，未平禁止再挂。

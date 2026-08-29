# Decisions（小时盘冻结门）

> 追加写入，不修改已有条目。格式: `### [编号] [标题]`
> 这不是 15 分钟盘的 ADR 全集。只记本仓库已经对用户生效的门。改门必须走 ARCH 草稿 → ADV → CMD → 用户。

---

### 001 只做 BTC 小时盘 `KXBTCD`

- 日期: 2026-08-25
- 状态: accepted
- 背景: Kalshi 还有 `KXBTC` 区间盘、`KXBTC15M` 15 分钟涨跌。
- 决策: 主路径只做下一个整点收的 `KXBTCD-{YY}{MON}{DD}{HH}`。午夜是 `AUG2900`，不是 `AUG2824`。
- 理由: 用户要的是小时盘。15 分钟盘研究在 `huhuhulululu/kalshi`。

### 002 计分 `EV = p · b − (1 − p)`

- 日期: 2026-08-25
- 状态: accepted
- 决策: 所有进场/对照用同一公式。~20% 是过滤，不是每笔保证。

### 003 默认 paper，禁止整条 loop 切 live

- 日期: 2026-08-26
- 状态: accepted
- 决策: `BTCHOUR_MODE=paper`。`GET /exchange/status` 的 `trading_active=true` 只是开盘门，不是切 live。
- 理由: 用户明确拒绝「整条 loop 切 live」。

### 004 实盘只允许 `live_one` 一张 post-only

- 日期: 2026-08-28
- 状态: accepted
- 决策: 每次最多 1 张真单。已有 1 张 resting 不再挂第二张。撤单必须带 `exchange_index`（`KXBTCD` = 2）。
- 理由: 必须一张一张实测；同时乱挂会一边倒吃瘪。

### 005 coupon 门（`952cb97`）

- 日期: 2026-08-29
- 状态: accepted
- 决策:
  - 涨 = 3 分钟 `|impulse| ≥ $100` 才挂 YES（28–42¢）
  - 跌 = `impulse < 0` 且 32–42¢ 看见才挂 NO（29¢ 飞刀不接）
  - 静 / 弱阳不挂
  - 成交 / 淡化 / 反手撤仍要同向 `|impulse| ≥ $100` 且 ask 还在 rest
  - rest $0.25；不吃 taker；0.50–0.70 垫档不上
- 理由: 「看见 32–42¢ 就挂」在 `AUG2911` +0 / ATM 0.42 停 34 分钟，`t_wait_stop` −85%。「3 分钟 $100 才挂」又找不到点。两头都不回退。

### 006 亏了空一小时；0 成交不叠 skip

- 日期: 2026-08-26
- 状态: accepted
- 决策: 亏了下一小时整小时不做 T。连亏不叠。0 成交不是亏损。

### 007 clip 之后本小时不 hop

- 日期: 2026-08-27
- 状态: accepted
- 决策: 第一笔 coupon clip 后这小时不再开新行权价、不翻面。

### 008 不靠放宽门成交

- 日期: 2026-08-26
- 状态: accepted
- 决策: 不把成交门放到 0.45；不吃 0.45–0.70 taker；不放宽 `p=0.30`；不回退成「不挂 YES」。
- 理由: `flex_cheap` / `flex_nowait` 对照更亏或不是现行路径。回放绿不是达成。

### 009 核盘只用表格、纽约时间、中文

- 日期: 2026-08-28
- 状态: accepted
- 决策: `python3 -m btchour board`。不画图。不提交 `latest.json` / `index.json` / `probe.json` / `.env` / `data/` / keys。整点 sweep 只强制提交 `catalog/snapshot/sweep.json` + `replay.json`。

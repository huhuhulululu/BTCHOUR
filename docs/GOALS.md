# 项目目标

> 本文件是**目标层级的唯一真相**。不要从 `huhuhulululu/kalshi`（15 分钟盘）抄日均美元阶段，也**不要抄它的坐等姿态**。
> 小时盘整条近 ATM 阶梯是工作盘（decisions **011**）。目标由用户设定；Agent 不发明 $10/$50/$200 天目标。

## 硬要求

在 Kalshi **BTC 小时盘**（系列 `KXBTCD`，下一个整点收）上，打出**人手那种 coupon clip**。

**提权：** 每小时一百多档（近 ATM `$600` / 32–42¢）是本仓库相对 15 分钟研究仓的操作优势。敏锐度和实际可操作性要压过那边的「没边就坐着」。空仓而阶梯活着 = 策略失败，不是盘口没机会。

达成 ≠ 回放变绿。达成 = 纸盘 / `live_one` 实测走出人工金带子那种单：

| 金带子 | 是什么 |
|---|---|
| `AUG2520` | 砸盘多档 NO 0.20–0.29，挂进出 |
| `AUG2608` | dump NO clip（T78299 rest 0.25，`t_clip` +18% / +0.4502） |

计分：`EV = p · b − (1 − p)`。~20% 是**过滤**，不是每笔保证。不要说每笔都赚 20%。

找不到下单点是策略问题，不是「盘口没机会」。188 档做成 1 笔噪声同理。不能实现就换策略，不要靠放宽门硬成交。

## 不是目标

| 不要 | 为什么 |
|---|---|
| 15 分钟盘 `KXBTC15M` / `KXETH15M` | 那是另一个仓库；小时盘主路径只做 `KXBTCD` |
| 把 15m「坐等」抄到小时盘 | 那边没阶梯；这边每小时一整条近 ATM 阶梯，操作权更高（011） |
| 回放 `flex_skip` / `flex_nowait` 变绿 | 对照，不是达成 |
| 整条 loop 切 live | 用户明确拒绝。`trading_active=true` 不是切 live |
| 把成交门放到 0.45 / 吃 taker / 放宽 p=0.30 | 对照过，更亏或吃瘪 |
| 看见 32–42¢ 就挂 / 孤零零停 25¢ | `AUG2911` 已证明蠢 |
| 日均美元阶段（抄 15m 仓库） | 用户没定 |

## 现行验证规格

- 默认 `BTCHOUR_MODE=paper`，playbook `flex`
- 实盘只允许 **`live_one`：每次 1 张 post-only**。已有 1 张 resting 不再挂第二张
- 亏了下一小时整小时不做 T；0 成交不叠 skip；clip 之后本小时不 hop
- 核盘：`python3 -m btchour board`，中文，纽约时间，只用表格

## 判据

- **进行中**：真 coupon 样本能复现人手规矩（跟带子、32–42¢ 活盘、rest 0.25、10%–50% clip）
- **未达成**：回放 16h 0/0/0 或偶然 1 张小 clip（如 374 +0.0353）都不算达成
- **失败信号**：静/弱阳乱挂、垫档、吃 taker、整条切 live、为成交改门、**空仓而近 ATM 32–42¢ 阶梯活着还只报「没机会」**

## 相关

- 编排：[`catalog/rules/plays.md`](../catalog/rules/plays.md)
- 人手规矩：[`catalog/research/manual.md`](../catalog/research/manual.md)
- 纸盘日记：[`catalog/research/learn.md`](../catalog/research/learn.md)
- 冻结门：[`docs/decisions.md`](decisions.md)
- 编制：[`docs/TEAM.md`](TEAM.md)

# 快速短线 / 做T（`swing_t`）

小时盘和 15 分钟盘的盘口会来回晃。**做T** 不是拿到结算，也不是假装每一笔都锁 20%。做法是：顺着已经走出来的方向进，往返 **10%–50%** 都算正常兑现；动量还在就拿到上限，到 50% 必须走，不吃整段。模型塌了就砍。同一窗口**不翻另一边**。

计分式不变，只是进场不再要求 `EV ≥ 20%`（那是锁仓的门槛）：

```
EV = p · b − (1 − p)
```

做T 用它看期望，但兑现看的是**往返 ROI**。

## 何时进（`swing_t`）

`swing_t` / `impulse_t` 仍只做合约总时长 8–70 分钟的快窗口。**dump coupon 不是这道门**：小时盘 = 下一个整点收盘的 `KXBTCD`，看距收盘还剩 3–70 分钟。4:13 ET 的 5 点盘即使被标 daily、已开一整天，也是这小时的盘。不做 `KXBTC15M`。

| 门 | 默认 | 为什么 |
| --- | --- | --- |
| 距离 | 现货离行权价 ≤ **$600** | 深 ITM 是锁仓的活，不是短线 |
| 买价 | **$0.18–$0.72**（`impulse_t` 上限 **$0.52**） | 砸盘里 0.20 的 NO 是好单；0.60+ 的 YES 是累了追价 |
| 模型 | **p ≥ 55%** | 至少有方向，不是纯抛硬币 |
| 缺口 | **p − ask ≥ 8%** | 盘口相对模型便宜才做 |
| 时间 | 至少还剩 **3 分钟** | 最后一分钟是 BRTI 60s TWAP，不新开 T |

`flex` 扫描顺序：**`lock_hold` → `impulse_wait`（dump coupon）→ `lock_wait`**。默认 **不跑 `impulse_t`**。已经决定、还能吃到 $0.82 的票，先锁，不做 T。`--playbook swing` 才加 `swing_t`。这一小时是快线：量大的近 ATM、强趋势才成交、低估才挂、10%–50% 抛掉；回不来就 scratch / 止损，不盼砸穿的价再落回。

**`impulse_t`** 默认关。对照用：3 分钟 BRTI 至少动 **$100**，涨只做 YES，跌只做 NO，ask **$0.18–$0.52**，p≥52%。纸盘 skip 小时同向 taker（`AUG2605`/`AUG2606`）和涨势 YES（`AUG2614`）把完成账打穿，不是目标单。亏了，**下一小时整小时不做 T**（挂单和 taker 都停）。连续亏不叠坐下一小时。skip 小时是亏损事件的下一张 ticker，不是「之后每一小时」。

**`impulse_wait`**：跟趋势挂 25¢。跌/静只挂 NO（32–42¢，29¢ 飞刀不接）。涨挂 YES（28–42¢）——5 点日盘 $250 档常常没有 32–42¢ NO，中间价在 YES。成交仍只要同向 |impulse| ≥$100。不吃 taker。人手 `AUG2520` 同一小时多档 NO 0.20–0.29 是砸盘带子；涨势空仓才是错的。

门：整条阶梯 $600。跌/静挂 NO **32–42¢**，涨挂 YES **28–42¢**，rest **$0.25**。不要求 |impulse| 才挂。已经反手 ≥$100 不挂、并撤对面。成交只要同向 |impulse| ≥$100。taker 过得了门也不再跳过 coupon。一小时最多同时 rest **3** 档近 ATM（人手 `AUG2520`）。第一笔 clip 后 dead，不 hop。3 分钟印淡了不撤；反弹/淡化 ask==rest 不成交。成交后 10%–50% 带兑现；**8 分钟还没摸到 +10% 就 `t_scratch`**。已经跑出 +10% 的，仍用 80% 硬止损扛反弹标记。`BTCHOUR_IMPULSE_WAIT=0` 可关。旧的宽 wait（26–48¢、$600、不 scratch）只留在 sweep 的 `flex_wait_loose`。

同一小时最多 3 档 coupon。不要在 clip 之后 hop 新行权价，也不要人累了猜反弹。

## 何时出

1. **`lock_on_book`**：只作用于锁仓单。对方买价已经锁住 **20%**，兑现。做T 不在 20% 被提前截死。
2. **`t_clip`**：往返进了 **10%–50%** 带。10% 是下限（缺口没了才走），**50% 是上限**（碰到就走）。中间动量还在就拿着。
3. **`t_trail`**：曾经摸到过 10%，再从最高买价回撤 **4¢** 就走。
4. **`t_stop`**：往返亏 **12%**，砍。只作用于 `impulse_t` / `swing_t`。`impulse_wait` 用 **`t_wait_stop`（−80%）**。旧的 −50% 会在反弹标记上杀掉 `AUG2520` 那种 25¢ 砸盘单。
5. **`t_fade`**：模型 p 比进场掉了 **12 个百分点**，方向不对，先出来。`impulse_wait` **不看淡化**。
6. **`invalidate` / `flatten_time`**：p 掉到 40% 以下，或进入最后约 40 秒 TWAP 窗口。

`lock_hold` 仓位即使在 `flex` 里也**不会**被做T规则刮走，也不会在 TWAP 前被 flatten。锁仓就是拿到结算（或盘口真的锁住 20%）。

## 怎么用

```bash
python3 -m btchour scan --playbook flex
python3 -m btchour probe --playbook swing
python3 -m btchour replay --hours 8 --playbook swing
python3 -m btchour run --once --playbook flex
```

| 变量 | 默认 |
| --- | --- |
| `BTCHOUR_PLAYBOOK` | `flex` |
| `BTCHOUR_SWING_MIN_P` | 0.55 |
| `BTCHOUR_SWING_MIN_GAP` | 0.08 |
| `BTCHOUR_SWING_MIN_ASK` / `MAX_ASK` | 0.18 / 0.72 |
| `BTCHOUR_SWING_TARGET` | 0.10（下限） |
| `BTCHOUR_SWING_MAX_CLIP` | 0.50（上限） |
| `BTCHOUR_SWING_TRAIL` | 0.04 |
| `BTCHOUR_SWING_FADE` | 0.12 |
| `BTCHOUR_SWING_MAX_DISTANCE` | 600 |
| `BTCHOUR_SKIP_AFTER_LOSS` | 1 |

这不是「每笔 T 都赚 10–50%」。10%–50% 是**实测正常兑现带**：不到 10% 不主动走（除非止损/淡化），到 50% 必须落袋，不吃到结算。人的好单是挂进、几分钟走、同一方向。人累了会翻面、换行权价、追 0.60+ 的 YES——机器不做这三件事。空仓仍然是正确动作。

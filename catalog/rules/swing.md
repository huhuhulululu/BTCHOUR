# 快速短线 / 做T（`swing_t`）

小时盘和 15 分钟盘的盘口会来回晃。**做T** 不是拿到结算，也不是假装每一笔都锁 20%。做法是：顺着已经走出来的方向进，往返 **10%–50%** 都算正常兑现；动量还在就拿到上限，到 50% 必须走，不吃整段。模型塌了就砍。同一窗口**不翻另一边**。

计分式不变，只是进场不再要求 `EV ≥ 20%`（那是锁仓的门槛）：

```
EV = p · b − (1 − p)
```

做T 用它看期望，但兑现看的是**往返 ROI**。

## 何时进（`swing_t`）

只做**快窗口**：小时盘或 15 分钟盘（8–70 分钟）。日盘/周盘留给 `lock`。

| 门 | 默认 | 为什么 |
| --- | --- | --- |
| 距离 | 现货离行权价 ≤ **$600** | 深 ITM 是锁仓的活，不是短线 |
| 买价 | **$0.18–$0.72**（`impulse_t` 上限 **$0.52**） | 砸盘里 0.20 的 NO 是好单；0.60+ 的 YES 是累了追价 |
| 模型 | **p ≥ 55%** | 至少有方向，不是纯抛硬币 |
| 缺口 | **p − ask ≥ 8%** | 盘口相对模型便宜才做 |
| 时间 | 至少还剩 **3 分钟** | 最后一分钟是 BRTI 60s TWAP，不新开 T |

`flex` 扫描顺序：**`lock_hold` → `impulse_t` → `impulse_wait` → `lock_wait`**。已经决定、还能吃到 $0.82 的票，先锁，不做 T。`--playbook swing` 才加 `swing_t`。

**`impulse_t`** 跟着 3 分钟 BRTI 动量走。涨了至少 **$100** 只做 YES，跌了只做 NO。ask **$0.18–$0.52**，p≥52%。做完这一笔（赚或亏）这小时不再开新 T，**不翻对面**。锁仓单走完，这小时也不再开 T。亏了，**下一小时只空反方向**（累了追反手）；同向 **taker** 还可以做，但 **不再挂 `impulse_wait`**（`AUG2518` NO 止损后挂同向 wait，把本来 +17% 的 YES 换成了 −70%）。

**`impulse_wait`（默认已换成 dump coupon / `dump_gap`）**：**只挂 NO**。旧策略是「卖一 26–48¢ 就挂 25¢」，纸盘第一笔把已经砸到 29¢ 的 `T78499` 吃进去，反弹标到 3¢，`t_wait_stop` −89%。人手好单不是接飞刀，是挂在 **卖一还在 32–42¢** 的近 ATM coupon 上（`AUG2520` 23:20 `T78699` ask 0.35 才是带子；23:09 ask 0.27 已经砸穿）。

门：动量 ≤−$100，taker 过不了门，NO ask **$0.32–$0.42**，离现货 ≤ **$150**，挂 **$0.25**。多张只留离现货最近的一张。3 分钟印淡了不撤；只在砸盘还在时成交（反弹里不填）。卖一打到 rest 算成交。成交后 10%–50% 带兑现；**8 分钟还没摸到 +10% 就 `t_scratch`**，不再拿 80% 去扛死单。已经跑出 +10% 的，仍用 80% 硬止损扛反弹标记。涨势不挂 YES。`BTCHOUR_IMPULSE_WAIT=0` 可关。旧的宽 wait（26–48¢、$600、不 scratch）只留在 sweep 的 `flex_wait_loose`。

同一小时只盯**第一次进场的那张合约**。不要在砸盘里换行权价，也不要人累了猜反弹。

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

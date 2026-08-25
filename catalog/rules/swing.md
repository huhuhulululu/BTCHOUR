# 快速短线 / 做T（`swing_t`）

小时盘和 15 分钟盘的盘口会来回晃。**做T** 不是拿到结算，也不是假装每一笔都锁 20%。做法是：在 ATM 附近、模型和卖价有缺口时进，盘口给出一截就走，模型塌了就砍，同一窗口可以翻另一边。

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
| 买价 | **$0.28–$0.72** | 太便宜是彩票，太贵锁不住一截利润 |
| 模型 | **p ≥ 55%** | 至少有方向，不是纯抛硬币 |
| 缺口 | **p − ask ≥ 8%** | 盘口相对模型便宜才做 |
| 时间 | 至少还剩 **3 分钟** | 最后一分钟是 BRTI 60s TWAP，不新开 T |

`flex` 扫描顺序：**`lock_hold` → `swing_t` → `lock_wait`**。已经决定、还能吃到 $0.82 的票，先锁，不做 T。

同一小时只盯**第一次进场的那张合约**：clip 之后可以翻对面；`t_fade` / 失效之后这小时不再做 T。不要在砸盘里换行权价连打。

## 何时出

1. **`lock_on_book`**：对方买价已经锁住 **20%**，直接兑现（比 12% 更好就拿）。
2. **`t_clip`**：往返 ROI ≥ **12%**，且 `p − bid` 已经不够大，不再让它跑。
3. **`t_trail`**：曾经摸到过 12%，再从最高买价回撤 **4¢** 就走。
4. **`t_fade`**：模型 p 比进场掉了 **12 个百分点**，方向不对，先出来。同一小时不再新开 T。
5. **`invalidate` / `flatten_time`**：p 掉到 40% 以下，或进入最后约 40 秒 TWAP 窗口。

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
| `BTCHOUR_SWING_MIN_ASK` / `MAX_ASK` | 0.28 / 0.72 |
| `BTCHOUR_SWING_TARGET` | 0.12 |
| `BTCHOUR_SWING_TRAIL` | 0.04 |
| `BTCHOUR_SWING_FADE` | 0.12 |
| `BTCHOUR_SWING_MAX_DISTANCE` | 600 |

这不是「每笔 T 都赚 12–20%」。12% 是**出货目标**，不是保证。缺口消失、模型反转、或最后一分钟 TWAP，都可以把一截利润吐回去。空仓仍然是正确动作。

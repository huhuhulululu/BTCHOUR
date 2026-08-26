# Verified live tests (2026-08-25)

Source: Kalshi public Trade API + event live_data (BRTI-like 1s series).

## Settlement

- Rule holds: last 60 one-second prints before the ET hour, simple average.
- `KXBTCD-26AUG2513` TWAP = **79147.04** → band `(79099.99, 79199.99]`.
- Last 16 completed hours all printed a $100 band (see `catalog/snapshot/hours.json`).

## 20% EV on the live 3pm book

At ~18:04 UTC, BRTI ≈ 79290, 56 minutes left:

- Best taker EV ≈ **+3.3%** (`NO` `T79799.99` ask 0.94, p≈97.5%).
- 193 sides had if-win ≥ 20% (cheap OTM/ATM). Almost all had low p.
- 6 sides had p ≥ 95%. Those asks were 0.94–0.99, so if-win < 20%.
- Intersection of if-win 20% + p 95% + EV 20% = **0**.

## Last-hour replay (`KXBTCD-26AUG2513`)

`T79099.99` yes_ask traded as low as 0.48 in the final minutes **and settled YES**. That is not a 20% locked trade: at those prints spot was hugging the strike and model p was ~58–85%. The gate rejected them.

Closest rejected EV: 16:48 UTC `T79199.99` YES ask 0.62, p=76%, EV≈19.7%. It would have **lost** (settled NO).

## Eight-hour minute replay

Command: `python3 -m btchour replay --hours 8`.

First pass used raw 1-minute realized vol (~0.30) and took two **hold-to-settle** tickets:

| Hour | Ticket | EV | Result |
| --- | --- | --- | --- |
| `KXBTCD-26AUG2511` | NO `T79499.99` @ 0.78 | +21.9% | win +0.21 |
| `KXBTCD-26AUG2510` | YES `T78599.99` @ 0.77 | +21.9% | **loss −0.78** (BTC sold off through the strike) |

Net −0.57. After flooring vol at `BTCHOUR_ANNUAL_VOL` (0.55), hold-to-settle on the same 8 hours is **0 takes / 0 pnl**.

## Flex playbook (not hold-to-settle)

`python3 -m btchour replay --hours 8 --playbook flex` on the same window, default size (up to 10 contracts):

| Hour | Play | Ticket | Exit | ROI | PnL |
| --- | --- | --- | --- | --- | --- |
| `KXBTCD-26AUG2510` | scalp YES `T78599.99` @ 0.62 | 3 minutes later p collapsed | `invalidate` @ 0.51 | −22.6% | −1.44 |
| same minute | scalp NO `T78599.99` @ 0.49 | bid paid 0.67 | `lock_on_book` | **+29.0%** | +1.47 |

Net **+0.03**. Other 7 hours idle. Hold-edge tickets still 0.

That dump hour is the point of flexibility:

- Holding the YES to expiry would have been a full-stake loss (band settled `78499.99–78599.99`).
- Invalidating at 0.51 cut it to −23% instead of −100%.
- Flipping to NO and selling when the bid locked 20% is the trade style that hold-to-settle cannot take.

Live 3pm book (`KXBTCD-26AUG2515`, ~37 minutes left, BRTI ≈ 79260): **0 hold tickets, 0 scalps**. Best raw EV sides are cheap OTM lotteries with p ≪ 60%.

This is still not “every fill makes 20%.” One clip locked +29%; the other was a cut. Empty hours are normal.

## Lock playbook (稳健 20%)

`BTCHOUR_PLAYBOOK=lock` only does this path. Gates: σ ≥ 3.2, p ≥ 99.8%, b ≥ 20%, EV ≥ 20%. Taker only at ≤ $0.82; otherwise rest $0.83 (`lock_wait`, paper status `working`).

Live 4pm window (2026-08-25 ~19:05 UTC, BRTI ≈ 79094):

- Hourly / daily / weekly / 15m **lock_hold takes: 0**
- One wait: daily **NO** `KXBTCD-26AUG2517-T81249.99` (ask 0.99, p≈99.97%, EV at $0.83 wait ≈ +20.4%)
- Cheapest already-decided touch is still $0.98–$0.99, so if-win is ~1–2%, not 20%
- Paper `run --once` recorded that wait as `working`, not a fill

8-hour hourly replay with candle **ask lows** (`replay --hours 8 --playbook lock`): **0 takes / 0 pnl**. The 95% / $0.81 tickets that used to look like 20% are rejected (σ≈2.3). That is the point.

## 做T / short swing (2026-08-25 ~19:26 UTC)

Default is `BTCHOUR_PLAYBOOK=flex`: `lock_hold` first, then `swing_t`, then `lock_wait`. 12% is a **clip target**, not a locked 20%.

Live 4pm book (BRTI ≈ 79050, ~33 minutes left):

- **0 lock_hold**, **0 swing_t**. ATM is already tight: closest YES `T78999.99` ask 0.68 / p≈56% (gap −12%); YES `T79199.99` ask 0.22 / p≈33%.
- One wait: daily NO `KXBTCD-26AUG2517-T80999.99` rest $0.83 (touch $0.99, p≈99.98%)
- 15m `KXBTC15M-26AUG251530` already one-sided (YES 0.1¢ / NO $1.00) — not a T
- Paper `run --once --playbook flex` recorded the wait as `working`

First 8-hour swing replay (no same-hour discipline) **overtraded the dump**: 10 takes / 6 wins / **−4.74** at 10 contracts. `AUG2510` hopped five nearby strikes.

After “one ticker per hour, flip only after a clip, stop after a fade, fade at 12 points”:

`replay --hours 8 --playbook swing` (same window, default 10 contracts):

| Hour | Ticket | Exit | ROI | PnL |
| --- | --- | --- | --- | --- |
| `AUG2514` | YES `T79199.99` @ 0.48 | `lock_on_book` | +23% | +1.16 |
| `AUG2513` | NO `T79299.99` @ 0.44 | `t_fade` | −27% | −1.23 |
| `AUG2511` | NO `T79399.99` @ 0.70 | `t_fade` | −17% | −1.21 |
| `AUG2510` | YES `T78399.99` @ 0.71 | `t_clip` | +16% | +1.17 |
| same ticker | NO `T78399.99` @ 0.65 | `t_fade` | −23% | −1.53 |
| `AUG2509` | YES `T78699.99` @ 0.55 | `lock_on_book` | +21% | +1.18 |

**6 takes / 3 wins / −0.47**. `flex` on the same candles is identical (no lock_hold print). Faster fade cut a previous `AUG2513` 20% lock, and also cut the dump flip from −76% to −23%. Empty hours are still the common case.

## Impulse 做T (2026-08-25 ~20:21 UTC)

Value-gap T was fading into dumps. New default `flex` is **`lock_hold` → `impulse_t` → `lock_wait`**.

`impulse_t` rules that survived a 16-hour sweep:

- 3-minute BRTI move ≥ **$100**, same direction only
- ask **$0.28–$0.52**, `p ≥ 52%`, `p − ask ≥ 2%`
- replay now loads strikes along the **spot path**, not just the settlement band
- hard **−12% stop** (`t_stop`) plus the old 12% clip / 20% lock
- one impulse per hour; no revenge flip

`replay --hours 16 --playbook flex` (10 contracts): **8 takes / 4 wins / −0.43**.

| Hour | Side | Ask | Exit | ROI | PnL |
| --- | --- | --- | --- | --- | --- |
| `AUG2516` | NO | 0.50 | `t_clip` | +15% | +0.76 |
| `AUG2513` | YES | 0.50 | `t_stop` | −37% | −1.93 |
| `AUG2511` | YES | 0.47 | `lock_on_book` | +26% | +1.26 |
| `AUG2510` | NO | 0.50 | `t_clip` | +19% | +0.96 |
| `AUG2509` | NO | 0.49 | `t_stop` | −15% | −0.75 |
| `AUG2508` | NO | 0.51 | `t_clip` | +16% | +0.86 |
| `AUG2504` | NO | 0.49 | `t_stop` | −13% | −0.65 |
| `AUG2501` | NO | 0.48 | `t_stop` | −19% | −0.95 |

Clips and 20% locks are green. The leftover loss is **1-minute stop gaps** (especially `AUG2513` −37%). A 3-second live loop should fill closer to −12%. This is still not “20% every hour.” Live 5pm book right now: impulse ≈ +$44, **0 fills**.

## Overnight paper loop (into 2026-08-26 00:07 UTC)

The flex paper loop ran ~4 hours: **0 T fills**. Three `$0.83` waits cancelled as `wait_invalid`. The 5pm dump (`AUG2517`, 78912 → 78190) printed a **−$303** 3-minute tape impulse at 20:49 and still took nothing — replay of that hour is also 0 takes. ATM NO was already above $0.52. The loop then went silent for **3 hours** (`21:01` → `00:06`) and missed `AUG2519` clip +17% and `AUG2518` stop −22%.

Next learning step is in the engine: tape impulse + reject journal (`btchour learn`) and a light 45s sync so the loop does not stall. See [`learn.md`](learn.md).

## Order book

ATM books are two-sided and deep at 1–4 cents. `yes_ask` on the market object is the real take price; 1-cent bids on both sides are inventory, not the touch for a 20% clip.

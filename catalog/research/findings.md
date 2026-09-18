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

## Manual tape (2026-08-25 evening)

Account fills (read-only). Same-side clips on the dump were the working rule: maker NO at $0.20–$0.25, out in 2–16 minutes, **+85% to well past 30%**. Holding NO from $0.24 to $0.92 (+280%) ate the whole move — not repeatable.

After that, direction broke: flip YES at $0.61 on `T78499`, hop strikes, chase $0.58–$0.86 YES, then `AUG2521` YES→NO flip. That is fatigue, not a new edge.

Engine change: T realizes a **10%–50%** band (floor / cap), **no flip**, **skip the opposite side next hour after a loss**, ask floor **$0.18**, ask cap **$0.52**. See [`manual.md`](manual.md).

`replay --hours 16 --playbook flex` after that change (AUG2505–AUG2520, 10 contracts): **6 takes / 3 wins / −0.84**.

| Hour | Side | Exit | ROI |
| --- | --- | --- | --- |
| `AUG2516` | NO | `t_clip` | +15% |
| `AUG2513` | YES | `t_stop` | −37% |
| `AUG2511` | YES | `lock_on_book` | +26% |
| `AUG2509` | NO | `t_stop` | −15% |
| `AUG2508` | NO | `t_clip` | +16% |
| `AUG2518` | NO | `t_stop` | −22% |

Clips and the 20% lock are still green. Leftover loss is still **1-minute stop gaps**. This is not “10% every hour.”

## Repeated sweep (2026-08-26 ~01:17 UTC)

`python3 -m btchour sweep --hours 16` caches each hour once, then replays flex / swing / lock with skip on/off, on both 16h and 24h.

First pass (skip the **whole** next hour, lock then T still allowed):

| Run | Takes | Wins | PnL |
| --- | ---: | ---: | ---: |
| flex skip 16h | 6 | 3 | **−0.84** |
| flex no-skip 16h | 8 | 5 | **+0.98** |
| swing skip 16h | 11 | 6 | +0.37 |
| lock 16h | 0 | 0 | 0 |
| flex skip 24h | 11 | 5 | −1.20 |
| lock 24h | 1 | 1 | +1.89 |

The two hours the blunt skip dropped were clips: `AUG2510` same-direction NO +19%, and `AUG2519` opposite YES +17%. 24h flex also took `AUG2423` lock_hold +20% and then an ATM impulse T that stopped **−43%**. That second bite is a bug.

After the fix (lock closes the hour for T; skip only the **opposite** side):

| Run | Takes | Wins | PnL |
| --- | ---: | ---: | ---: |
| flex skip 16h | 7 | 4 | **+0.12** |
| flex no-skip 16h | 8 | 5 | +0.98 |
| swing skip 16h | 12 | 6 | −1.07 |
| lock 16h | 0 | 0 | 0 |
| flex skip 24h | 11 | 6 | **+2.18** |
| flex no-skip 24h | 13 | 7 | +2.09 |
| lock 24h | 1 | 1 | +1.89 |

`AUG2510` NO after `AUG2509` NO stop is now taken. `AUG2519` YES after `AUG2518` NO stop stays skipped (tired flip). `AUG2423` keeps the lock and does not open the −43% T. Value-gap `swing` still overtrades. Ask cap stays **$0.52** — loosening it is how the 16h losers appeared.

Live `AUG2522` at ~01:15 UTC: BRTI ≈ 78725, impulse ≈ −$56, **0 lock / 0 T**. Paper ledger still **0 completed fills** (two $0.83 waits only). This is not “every hour prints 10–50%.”

## Repeated sweep (2026-08-26 ~01:22 UTC)

Same cached tapes. Question this round: why `AUG2520` manual 20–25¢ NO is 0 engine takes.

Minute closes **do** print those prices (`T78599` NO close 0.19–0.27 while the dump is on). The gate that blocks them is `impulse_min_p=0.52`: model p on those strikes is **0.31–0.38**. ATM 0.46–0.51 is what survives p≥52%.

Tried lowering p to 30% and cap ask at $0.35 (and $0.52). Also stopped using the 40% invalidate on tickets that already entered below 40%.

| Run | Takes | Wins | PnL |
| --- | ---: | ---: | ---: |
| flex skip (default) 16h | 7 | 4 | **+0.12** |
| flex cheap p30/ask35 16h | 15 | 6 | **−5.60** |
| flex cheap p30/ask52 16h | 15 | 8 | −2.48 |
| flex skip 24h | 11 | 6 | **+2.18** |
| flex cheap p30/ask35 24h | 24 | 8 | −8.65 |

`AUG2520` cheap NO @ 0.27 then `t_stop` **−57%** on the next minute gap. The human clip to 0.37–0.51 does not survive 1-minute stops. Default stays p≥52% / ask≤$0.52. Sweep now always prints the cheap variant so the next round does not re-guess this.

Live `AUG2522` ~01:20 UTC: impulse faded to +$20s, **0 T**, paper completed PnL still **0**.

## Maker wait under the dump (2026-08-26 ~01:58 UTC)

Cheap **taker** NO (p 30% / ask 35¢) is still the red tape (−4.38 / −7.43 with wait on). Human clips were **resting 20–25¢ NO** while the touch was still ~32¢, then holding the bounce.

Symmetric 25¢ waits (YES on rallies) printed three −50% stops and put 24h **below** the +2.18 ATM baseline. Dump-only wait plus skip-after-loss still ate `AUG2519` (−70% wait instead of the +17% YES clip). Fix: after a losing T, skip **wait** next hour; same-direction **taker** is still allowed.

`python3 -m btchour sweep --hours 16` after that (10 contracts, cached tapes AUG2506–AUG2521 / AUG2422–AUG2521):

| Run | Takes | Wins | PnL |
| --- | ---: | ---: | ---: |
| flex skip + dump wait (default) 16h | 9 | 6 | **+1.29** |
| flex no-wait 16h | 7 | 4 | +0.12 |
| flex skip + dump wait 24h | 13 | 9 | **+5.03** |
| flex no-wait 24h | 11 | 6 | +2.18 |
| flex cheap p30/ask35 16h | 15 | 7 | −4.38 |

Wait fills that survived: `AUG2514` / `AUG2513` NO @ 0.25 clip +10%; `AUG2504` NO @ 0.25 clip +41% (replaces the old ATM −13% stop). `AUG2513` also blocks the ATM YES `t_stop` −37%. Leftover: `AUG2507` wait stop −51%. `AUG2519` is now empty (bruised hour, no wait). This is not “every wait prints 10–50%.”

Default stays dump-only `impulse_wait` on. Sweep still prints `flex_nowait` and `flex_cheap`.

## Fade-hold, dump-only fill, 80% wait stop (2026-08-26 ~03:27 UTC)

Gold tape `AUG2520`: human `T78699` NO maker 0.25 at **23:11:21Z**, sold 0.51 at 23:28. First wait code offered then **cancelled** when impulse faded −112 → −87. Fade-hold + any-extreme promote then filled the **23:14 bounce** (yes bid 0.86) and `t_wait_stop` −51% at 23:15.

Fixes that stay default:

- Keep the rest through fade; cancel only on a ≥+$100 flip.
- Promote a dump NO wait only while impulse is still negative. Bounce prints do not fill.
- `ask == rest` is a fill (`AUG2520` 23:20 `no_low=0.25`).
- Wait hard stop **80%**. 50% dies on bounce marks; 99% lets `AUG2507` settle −100%.

Same old tapes as the +1.29 / +5.03 sweep (AUG2506–AUG2521 / AUG2422–AUG2521): default **15 / 13 / +7.98** and **21 / 18 / +13.46**. `AUG2520` is now wait NO @ 0.25 → `t_clip` **+41.5%** at 23:21.

Official current window (`--hours 16`, AUG2508–AUG2523 / AUG2500–AUG2523, 10 contracts):

| Run | Takes | Wins | PnL |
| --- | ---: | ---: | ---: |
| flex skip + dump wait (default) 16h | 14 | 13 | **+9.89** |
| flex no-wait 16h | 7 | 4 | +0.12 |
| flex skip + dump wait 24h | 20 | 17 | **+11.98** |
| flex no-wait 24h | 9 | 4 | −1.27 |
| flex cheap p30/ask35 16h | 15 | 11 | +2.85 |
| flex cheap p30/ask35 24h | 23 | 13 | −1.89 |

`AUG2520` / `AUG2521` (`T78399` @ 00:38, the live dump the old paper loop only logged as taker `blocked`) now clip. Leftover: `AUG2507` wait stop −81%. Cheap taker 24h is still red — do not lower `impulse_min_p`. This is replay, not paper.

## First paper wait (2026-08-26 ~05:06 UTC) — wrong strike

Paper finally filled a dump wait. It was not a 10–50% clip.

`AUG2602` `T78499` NO @ **0.25 maker**, 10 contracts, cost 2.5. Rest at 05:06:18Z, impulse −$104, then-ask **0.29**, spot **78689.70**, model p 33.3%. Promoted. Bounce marked bid 0.12 then 0.03. `t_wait_stop` **−88.8%**, realized **−2.2204**. Peak bid 0.23. The 80% stop did what it was told.

Same scan also wanted `T78599` rest 0.25 under ask **0.42** (p 42.0%, ~$90 from spot). Sort was `(ask − rest)`, so the **cheapest ask just above 25¢** won. Human rests the dump ATM. `T78499` is ~$190 below spot — further OTM NO / deeper ITM YES. The 05:20 dump that could have saved the nearer strike could not revive T78499.

Nearest-strike alone is not enough. `AUG2520` 23:09 nearest is `T78799` ask 0.44 — rest 0.25 never fills. Human / gold replay is `T78699` when ask is still **0.32–0.35**. Paper `T78499` ask **0.29** is the already-dumped knife.

**Strategy switch (default):** dump coupon, not “rest 25¢ under any 26–48¢ dump ask.”

- Rest only if NO ask is **$0.32–$0.42** and strike is within **$150**
- Then nearest strike
- **Scratch** if the bid never makes +10% in 8 minutes. Do not hold a dead coupon to −80%
- Old wide wait stays as sweep `flex_wait_loose`

Do not loosen taker p=0.30. Do not rest YES on rallies. After this loss, skip wait on `AUG2603`.

Paper completed: **1 / 0 / −2.2204**. Replay green is not 达成.

Cache sweep after the switch (AUG2601–AUG2502, 10 contracts):

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (new default) | 10 / 7 / **+1.03** (1 scratch) | 15 / 10 / **+2.97** |
| flex_nowait | 5 / 3 / +0.01 | 8 / 4 / −0.53 |
| flex_wait_loose (old 26–48¢) | 10 / 8 / +5.51 | 17 / 13 / +8.24 |
| flex cheap p30/ask35 | 14 / 6 / −3.94 | 22 / 9 / −7.92 |

`AUG2520` still clips +41.5% under the new default. Loose wait is greener on 1-minute bars because it still eats 29¢ knives. Paper already proved that path. Default stays dump coupon while it beats nowait. Cheap taker still red.

## AUG2603 close + skip-hour stuck (2026-08-26 ~07:09 UTC)

`AUG2603` was the designed skip-wait hour after the `AUG2602` knife. Paper 0 fills. Journal 58 coupon-quality waits (`T78899` / `T78799` ask 0.37–0.40). Same-dir taker never cleared p 52%. Minute replay coupon/nowait/loose: **0 takes**. Cheap taker NO @ 0.19 `t_stop` −39%.

New window (AUG2603–AUG2512 / AUG2603–AUG2504):

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 8 / 5 / **−1.19** | 14 / 9 / **+0.94** |
| flex_nowait | 3 / 1 / −2.21 | 8 / 4 / −0.53 |
| flex_wait_loose | 8 / 6 / +3.29 | 16 / 12 / +6.22 |
| flex cheap p30/ask35 | 14 / 5 / −5.55 | 22 / 8 / −7.95 |

16h went from +0.07 to −1.19 because `AUG2511` +26% rolled out of the window, not because coupon lost this hour. Coupon still beats nowait. Keep it. Cheap still red.

Paper bug: `Store.session_memory()` rebuilds from trades and drops `skipped_event`, so every later hour looked like the skip hour. Skip hour is now the next ticker after `last_loss_event`. `AUG2604` is the first live coupon hour.

Paper completed still **1 / 0 / −2.2204**. Replay green is not 达成.

## AUG2604 close — right rest, fade fill (2026-08-26 ~08:00 UTC)

First live coupon hour. Rest 07:10:33Z `T78899` NO 0.25 under ask **0.37**, impulse −$115. One wait. No YES. No hop. Fade held.

Fill 07:41:30Z when ask printed 0.25 and 3-minute impulse was ~0, spot already +$42 from the rest. Peak bid 0.27 (peak ROI 2.5% after fees). `t_scratch` at 07:49:30Z bid **0.12**, **−1.374** (−55%). Scratch did its job.

Not a 29¢ knife. The rest matched the human book. The fill did not match the human dump. Promote now requires impulse still ≤−$100. Keep the rest through fade. Bounce / fade ask==rest do not fill.

Minute replay this hour: coupon/nowait/loose **0**. Cheap YES @ 0.20 clipped +21% — do not chase that.

New window (AUG2604–AUG2513 / AUG2604–AUG2505) after dump-only fill:

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 6 / 4 / **−0.47** | 13 / 10 / **+6.98** |
| flex_nowait | 3 / 1 / −2.21 | 7 / 4 / +0.12 |
| flex_wait_loose | 7 / 6 / +4.93 | 13 / 11 / +11.83 |
| flex cheap p30/ask35 | 13 / 4 / −6.33 | 21 / 8 / −7.61 |

Coupon still beats nowait. `AUG2520` still +41.5%. Paper completed **2 / 0 / −3.5944**. Skip wait on `AUG2605`. Replay green is not 达成.

## AUG2605 close — skip-wait held; same-dir taker re-armed skip (2026-08-26 ~09:00 UTC)

Designed skip-wait hour after the `AUG2604` fade-fill scratch. Not a live dump-coupon test.

Paper: 479 scans, impulse −$140 / +$211. Journal 10 coupon books (`T78799` ask 0.39, then `T78699` ask 0.41). Engine did not rest (skip). Rally YES stayed `blocked`. Same-dir `impulse_t` NO `T78699` @ **0.49** (p 56%, impulse −$139) — allowed by the AUG2518 rule — peaked 0.55 and `t_stop` **−1.243**. That loss set `skipped_event=KXBTCD-26AUG2606`.

Skip treadmill: coupon scratch → skip next wait → losing taker on the skip hour → skip the hour after that. Leave the rule. The switch trigger is still the next **closed live coupon hour**, not a skip hour. `AUG2606` sits wait; `AUG2607` is the next live rest unless this hour also loses a T.

15m `lock_wait` `KXBTC15M-26AUG260500` rest 0.83 is a current 15-minute window, not dump coupon, and does not block hourly wait.

Minute replay this hour: coupon wait **0**. Same 0.49 NO `t_stop` −12.8% on 1-minute bars. Cheap YES @ 0.24 `t_stop` −25% — do not chase.

New window (AUG2605–AUG2514 / AUG2605–AUG2506) after dump-only fill:

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 6 / 4 / **+0.81** | 13 / 9 / **+5.10** |
| flex_nowait | 3 / 1 / −0.93 | 8 / 4 / −0.53 |
| flex_wait_loose | 7 / 6 / +6.21 | 13 / 10 / +9.95 |
| flex cheap p30/ask35 | 13 / 4 / −6.11 | 21 / 8 / −8.69 |

16h rose because `AUG2513` YES stop −1.93 rolled out, not because this hour printed a clip. Coupon still beats nowait. `AUG2520` still +41.5%. Cheap still red. Paper completed **3 / 0 / −4.8374**. Skip wait on `AUG2606`. Replay green is not 达成.

## AUG2606 mid-hour — skip-hour taker loss no longer chains skip (2026-08-26 ~09:30 UTC)

Skip-wait held: 36 coupon journals, 0 rests. First dump at 09:16 `T78599` ask **0.36**, impulse −$102 — the human 32–42¢ book. Same-dir `impulse_t` NO `T78499` @ **0.46** at 09:20 (p 55%, impulse −$170) peaked 0.49 and `t_stop` **−0.6455**.

Second consecutive losing hour (`AUG2605` then `AUG2606`). Chaining skip would sit `AUG2607` too and the dump coupon never goes live. Sit-out stays one hour after an isolated loss. Consecutive losing hours do not stack another skip. No more T on the hour just lost. `AUG2607` is the next live coupon rest.

Paper completed **4 / 0 / −5.4829**. Hour still open; no sweep. Replay green is not 达成.

## AUG2606 close — skip hour, not a live coupon test (2026-08-26 ~10:00 UTC)

Skip-wait held: 579 scans, 36 coupon journals (`T78599` / `T78499` / `T78399`), 0 `impulse_wait` fills. 09:16 `T78599` ask **0.36** was the human book and was skipped. 09:20 same-dir `impulse_t` NO `T78499` @ 0.46 `t_stop` **−0.6455**. Remainder of the hour took no more T. Consecutive-loss patch left `skip_next=False`, so `AUG2607` is the live coupon rest.

Do not treat empty coupon on a skip hour as coupon failure. Switch only after the next **closed live coupon hour**.

Minute replay this hour is `incomplete data` (TWAP / live_data not published at the close print). Sweep window now starts AUG2606:

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 6 / 4 / **+0.81** | 12 / 8 / **+1.77** |
| flex_nowait | 3 / 1 / −0.93 | 8 / 4 / −0.53 |
| flex_wait_loose | 7 / 6 / +6.21 | 12 / 9 / +6.62 |
| flex cheap p30/ask35 | 14 / 5 / −5.79 | 22 / 8 / −9.16 |

16h unchanged (this hour replayed 0). 24h fell because `AUG2506` +3.33 rolled out. Coupon still beats nowait. `AUG2520` still +41.5%. Cheap still red. Paper completed **4 / 0 / −5.4829**. `AUG2607` is live coupon. Replay green is not 达成.

## AUG2607 close — live coupon hour, no dump (2026-08-26 ~11:00 UTC)

`skip_next=False`. 450 scans, **0 wait journals**, 0 T fills. Impulse floor **−$87**, never ≤−$100, so no 32–42¢ coupon book to rest. Rally YES to +$117 stayed `blocked` (`T78899` p 31¢ / `T78799` ask 0.59). `lock_wait` YES `T77399` 0.83 `wait_invalid`.

Empty coupon is **no dump**, not coupon failure. Do not switch. The switch trigger is a live hour that actually dumped and still could not print a human-style clip.

`AUG2606` minute tape is now complete: band 78399.99–78499.99, replay same-dir NO @ 0.51 `t_stop` −0.75 (paper was 0.46 / −0.6455). `AUG2607` tape still `incomplete data` at the close print.

New window (AUG2607–AUG2516 / AUG2607–AUG2508):

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 7 / 4 / **+0.06** | 13 / 8 / **+1.02** |
| flex_nowait | 4 / 1 / −1.68 | 9 / 4 / −1.27 |
| flex_wait_loose | 8 / 6 / +5.46 | 13 / 9 / +5.87 |
| flex cheap p30/ask35 | 15 / 5 / −6.54 | 22 / 8 / −7.88 |

16h fell because `AUG2606` replay taker −0.75 rolled in. Coupon still beats nowait. `AUG2520` still +41.5%. Cheap still red. Paper completed **4 / 0 / −5.4829**. `AUG2608` is the next live coupon rest. Replay green is not 达成.

## AUG2608 mid-hour — first paper dump-coupon clip (2026-08-26 ~11:45 UTC)

Live coupon hour (`skip_next=False`). The dump printed and the coupon clipped in the human band.

| Time | Event |
| --- | --- |
| 11:38:00Z | Impulse ≤−$100. `T78399` ask **0.49** stayed `blocked` (p 41–49% < 52%) |
| 11:38:29Z | Nearest strike became `T78299` ask **0.46**, then **0.43**. Taker still blocked (p 36–44%) |
| 11:39:40Z | Rest `T78299` NO **0.25** under ask **0.34**, impulse **−$226**, spot 78350, p 42.7%, ~$50 from spot. One wait. No YES. No hop |
| 11:40:08Z | Filled during dump (28s), impulse still **−$191**. Dump-only fill held |
| shortly after | `t_clip` at bid **0.31**, **+18.0%**, pnl **+0.4502**. Band 10%–50%. Not a locked 20% |

`raw.peak_bid=0.23` is below entry 0.25 while the exit note is clip 18% @ 0.31 — a mark quirk, not a failed peak. After the fill, journal diagnosed `T78399` ask 0.49 p 56% (`open`) and did not take a second T. The win cleared session memory (`last_loss_event=None`, `skip_next=False`).

This is the first paper human-style dump NO clip: 32–42¢ book, rest 25¢, fill while the dump is on, clip in 10–50%. Do not switch. Do not loosen taker p. Do not rest YES. Hour still open until 12:00 UTC — sweep after the close. Leftover `lock_wait` (stale 15m id 19, far OTM `T83499`) is not dump coupon.

Paper completed **5 / 1 / −5.0327**. Replay green is not 达成.

## AUG2608 close — live coupon dumped and clipped (2026-08-26 ~12:00 UTC)

Closed ledger is the same clip: one dump coupon, `t_clip` **+0.4502** (+18% at bid 0.31). 1007 scans, 11 wait journals, 10 open diagnoses. Impulse floor **−$246**, ceiling +$97. After the fill, rally YES stayed `no_impulse` — no YES rest, no second T. Win cleared session (`skip_next=False`). **`AUG2609` is the next live coupon rest.**

This closed live coupon hour dumped and printed a human-style clip. Do not switch.

Minute replay this hour is `incomplete data` at the close print. `AUG2607` tape is now complete: band 78699.99–78799.99, replay 0 (matches paper).

New window (AUG2608–AUG2517 / AUG2608–AUG2509):

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 6 / 3 / **−0.70** | 12 / 7 / **+0.15** |
| flex_nowait | 3 / 0 / −2.44 | 8 / 3 / −2.14 |
| flex_wait_loose | 7 / 5 / +4.70 | 12 / 8 / +5.01 |
| flex cheap p30/ask35 | 15 / 4 / −8.68 | 22 / 8 / −8.74 |

16h fell because `AUG2516` taker clip +0.76 rolled out and `AUG2608` replayed 0 (live +0.45 is not on the minute tape yet). Coupon still beats nowait. `AUG2520` still +41.5%. Cheap still red. Do not switch on the 29¢ knife. Do not chase YES.

Paper completed **5 / 1 / −5.0327**. Replay green is not 达成.

## AUG2608 tape complete + AUG2609 live rest (2026-08-26 ~12:15 UTC)

`AUG2608` band is **78399.99–78499.99**. `T78299` settled YES — holding the paper NO to expiry would have been −100%. The +18% clip was the right exit.

Minute replay does not match paper. Replay 11:39 took `T78399` NO taker @ **0.52** and `t_stop` −1.05. Live blocked that book (ask 0.49, p&lt;52%) and rested `T78299` 0.25, which clipped. Loose wait replayed the 25¢ rest and `t_clip` +97% on 1-minute closes — do not make that the default. Default coupon also ate the replay taker, so the window is now:

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 7 / 3 / **−1.74** | 13 / 7 / **−0.89** |
| flex_nowait | 4 / 0 / −3.49 | 9 / 3 / −3.18 |
| flex_wait_loose | 8 / 6 / +7.13 | 13 / 9 / +7.43 |
| flex cheap p30/ask35 | 16 / 4 / −9.27 | 23 / 8 / −9.33 |

Coupon still beats nowait. `AUG2520` still +41.5%. Cheap still red. Do not switch. Replay green is not 达成.

`AUG2609` live coupon is working: 12:06:29 rest `T78299` NO **0.25** under ask **0.41**, impulse −$100, spot 78414 (~$115). One wait. Dump ask printed 0.37, not 0.25 — no fill. Fade to +$62 did not cancel. Still working at 12:17 with impulse ~−$44. Do not loosen taker p. Do not rest YES.

Paper completed **5 / 1 / −5.0327**.

## AUG2609 mid-hour — flip cancel, then second dump rest (2026-08-26 ~12:30 UTC)

First rest never filled. At 12:22:52 impulse **+$102** cancelled it `wait_invalid` — the ≥+$100 flip rule, not a fade cancel. Rally YES `T78499` / `T78599` / `T78699` stayed `blocked`. No YES rest.

12:31:27 second dump rest: `T78199` NO **0.25** under ask **0.37**, impulse **−$281**, spot 78335, ~$135, p 33.6%. One wait. `T78299` taker ask 0.46 still blocked. Fill only while the dump is on.

12:34 impulse **+$115** cancelled the second rest (flip). 12:38 third dump: `T78099` ask **0.36** was the human book; `takers[:1]` ate `T78299` NO @ **0.51** (p 61%), peak 0.59 missed the fee-on 10% clip at 0.60, `t_stop` **−0.6498**. Coupon did not fail — the taker stole the slot. Patch: dump coupon before impulse_t; do not hop off a working rest. Skip wait on `AUG2610`.

Paper completed **6 / 1 / −5.6825**. Hour still open. Do not switch.

After the patch, same AUG2608 window: coupon 16h **7 / 4 / +0.15** vs nowait **−3.49**; 24h **+2.72** vs **−3.18**. `AUG2608` minute close still takes the 0.52 taker (the live 0.34 coupon is not on the 1-minute bar). `AUG2518` no longer takes the losing taker. Coupon still beats nowait. `AUG2520` still +41.5%.

## AUG2609 close — coupon rested, taker stole the third dump (2026-08-26 ~13:00 UTC)

Live coupon hour. It dumped. Do not treat this as coupon failure. The switch trigger is a closed live coupon hour that dumped and still could not print a human-style clip. This hour rested the human book twice; the third dump’s 0.36 coupon was stolen by taker priority. That is patched. `AUG2608` already clipped. Do not switch.

| Time | Event |
| --- | --- |
| 12:06:29Z | Rest `T78299` NO **0.25** under ask **0.41**, impulse −$100, spot 78414 (~$115). One wait. Dump ask printed 0.37, not 0.25 — no fill |
| 12:22:52Z | Impulse **+$102** → `wait_invalid`. Flip rule, not fade. Rally YES `T78499` / `T78599` / `T78699` stayed `blocked` |
| 12:31:27Z | Second rest `T78199` NO **0.25** under ask **0.37**, impulse **−$281**, spot 78335 (~$135), p 33.6% |
| 12:34 | Impulse **+$115** cancelled the second rest (flip) |
| 12:38:31Z | Third dump: journal saw `T78099` ask **0.36** (human coupon, p ~0.30). Engine `takers[:1]` took `T78299` NO @ **0.51** p 61% |
| 12:39:03Z | Fill. Peak bid 0.59 missed fee-on 10% clip at 0.60. `t_stop` **−0.6498** (−12.3%) |
| after | No more T (last_loss hour). Coupon-first patch shipped 12:45 — too late for this fill |

981 scans. Impulse floor **−$327**, ceiling +$157. Spot 78219–78648. Journal: 70 wait / 123 blocked / 11 open. No YES rest. No strike hop.

Minute replay this hour is `incomplete data` at the close print. Sweep window now starts at AUG2609:

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 7 / 4 / **+0.15** | 12 / 8 / **+2.72** |
| flex_nowait | 4 / 0 / −3.49 | 8 / 3 / −2.44 |
| flex_wait_loose | 8 / 6 / +7.13 | 12 / 8 / +7.25 |
| flex cheap p30/ask35 | 15 / 3 / −9.09 | 21 / 8 / −6.61 |

16h matches the post-patch AUG2608 window (this hour replayed 0). 24h nowait moved −3.18 → −2.44 because a losing taker rolled out, not because coupon earned here. Coupon still beats nowait. `AUG2608` minute close still takes the 0.52 taker. Cheap still red. Do not switch on the 29¢ knife. Do not chase YES.

Paper completed **6 / 1 / −5.6825**. Session: `last_loss_event=AUG2609`, `skip_next=True`, `skipped_event=AUG2610`. **`AUG2610` is skip-wait** (consecutive losses do not stack). Replay green is not 达成.

## AUG2610 mid-hour — skip-wait held, same-dir taker clipped (2026-08-26 ~13:06 UTC)

Skip-wait held: journal saw `T78199` / `T78099` ask **0.42** coupon books and did not rest. 13:05:07 same-dir `impulse_t` NO `T78299` @ **0.48**, p 52.2%, impulse −$114 — allowed (AUG2518 / AUG2605 / AUG2606). `t_clip` at bid **0.58**, **+13.2%**, pnl **+0.6546**. `raw.peak_bid=0.56` is a mark quirk; the exit print is 0.58. No YES. No coupon rest.

The win cleared session (`skip_next=False`). This is a skip-hour taker clip, not a dump-coupon clip. Do not switch. Leftover working tickets are far-OTM `lock_wait` 0.83, not dump coupon. Paper completed **7 / 2 / −5.0279**.

After the clip the hour is `swing.dead`: 13:06–13:11 journal saw `T78099` ask **0.42** and `T77999` ask **0.41** and did not rest. One T per hour, not a miss.

## AUG2609 tape complete (2026-08-26 ~13:16 UTC)

Band is **78299.99–78399.99**. `T78299` settled YES — holding the paper 0.51 NO to expiry would have been −100%. The `t_stop` −12.3% beat settlement. The human `T78099` coupon would also have settled YES; it never filled.

Minute replay does not match paper. Replay 12:39 took the same `T78299` NO @ **0.51** and this time `t_clip` +10.6% / +0.557 (live peak 0.59 missed fee-on 0.60 and stopped at 0.48). The live 0.36 coupon is not on the 1-minute close, so coupon-first cannot block that replay taker. Loose wait replayed a 25¢ rest and `t_clip` +61% / +1.53 — do not widen the default.

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 8 / 5 / **+0.71** | 13 / 9 / **+3.28** |
| flex_nowait | 5 / 1 / −2.93 | 9 / 4 / −1.88 |
| flex_wait_loose | 9 / 7 / +8.66 | 13 / 9 / +8.77 |
| flex cheap p30/ask35 | 16 / 4 / −7.88 | 22 / 9 / −5.40 |

The window greened because this hour’s replay taker clipped +0.56, not because paper coupon earned. Coupon still beats nowait. Cheap still red. Do not switch on the 29¢ knife. Do not chase YES. Replay green is not 达成. `AUG2610` is still open — sweep again at 14:00.

## AUG2610 close — skip-hour taker clip, not a coupon test (2026-08-26 ~14:00 UTC)

Skip-wait held: 994 scans, 85 coupon journals (`T78199` / `T78099` / `T77999`), **0 `impulse_wait`**. 13:05 same-dir `impulse_t` NO `T78299` @ 0.48 `t_clip` **+13.2% / +0.6546**. After the win the hour was `swing.dead` — later 0.42 / 0.41 books were not rested. Rally YES stayed `blocked`. No YES wait.

Do not treat “no coupon rest this hour” as coupon failure. This was the skip hour. The switch trigger is a closed live coupon hour that dumped and still could not clip. `AUG2608` already clipped. Do not switch.

Minute replay this hour is `incomplete data` at the close print. Sweep window now starts at AUG2610 (this hour replayed 0):

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 8 / 5 / **+0.71** | 12 / 8 / **+2.32** |
| flex_nowait | 5 / 2 / −1.03 | 8 / 3 / −2.84 |
| flex_wait_loose | 8 / 6 / +6.83 | 12 / 9 / +9.72 |
| flex cheap p30/ask35 | 15 / 4 / −6.45 | 21 / 9 / −4.53 |

16h matches the completed-AUG2609 window. 24h fell +3.28 → +2.32 because a winning T rolled out. Coupon still beats nowait. Cheap still red. Do not switch on the 29¢ knife.

Session already clear. **`AUG2611` is the next live coupon rest.**

## AUG2611 open — rally YES taker clipped, not dump coupon (2026-08-26 ~14:01 UTC)

14:00:20 `impulse_t` YES `T78299` @ **0.50**, p 59.7%, impulse **+$137**. No dump, no 32–42¢ coupon book. `t_clip` at bid **0.60**, **+12.7%**, pnl **+0.657**. `raw.peak_bid=0.58` is a mark quirk; the exit print is 0.60. No YES wait. Not a cheap YES chase.

The win marks the hour `swing.dead`: a later dump can journal a coupon and will not rest (one T per hour). This is not a dump-coupon clip. Do not switch. Paper completed **8 / 3 / −4.3709**. Replay green is not 达成.

## Order book

ATM books are two-sided and deep at 1–4 cents. `yes_ask` on the market object is the real take price; 1-cent bids on both sides are inventory, not the touch for a 20% clip.

## AUG2618 open — working coupon ate the other rungs (2026-08-26 17:03 ET)

5pm `AUG2617` daily closed with **0 coupon fills**. 17:00 ET rolled to 6pm hourly `KXBTCD-26AUG2618` ($100 ladder). Paper immediately rested `T78499` YES **0.25** under 0.39, impulse +$9, id **65**. That hang is the tape-follow YES rest.

By 17:02 ET impulse faded to −$3 / −$10. Journal kept printing wait `T78399` NO 0.39 then `T78299` NO 0.33 — in-band, near ATM. Scan `opps=0`. `pick_dump_wait` / `_execute` already allow 3 unique tickers, but `allow_swing` treated a live (not dead) coupon like a one-ticker T: `ticker==memory.ticker and side!=memory.side`. Second and third rungs never reached the book. Human `AUG2520` hung three. Patch: a working `impulse_wait` still allows nearby coupon rungs; clip-after-dead still blocks hop. Diagnose forming now uses the tape side, so a +$100 rally journals YES wait instead of falling through the old NO-only `dump_wait_rest_ready`.

Do not cancel the working YES on a −$10 fade. Fill still needs same-way |impulse| ≥$100 and ask==rest. Do not eat taker. Replay green is not 达成.

## AUG2618 17:10 ET — live YES coupon clipped, then hopped

`T78699` YES rest 0.25 filled 17:10:32 (impulse **+$90**, ask had been 0.30). Same position `t_clip` at bid 0.41, **+57% / +1.4306**. Paper now **11 / 5 / −3.0341**. That is the first live tape-follow YES clip this hour. It is not a taker.

Two bugs on that print:

1. YES fill did not require |impulse| ≥$100 (NO already did). +$90 should not promote. Fixed: both sides need the same-way $100.
2. Clip 之后 hop。17:11:06 又挂回同一张 `T78699`（id 67）。扫描在 clip 之前，`allow_swing` 还当 working。已撤 67（`coupon_hop`）。`run_cycle` 在 exit 之后重新套 swing memory。

`T78499` YES 0.25 仍 working。淡了不撤。这小时已 clip，不再 hop 新档。

17:16 ET sweep（YES 成交也要 |impulse| ≥$100 之后）。Coupon 仍打赢 nowait。回放变绿不是达成。`AUG2608` dump clip 仍算对。不吃 taker。

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 6 / 6 / **+8.32** | 10 / 9 / **+7.83** |
| flex_nowait | 5 / 3 / −0.22 | 6 / 3 / −1.26 |
| flex_wait_loose | 7 / 6 / +7.17 | 11 / 10 / +14.38 |
| flex cheap p30/ask35 | 9 / 6 / +5.12 | 13 / 9 / +4.63 |

## AUG2618 close / AUG2619 open (2026-08-26 18:01 ET)

Live `AUG2618`: leftover `T78499` YES rest cancelled `wait_invalid` at the close (never filled). The hour's only fill remains `T78699` YES `t_clip` +1.4306. Paper still **11 / 5 / −3.0341**. Session clear — next hour may rest.

18:00 ET rolled to 7pm hourly `AUG2619`. Quiet/down hung `T78599` NO 0.25 under 0.33, then `T78699` NO 0.25 under 0.38. Tape faded to +$41 (not a $100 flip, NOs stay) and added `T78899` YES 0.25 under 0.36. Three working, mixed sides. Fill still needs same-way |impulse| ≥$100. Do not eat taker.

18:01 ET sweep (window starts at `AUG2618`). Replay does not print the live YES clip. Coupon still beats nowait. Replay green is not 达成.

| Run | 16h | 24h |
| --- | ---: | ---: |
| dump coupon (default) | 5 / 5 / **+7.57** | 9 / 8 / **+6.99** |
| flex_nowait | 5 / 3 / −0.22 | 6 / 4 / +0.64 |
| flex_wait_loose | 7 / 6 / +7.17 | 10 / 9 / +12.56 |
| flex cheap p30/ask35 | 7 / 5 / +5.74 | 12 / 8 / +3.79 |

---

# 合成盘对照（2026-09-18）

没有真 tape 的一次纯机制检验。命令、世界设定、怎么读结果见 [`catalog/rules/research.md`](../rules/research.md)。

```bash
python3 -m btchour research fill-model --hours 40 --seeds 5   # 每行 200 小时
python3 -m btchour research calibration --hours 200 --seeds 3
```

## 一、回放的挂单成交模型在自己给自己发钱

`fair` 世界里，盘口报的就是真实公允值加一个价差。**这里没有任何人能赚钱**，这是对照组。

| 世界 / 成交模型 | 笔数 | 每小时 | 胜率 | 总盈亏 | 每笔 | 95% 区间 | t |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `fair` / wick | 119 | 0.59 | **90%** | **+80.37** | +0.675 | [+0.481, +0.863] | **7.09** |
| `fair` / close | 7 | 0.04 | 43% | −1.22 | −0.174 | [−1.459, +1.283] | −0.23 |
| `underreact` / wick | 119 | 0.59 | **99%** | +115.58 | +0.971 | [+0.865, +1.074] | **18.24** |
| `underreact` / close | 4 | 0.02 | 100% | +5.79 | +1.448 | [+0.916, +1.808] | 5.22 |
| `overreact` / wick | 88 | 0.44 | 82% | +61.18 | +0.695 | [+0.387, +1.001] | 4.44 |
| `overreact` / close | 4 | 0.02 | 75% | +8.71 | +2.178 | [−0.798, +4.327] | 1.37 |

同一批 tape、同一套门，**只差成交规则一条**。

原因是机械的：`wait_book_crossed` 允许用**那一分钟买一的最高点**（`yes_bid_high`）判成交，而持仓随后按**同一分钟的收盘**标记。一分钟之内近 ATM 档的概率能晃 8–10¢，所以这条规则等于「在分钟内最有利的那一瞬成交，再按收盘算账」。在一个报公允价的盘口上，它照样打出 90% 胜率、t=7.09。

这条也解释了本文件上半部分那些 `6 / 6 / +8.32`、`5 / 5 / +7.57`：**6 笔 6 胜不是好结果，是成交模型出问题的信号。**

改动：`Settings.replay_wick_fill` 默认 **False**，回放成交要求收盘报价落在 rest 上。要复现旧数字用 `BTCHOUR_REPLAY_WICK_FILL=1`。纸盘 loop **不受影响**：它每几秒轮询一次，而且额外要求 rest 价位上有真实成交（`tape_at_rest`）。

### 复现：换掉结算模型，结论不动

同一组对照在**旧结算模型**（τ 压在整 60 秒）下先跑过一次，每行 300 小时，独立种子：

| 世界 / 成交模型 | 笔数 | 总盈亏 | 胜率 | 成交时 edge |
| --- | ---: | ---: | ---: | ---: |
| `fair` / wick | 166 | +119.35 | 90% | +0.097 |
| `fair` / close | 14 | −6.75 | 43% | −0.008 |
| `underreact` / wick | 168 | +162.71 | 97% | +0.194 |
| `underreact` / close | 7 | +0.67 | 71% | +0.139 |
| `overreact` / wick | 125 | +72.30 | 82% | +0.044 |
| `overreact` / close | 2 | −4.53 | 0% | −0.051 |

数字不同，形状一样：wick 在公允盘里照样 90% 胜率，close 照样每小时 0.02–0.05 笔。**这条结论不依赖第四节那个结算模型修正。**

## 二、诚实成交之后，`impulse_wait` 不是「亏」，是「几乎不成交」

`close` 三行：**每小时 0.02–0.04 笔**，200 小时 4–7 笔。这不是一条能靠大数定律稳定下来的策略，无论方向对不对。

成交那一刻模型对我们这一侧的读数（`maker_edge_at_fill`，付出 0.25）：

| 世界 | 成交笔 | 成交时模型 p | 成交时 edge |
| --- | ---: | ---: | ---: |
| `fair` / close | 7 | 0.235 | **−0.015** |
| `underreact` / close | 3 | 0.409 | +0.159 |
| `overreact` / close | 1 | 0.196 | **−0.054** |

这就是 decisions **015** 那道题的定量答案：在盘口下方 8¢ 挂 NO，只有当盘口自己走回来才成交，而盘口走回来的意思就是这轮砸盘在退。公允盘里成交价比公允贵 1.5¢，反应过度的盘里贵 5.4¢。**不是阈值松紧，是构造性错位** —— 015 的原话，现在有了数。

在为它量身定做的 `underreact` 世界里，方向是对的（edge +0.159），但一小时只成交 0.02 笔。**机制不是方向错，是被自己饿死。**

## 三、20% EV 门 = 要求盘口比自己的模型便宜 16.7%

`EV = p·b − (1−p)` 代入 `b = (1−c)/c` 塌成 `EV = p/c − 1`，所以 `EV ≥ T ⟺ c ≤ p/(1+T)`。

`T = 20%` 时：p=0.30 要 25¢（便宜 5¢），p=0.42 要 35¢（便宜 7¢），p=1.00 要 0.833（便宜 16.7¢，也就是 `lock` 那个 $0.82/$0.83 天花板的来历）。

一条 1¢ 价差的盘口不会随手给 5–7¢ 的错价。**所以这道门筛的不是好单，是「模型和市场分歧最大的时刻」，而那通常是模型错。** 回放 0 笔和实盘 17 张噪声（014）是同一件事的两面。推导和表见 [`catalog/rules/ev.md`](../rules/ev.md)。

## 四、结算模型：60 秒均价不是 60 秒方差

`KXBTCD` 结算是最后 60 个 BRTI 打印的简单平均（[`settlement.md`](../rules/settlement.md)）。取平均会扔掉方差：末段窗口只贡献 `m/3` 秒，窗口已经走了一半时几乎不贡献。

旧的 `digital_prob` 反过来把 τ **下限压在整 60 秒**，于是把全场最确定的那一分钟当成最不确定的一分钟。修正后：

```
τ ≥ 60s  →  τ − 40s
τ < 60s  →  τ³ / (3 × 60²)
```

差距（现货在行权价上方 $50，年化波动 0.55）：剩 60 秒时 **p = 0.928**，旧模型 0.801；剩 30 秒时 1.000 对 0.835。

校准（200×3 小时，全部近 ATM 档对真实结算，不牵涉盘口）：Brier 全程 0.1247 对 0.1249，**最后 5 分钟 0.0371 对 0.0380**。整场差别小，但错都错在收盘前那几分钟 —— 也就是 `flatten_seconds`、`invalidate_p`、以及 `lock` 判「已经被价格决定」的那几分钟。可靠性曲线在修正后是平的（0.0–0.1 桶实测 2.6%，0.9–1.0 桶 95.7%）。

## 五、这些结论管不到什么

合成盘**证明不了**任何策略在真盘赚钱。它只能证伪。上面每一条都是「机制」结论，真盘结论仍然只能来自真 tape，而真 tape 需要能连上 Kalshi 的机器（本次会话的出网策略挡掉了 `external-api.kalshi.com`）。

归档通道已经铺好：`sweep` 拉完之后 `python3 -m btchour research archive` 把 `data/replay-cache/` 固化进 `data/archive/`，之后 `research oos` 按时间切前后两半，老的一半调参、新的一半报数。

## 真 tape 决定性检验：模型输给市场中价（2026-09-18）

出网白名单放行 `external-api.kalshi.com` 之后的第一次真盘检验。归档 **92 小时**真实
tape（`KXBTCD-26SEP1414` … `26SEP1809`，**48568** 个近 ATM 读数），跑 016 自己写下的
决定性检验。

### 1. 决定性检验：中价更准，而且显著

`python3 -m btchour research baseline --hours 96`

```
模型 vs 市场中价（92 小时 / 48568 个近 ATM 读数）

全样本 Brier：模型 0.11519   中价 0.10319

分歧               读数     模型 Brier     中价 Brier      谁更准
0-2¢           6481      0.12858      0.12855       中价
2-5¢           8835      0.13283      0.13097       中价
5-10¢         14222      0.10622      0.09833       中价
>10¢          19030      0.10914      0.08529       中价
```

四个桶全输。按小时聚类自举（同一档位在一小时里被读 40 分钟是一次下注，不是 40 次）：

| 范围 | 读数 | 模型 | 中价 | 差 | 95% 区间 |
| --- | ---: | ---: | ---: | ---: | --- |
| 全样本 | 48568 | 0.11519 | 0.10319 | **+0.01199** | [+0.00844, +0.01551] |
| 门开火处（分歧 ≥5¢） | 33252 | 0.10789 | 0.09087 | **+0.01702** | [+0.01216, +0.02150] |

区间不跨 0，且在门开火的地方差距**更大**。24 小时子样本给的是同一结论、同一量级
（+0.01581 / [+0.00869, +0.02270]），不是某个时段的巧合。

016 的判据原文是「模型对真实结算的 Brier 跑赢市场中价」。**它没跑赢。016 里的 D
（把 20% 门改成小正 EV × 笔数）到此为止。**

### 2. 为什么输：0.55 的波动率地板

`BTCHOUR_ANNUAL_VOL=0.55` 是个写死的猜测。真实分钟级波动率远低于它，地板把 p 往 0.5
拽，于是模型系统性地不敢下判断：

| 中价桶 | 读数 | 均模型 p | 均中价 | 真实频率 |
| --- | ---: | ---: | ---: | ---: |
| 0.0–0.1 | 11810 | 0.134 | 0.041 | **0.033** |
| 0.1–0.2 | 4475 | 0.248 | 0.146 | **0.137** |
| 0.2–0.3 | 3070 | 0.330 | 0.248 | **0.233** |
| 0.7–0.8 | 3069 | 0.671 | 0.753 | **0.794** |
| 0.8–0.9 | 4462 | 0.751 | 0.854 | **0.873** |
| 0.9–1.0 | 11703 | 0.865 | 0.958 | **0.979** |

中价那一列贴着真实频率，模型那一列一路被拽向 0.5。所谓「模型看见了市场没看见的东西」，
实际上是波动率填错了。

波动率扫描（92 小时全样本 Brier）：

| 设定 | 模型 | 中价 | 差 |
| --- | ---: | ---: | ---: |
| floor 0.55（现行） | 0.11519 | 0.10319 | +0.01199 |
| floor 0.40 | 0.10701 | 0.10319 | +0.00382 |
| floor 0.30 | 0.10429 | 0.10319 | +0.00110 |
| **realized，无地板 / floor 0.20** | **0.10396** | 0.10319 | **+0.00077** |
| realized ×0.8 | 0.10490 | 0.10319 | +0.00170 |
| realized ×1.3 | 0.10590 | 0.10319 | +0.00270 |
| 固定 0.35 | 0.10539 | 0.10319 | +0.00219 |
| 固定 0.45 | 0.10907 | 0.10319 | +0.00588 |

**没有任何一档让模型赢。** 最好的一档只是逼近打平。这是本次检验最重要的一行：修波动率
能把「稳定地错」变成「和市场差不多」，**它变不出优势**。

门的开火率同时说明现行设置有多离谱（24 小时窗口口径）：

| 波动率设定 | 分歧 ≥5¢ 的读数占比 | 该区间 模型−中价 | 95% 区间 |
| --- | ---: | ---: | --- |
| floor 0.55（现行） | **75.3%** | +0.02042 | [+0.01137, +0.02815] |
| realized，无地板 | 20.0% | +0.00286 | [−0.00423, +0.01091] |

现行设置下，四分之三的近 ATM 读数都「和市场有 ≥5¢ 分歧」。那不是发现了错价，那是噪声。
换成 realized 之后开火率掉到 20%，差值区间开始跨 0 —— 打平。

### 3. 样本外：`swing` 两段都亏，`flex` 92 小时动手 1 次

`python3 -m btchour research oos --hours 96 --train 0.5`

| 变体 | 调参段（46h） | 样本外（46h） |
| --- | --- | --- |
| `swing` | 17 笔 / 29% / **−10.49** / t=−1.92 / 回撤 15.01 | 20 笔 / 45% / **−9.65** / t=−1.27 |
| `flex`（默认） | 0 笔 | 1 笔 / +0.84 |
| `lock` | 0 笔 | 2 笔 / +9.77（t=1.68，两笔不算数） |

24 小时窗口里 `swing` 调参段还能打出 83% 胜率 / t=3.18、样本外翻成 20% / −4.30；拉到
92 小时，**两段都是亏的，合计 −20.14 / 37 笔**。那个 t=3.18 是窗口挑出来的，不是策略。

默认的 `flex` 在 92 小时里动手 **1 次**。这就是 016 换上诚实成交模型之后的真实面貌：
历史上那些 `6/6/+8.32` 的绿数字，靠的是旧回放成交模型，不是市场。

### 4. 唯一看着像错价的地方，样本量不够判

尾部市场略偏保守（中价 0.958 的桶真实 0.979）。按真实 ask、真实 taker 费、每个
(小时, 档位) 只取一条、按小时聚类自举来算：

| 距结算 20 分钟 | 笔数 | 实际 YES 率 | 每笔盈亏 | 95% 区间 |
| --- | ---: | ---: | ---: | --- |
| YES ask 0.90–0.95 | 57 | 0.965 | +0.0399 | [−0.0153, +0.0762] |
| YES ask 0.97–0.985 | 79 | 1.000 | +0.0224 | [+0.0214, +0.0234] |
| NO（yes_bid 0.03–0.05） | 53 | 0.943 | −0.0243 | [−0.0931, +0.0321] |

24 小时版本里这几行几乎全是 100% 胜率、区间不跨 0；样本量翻到 92 小时之后，**大部分
区间开始跨 0**，剩下不跨 0 的全是「一笔没亏过」的退化区间 —— 那不是置信，那是还没轮到
它亏。

**这正是本仓库反复警告过的那种数字**（`research.md`：「6 笔 6 胜不是好结果，是要去查
成交模型的信号」）。在 ask=0.99 买 YES，费后要 p ≥ 0.9907 才不亏：

| 观察 | 真实胜率 95% 下界（rule of three） | 够不够 |
| --- | ---: | --- |
| 30 笔全胜 | 0.9000 | 不够 |
| 100 笔全胜 | 0.9700 | 不够 |
| 300 笔全胜 | 0.9900 | 不够 |
| 1000 笔全胜 | 0.9970 | 够 |

按当前密度要约 **800 小时** tape 才判得动。而且它的风险形状是捡钢镚：一次 0.99 的亏损
要 106 次盈利补回来，和用户要的「稳定」正好相反。**不做，也不作为下一步方向。**

### 5. 归档现在进 git

`data/` 整个被 gitignore，容器一销毁 tape 就没了 —— 这是这个仓库跑了这么久从来没攒够
过样本的直接原因。`.gitignore` 改成 `data/*` + `!data/archive/`。92 小时约 2.7MB，
300 小时约 9MB，可以直接进版本库。

Kalshi 的分钟 K 线大约只回溯到 4 天，所以**一次性补不出 300 小时**，只能往前持续攒。

### 6. 顺带修的：Kalshi 客户端没有退避

`_request` 撞 429 直接抛，`sync` 第一次就崩在 `/series/KXBTCD`。已加：GET 的 429/5xx
指数退避重试（认 `Retry-After`）+ 请求间隔下限 60ms。签名 POST **不重试** —— 重发订单
会开重仓。`tests/test_kalshi_retry.py` 6 条覆盖，全量 285 条绿。

### 7. 016 给 C 定的判据本身是坏的

上一个线程确认 `maker_edge_at_fill = fill_model_p − paid`，而 `fill_model_p` 就是引擎的
`digital_prob` —— **正是第 1 节刚判定跑不赢中价的那个模型**。它自己也标了这个局限：合成盘
里盘口按构造报公允值，所以那里可信；真 tape 上它会把模型自身的误差一起吃进去。

问题是 016 把它写成了 C 方向的前置判据（「样本外 `maker_edge_at_fill` 为正、区间不跨 0」）。
而模型的偏差是**单向**的：0.55 地板把 p 拽向 0.5，所以在一个 0.25 挂单会成交的**便宜那侧**，
模型读数系统性偏**高**。

92 小时真 tape，按中价分档，三方对照：

| 中价区间 | 读数 | 模型 p | 中价 | 真实频率 | 016 的口径 | 诚实口径 | 差 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.15–0.20 | 1949 | 0.273 | 0.174 | 0.168 | **+0.023 通过** | **−0.082 亏** | +0.105 |
| 0.20–0.25 | 1610 | 0.313 | 0.224 | 0.195 | **+0.063 通过** | **−0.055 亏** | +0.118 |
| 0.25–0.30 | 1460 | 0.350 | 0.275 | 0.275 | +0.100 通过 | +0.025 | +0.075 |
| 0.30–0.35 | 1374 | 0.384 | 0.325 | 0.324 | +0.134 通过 | +0.074 | +0.060 |
| **合计 0.20–0.35** | **4444** | **0.347** | 0.272 | **0.261** | **+0.097** | **+0.011** | **+0.086** |

（016 的口径 = 模型 p − 0.25；诚实口径 = 真实结算频率 − 0.25。）

偏差一律是 +0.06 ~ +0.12 的**乐观**方向。最上面两行是要害：016 的门会在**实际每张亏
5.5~8.2 分**的地方亮绿灯。合计那行同样说明问题 —— 门读到 +0.097 像是个厚边，诚实数字
是 +0.011，基本等于零，而且这还没算「哪些挂单真的会成交」带来的逆向选择。

**所以攒够 300 小时再按 016 原文验 C，会再生产一次假绿**，和旧 wick 成交模型当年造出
90% 胜率是同一个毛病，只是换了个地方。

修法：加 `maker_edge_realized`（`btchour/research/evaluate.py`），**不碰模型**——
成交那一侧最后结算值 1/0，减去挂单含费成本。`btchour/replay.py` 的 `_stamp_settlement`
在两个成交出口都记下 `settles` / `settle_value`。两个指标并排出，合成盘上看
`maker_edge_at_fill`，真 tape 上看 `maker_edge_realized`。

---

# 三条路，92 小时真 tape 上全部量化（2026-09-18）

017 判掉了「模型比市场懂」这条。剩下的两条也测完了，数据是同一批 92 小时（`data/archive/`，`26SEP1410`–`26SEP1809`）。

## 一、阶梯静态套利：没有

同一到期日的 `KXBTCD` 里，YES(K) 必须随 K 单调不增。所以 K1 < K2 时买 YES(K1) + 买 NO(K2) 在任何世界都至少赔付 1，落在两档之间赔付 2：

```
S < K1        0 + 1 = 1
K1 ≤ S < K2   1 + 1 = 2
S ≥ K2        1 + 0 = 1
```

成本是 `yes_ask(K1) + 1 − yes_bid(K2)`，所以只要 `yes_bid(K2) > yes_ask(K1) + 费`，就是无风险利润。**不需要任何模型，也不需要对比特币有看法。**

92 小时、**47293 对**近 ATM 组合：

| 口径 | 交叉数 |
| --- | ---: |
| 收盘价（乐观） | **0** |
| 分钟内最差价 | **0** |
| 费前 | **1**（0.002%，值 1¢，两腿 taker 费约 3¢ 吃光） |

离套利最近的一次是 **−0.21¢**，99 分位 −1.21¢，中位数 −10.5¢。扫描器对注入的假交叉能检出（`tests/test_ladder.py`），所以这个 0 是真的 0。**阶梯是自洽的，这条路干净地否掉。**

## 二、贴价挂单：中段亏，尾部有一丝

不设门、不设出场：挂在该档自己的买一 / 卖一，下一分钟真有成交打穿才算成交，maker 费 0。**忽略排队和成交量，所以这是上界**——真实执行只会更差。78192 次成交：

| 口径 | 方向 | 成交 | 每张 | 95% 区间 | t |
| --- | --- | ---: | ---: | --- | ---: |
| 持到结算 | 挂买一买入 | 38883 | +0.0103 | [+0.0070, +0.0136] | 6.15 |
| 持到结算 | 挂卖一卖出 | 39309 | −0.0099 | [−0.0131, −0.0066] | −5.89 |
| 持到结算 | **合计** | 78192 | **+0.0002** | [−0.0021, +0.0025] | 0.15 |
| 1 分钟 markout | 合计 | 72540 | **−0.0011** | [−0.0017, −0.0006] | −3.94 |

买卖两边持到结算的 ±1¢ 是这段样本里 BTC 在涨，不是优势——**合计 t=0.15，区间跨 0**。

真正的数是 markout：挂单被打中之后一分钟，中价平均往我们不利的方向走了 **0.11¢**，比吃到的半个价差还多。两边对称（−0.0012 / −0.0011），这是逆向选择的典型形状。

### 按价格分档，样本外一致

| 样本 | 档位 | 成交 | markout | t |
| --- | --- | ---: | ---: | ---: |
| 前半 | 尾部 <0.15 | 9179 | +0.0011 | 2.50 |
| 前半 | 尾部 >0.85 | 9745 | +0.0008 | 1.77 |
| 前半 | **中段 0.15–0.85** | 17893 | **−0.0035** | −4.58 |
| 后半（样本外） | 尾部 <0.15 | 9250 | +0.0008 | 1.63 |
| 后半（样本外） | 尾部 >0.85 | 9624 | +0.0017 | 4.44 |
| 后半（样本外） | **中段 0.15–0.85** | 16849 | **−0.0037** | −4.64 |

按剩余时间切没有任何窗口能翻正（3–10 分 −0.0020，35–60 分 −0.0011）。

**要害：005 / 014 规定的 32–42¢ coupon 带，正好落在亏得最狠的中段。** 前后两半各自 t≈−4.6，这不是样本波动。

尾部两档前后两半都为正，是这 92 小时里唯一一个正的、且样本外没掉的东西。但它 **+0.1¢ 一张**，而且是在忽略排队的上界口径下——尾部恰恰是排队最深、流量最薄的地方。**这是一个候选，不是一个结果。**

## 三、这三条路合起来说明什么

| 机制 | 判据 | 结果 |
| --- | --- | --- |
| 模型比市场懂（taker / 20% 门 / lock / impulse_t） | Brier vs 中价 | **否**（0.115 vs 0.103，四桶全输，8 档波动率没有一档能赢） |
| 阶梯静态套利 | 交叉数 | **否**（47293 对里费前 1 次，费后 0 次） |
| 贴价做市 | 1 分钟 markout | **中段否**（−0.36¢，样本外一致）；尾部 +0.1¢ 待验 |

92 小时约 4 天，一个 regime。尾部那条要动真钱之前至少还要：更多 tape、一个带排队位置的成交模型、以及把 markout 换成真实出场后的已实现盈亏。

## 四、高尾挂单测完了：优势来自成交假设，不来自市场

018 结尾把「尾部贴价挂单」留成唯一候选，并给它定了三道门：更多 tape、一个带排队位置的成交模型、用真实结算盈亏替代 markout。第一道门在这个容器里过不去（Kalshi 分钟 K 线只回溯约 4 天），后两道门做完了，候选没有活下来。

### 先修一个我自己的错：分桶分错了

018 那张表按**报价**分档。挂在 0.10 的卖一是卖 YES，也就是**用 0.90 买 NO**——经济上是高尾，却被归进了「便宜 <0.15」那格，和一张真正 10 分钱的彩票放在一起。两尾于是被搅匀，读出来「两尾都是 +0.1¢」。

换成按**持仓成本**分档（`MakerFill.cost`：买 YES 就是报价，卖 YES 就是 1 − 报价），两尾立刻分开，而且方向相反：

| 成本档 | 成交 | 持到结算/张 | 95% 区间（小时聚类） | 亏损率 |
| --- | ---: | ---: | ---: | ---: |
| 便宜彩票 <0.15 | 21166 | **−0.0123** | [−0.0251, +0.0025] | 95.7% |
| 中段 0.15–0.85 | 34825 | −0.0015 | [−0.0043, +0.0013] | 51.0% |
| 高尾 0.85–0.95 | 10059 | +0.0173 | [−0.0086, +0.0408] | 8.1% |
| 极高尾 >0.95 | 12142 | +0.0127 | [+0.0040, +0.0196] | 1.5% |

这就是赔率偏差的教科书形状：便宜的彩票系统性偏贵，近确定的偏便宜。**018 说「两尾都为正」是错的，便宜那尾是负的。**

区间也换了口径。一小时里所有档位骑的是同一条 BTC 路径，78192 次成交远不是 78192 个独立样本，所以按**小时**重抽（`cluster_ci`），不再按成交笔重抽。markout 的结论扛得住（中段 −0.0035 [−0.0044, −0.0026]），结算口径的区间则宽了一大截。

### 第二道门：把「到价即成交」换成「打穿才成交」

`touch` 口径是盘口摸到我们的价就算成交，等于假设我们永远排在队首。`through` 口径要求下一分钟**穿过**我们的价，这是「我们前面那一队真的被吃光了」的最便宜证据。这不是精确的排队模型，但方向是对的，而且它先验证了一件事：

成交数 78192 → 46257（少 41%），而**高尾成交数 22201 → 8893（少 60%）**。排队假设最松的地方，正是高尾——薄、深队列、流量少，和 018 担心的完全一致。

| 成本档 | through 成交 | 持到结算/张 | 95% 区间（小时聚类） |
| --- | ---: | ---: | ---: |
| 便宜彩票 <0.15 | 9454 | −0.0180 | [−0.0350, +0.0008] |
| 中段 0.15–0.85 | 27910 | **−0.0125** | [−0.0168, −0.0081] |
| 高尾 0.85–0.95 | 5644 | +0.0041 | [−0.0251, +0.0306] |
| 极高尾 >0.95 | 3249 | +0.0034 | [−0.0101, +0.0160] |
| **高尾合计 >0.85** | 8893 | **+0.0038** | **[−0.0185, +0.0243]**（t=1.41） |

高尾每张从 +1.48¢ 掉到 **+0.38¢**，区间跨 0。换句话说：**高尾那点优势，四分之三是「我们排在队首」这个假设发的钱，不是市场发的。** 中段在严格口径下反而更难看（−1.25¢，区间不跨 0）。

### 高尾内部也不一致

| 切法 | 成交 | 每张 | 95% 区间 |
| --- | ---: | ---: | ---: |
| 前半 46 小时 | 4515 | −0.0107 | [−0.0474, +0.0227] |
| 后半 46 小时（样本外） | 4378 | +0.0188 | [−0.0030, +0.0402] |
| 挂买一买 YES（>0.85） | 4533 | +0.0153 | [−0.0217, +0.0473] |
| 挂卖一卖 YES（<0.15，即长 NO） | 4360 | −0.0081 | [−0.0404, +0.0202] |
| 剩 3–15 分钟 | 1802 | +0.0012 | [−0.0245, +0.0243] |
| 剩 >30 分钟 | 4764 | +0.0010 | [−0.0308, +0.0285] |

前半是负的，后半是正的；两个方向一正一负；按剩余时间切没有任何一段站得住。一个真实的结构性优势不会只在样本的后一半、只在其中一个方向上出现。

### 第三道门：收益形状本身就不是「稳定」

92 小时把高尾全挂满（`through` 口径）：

- 总盈亏 **+45.2 张**，占用本金 7801 张 → 每单位本金 **+0.58%**（92 小时累计，不是每小时）
- **最差的那一个小时亏 42.8 张 —— 等于 92 小时总收成的 0.9 倍**
- 去掉最差的 3 个小时，总盈亏变成 +146.9；这 3 小时决定了整个结果
- 亏损小时 30/92，小时中位数 +4.35，小时均值区间 **[−1.52, +2.25]**

形状是「捡钢镚 + 偶尔被车撞」：92 次里 62 次小赚，1 次把全部赚回去的还多。小时盈亏 sd=9.31、均值 +0.49，要把 t 打到 2 需要约 **1439 小时（60 天连续 tape）**，打到 3 需要 3237 小时（135 天）。

### 结论

高尾挂单**不是一个可以动真钱的策略**，并且不值得再攒样本去救：它在唯一诚实的成交口径下与 0 无法区分，样本内前后两半符号相反，方向之间符号相反，而且要判定它需要的 tape 量（60–135 天连续分钟 K 线）在这个交易所拿不到——Kalshi 只回溯约 4 天。

taker、静态套利、做市中段、做市尾部，四条路全部测完，全部是负的或与 0 无法区分。这张桌子上没有给我们的 alpha。

---

# 换桌子：全交易所扫描（2026-09-18）

017–019 在 `KXBTCD` 上测死四条路之后，这一节换的是桌子，不是参数。方法也换了
一件事，先说这件事，因为后面所有数字都靠它。

## 0. 不再发明成交

在此之前，本仓库每一个工具都要**发明**成交：回放的 wick 模型从 K 线极值里发明，
`research/maker.py` 从「盘口碰到我们的价」里发明。两种发明都发过本不存在的钱
（合成公允盘里 90% 胜率 / t=7.09；`touch` 换成 `through` 后优势掉四分之三）。

Kalshi 的 `/markets/trades` 公开每一笔成交的**主动方**。主动方买 YES，那挂单方
就是在同一价上卖了 YES，也就是用 `1 − yes_price` 买了 NO。挂单方的价格、方向、
张数因此全部确定，结算结果也是公开的，于是**挂单方的盈亏是纯算术** —— 没有成交
规则，没有排队假设，没有任何模型。每一笔都是真的发生过的成交。

它量的是**池子**：全体挂单方每张赚了多少。新进入者拿不到全部（队列里已经有人，
其中一些还知情），所以池子是上界。但方向是对的：**池子为负的市场，谁做市都是
亏的**。代码 `btchour/research/venue.py`。

## 1. 结构筛选（不用 tape，一次快照）

交易所共 **14154 个系列**。三小时全所成交 **1234978 笔**，涉及 **1505 个系列**。
按名义额（`张数 × 挂单方持仓成本`）排名，前几名：

| 系列 | 3 小时名义 | 张数 | 笔数 | 价差 | ATM taker 费 | 价差/费 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `KXBTC15M` BTC 15 分钟 | $4388524 | 26585494 | 350045 | 1.00¢ | 0.82¢ | 1.22 |
| `KXATPCHALLENGERMATCH` | $3188416 | 11196846 | 69760 | 1.04¢ | 1.75¢ | 0.60 |
| `KXMVECROSSCATEGORY` 串关 | $2927596 | 100025105 | 104544 | — | — | — |
| `KXBTCD` BTC 小时（旧桌） | $2032786 | 8241407 | 82847 | 1.02¢ | 1.30¢ | 0.78 |
| `KXWTACHALLENGERMATCH` | $1559077 | 5889385 | 44275 | 2.98¢ | 1.75¢ | **1.70** |
| `KXITFMATCH` | $609835 | 3570959 | 30834 | 2.21¢ | 1.75¢ | 1.26 |
| `KXGOLD15M` | $512416 | 2687453 | 42590 | 0.30¢ | 0.21¢ | 1.40 |
| `KXMLBGAME` 棒球 | $214723 | 501081 | 4063 | 1.01¢ | 0.87¢ | 1.16 |

**第一条结论在这里就出来了：价差比交易成本薄不是 `KXBTCD` 的毛病，是整个
Kalshi 有流动性那一半的常态。** 60 个最活跃系列里，价差/费比值几乎全落在
0.6–1.8。比值高的（`KXTHAIL1TOTAL` 21.0、`KXIDNSLTOTAL` 19.9、`KXCS2MAP` 8.5）
名义额只有头部的百分之一到千分之一 —— 和 `KXBTC` 区间盘同一个教训：**宽价差
和没人交易是一件事的两面**。

费率结构也扫了：**14 个系列 `fee_multiplier = 0`（taker 费为零）**，另有 19 个
棒球衍生系列是 0.5 倍，162 个系列反过来收挂单费。零费那 14 个全是
`KXTRUMPOUT`、`KXBTCY`（BTC 年末价）这类一次性 / 年度市场 —— 一辈子只结算一次，
**过不了第 3 道（独立样本）**。费率优势和样本量在这个交易所是负相关的。

## 2. 静态套利：全交易所两种形状都为零

**互斥篮子。** `mutually_exclusive` 的事件里至多一个 YES 能结算，所以按买一全部
卖出收到 `Σbid`，最多赔 1，`Σbid > 1` 就是锁死的利润。**7937 次观测，0 次违反。**
最接近的一次是 **−0.14¢**（`KXNCAAFGAME-26SEP19KENNTENN`），中位数 **−12.1¢** ——
典型互斥事件的抽水有 12 分宽。

反方向（`Σask < 1` 全买）**不是套利**，写进代码时特意关掉了默认输出：
`mutually_exclusive` 只承诺「至多一个」，不承诺「恰好一个」，候选名单可以漏掉
真正的赢家，一条腿被暂停也会让快照里的篮子凭空变便宜。第一版没分清这一点，
于是把 `KXLAPRIMARY` 两条腿 `Σask = 0.138` 报成了 85 分的套利。

**嵌套阶梯。** 一次全交易所快照覆盖 **12472 个在挂市场**，只找到 **6 次交叉**，
全部在同一场比赛里，**挂单量 0.01 张** —— 名义额两美分。多轮快照结果一致
（6 / 6 / 8 次）。

写这个扫描时还踩了一个必须记下来的坑：第一版按**事件**配对阶梯，于是
`KXNCAAF1HSPREAD-26SEP18PRSTORE` 这种一个事件里装了两条反向阶梯（「Oregon 赢
超过 K 分」和「Portland St. 赢超过 K 分」）的市场，被配成了
「0.01 买 / 0.99 卖」。**19479 对里报出 18082 次交叉，全是造出来的。** 正确的
分组键是「把阈值数字抠掉之后的问题本身」（`noarb.rung_subject`）。
*一个事件不等于一条阶梯。*

## 3. 挂单池：真成交、按结算算钱

网球挑战赛是第 1、2 两道唯一同时过的品类：价差 1.0–3.0¢ 且名义额排在全所前五。
每场比赛是一个独立结算标的，一天几十场，回溯 30 天都拿得到 —— 第 3 道
（`KXBTCD` 死在这一道：判高尾要 1439 小时分钟 K 线，交易所只回溯 4 天）在这里
不是问题。

| 系列 | 场次 | 笔数 | 张数 | 名义 | 挂单池 ¢/张 | 95% 区间（按场重抽） | t |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `KXATPCHALLENGERMATCH` | 188 | 1055295 | 143237466 | $73394582 | **−0.010** | [−2.50, +2.47] | −0.01 |
| `KXWTACHALLENGERMATCH` | 143 | 903397 | 132391940 | $65391528 | **+1.837** | [+0.33, +3.46] | +2.28 |
| `KXITFMATCH` | 200 | 354499 | 34354829 | $16872576 | **+2.755** | [−2.32, +8.18] | +1.03 |

WTA 那一行是整轮扫描里唯一一个区间不跨 0 的结果。它扛不住拆：

- **前半 +2.42 / 后半 +1.08（t 从 2.12 掉到 0.95）。**
- **最赚的 5 场比赛占了全部利润的 60%**（总 +$2431717，最好一场 +$454485，
  最差一场 −$263569，143 场里 87 场为正）。
- 挂单方拿 NO 那侧 +3.50，拿 YES 那侧 −2.07。

这正是 019 判高尾时用的「形状」检验：**收成集中在极少数结算上，就不是稳定优势，
是方向性运气。** 按结算算钱把整场比赛的方向赌博算进了「做市」，而做市商并不
持有到结算。

## 4. Markout：做市真正在做的那件事

做市商挂、成交、然后平掉。所以要问的是「成交之后不久，市场在哪里成交」，而不是
「这张票最后赔付多少」。参照价用**之后 60 秒内真实成交的成交量加权均价** —— 仍然
只用真成交，不用任何我们想象出来的报价。买卖价跳动让这个均价落在盘口中间附近，
于是挂在卖一上成交的人开局领先半条价差，再把流向知道的东西吐回去。

| 系列 | 张数 | markout 60s ¢/张 | 95% 区间 | t |
| --- | ---: | ---: | --- | ---: |
| `KXATPCHALLENGERMATCH` | 141281226 | **+0.150** | [+0.084, +0.206] | +4.84 |
| `KXWTACHALLENGERMATCH` | 130220796 | **+0.120** | [+0.024, +0.251] | +2.05 |

方差比按结算算小一个量级，符号也稳。**但量级说明了一切：WTA 的价差 2.98¢，
半条价差 1.49¢，挂单方最后只留下 0.12¢ —— 逆向选择吃掉了价差的 92%。**

## 5. 排队代价，这次是量出来的不是假设的

019 在 `KXBTCD` 上把「盘口碰到我们的价」换成「必须打穿」，成交掉 60%、优势掉
四分之三。真成交能直接量同一件事，不需要成交规则：同一个市场里、同一主动方向、
1 秒之内的连续成交是**同一张主动单在扫盘**。排在档位最前面的人每次扫盘都成交；
排在后面的人只在大单来的时候才轮到。所以**按扫盘规模切 markout，就是按队列位置
切 markout**。

| 扫盘规模（张） | 占成交量 | ATP ¢/张 | WTA ¢/张 |
| --- | ---: | ---: | ---: |
| 0–100 | 3.4% | **+0.448** (t=17.6) | **+0.346** (t=17.8) |
| 100–500 | 14.0% | +0.285 (t=12.4) | +0.218 (t=10.2) |
| 500–2000 | 26% | +0.197 (t=5.9) | +0.076 (t=2.2) |
| 2000–10000 | 34% | +0.089 (t=1.8) | +0.035 (t=1.0) |
| ≥10000 | 22–24% | +0.057 (t=0.65) | +0.196 (t=0.81) |
| **只算 ≥2000 的（后排口径）** | 57% | **+0.076 (t=1.50)** | **+0.102 (t=0.98)** |

单调下降，两个系列一致。**优势基本上就是「我们排在队首」这件事本身**：小单
+0.35~0.45¢，大单 +0.03~0.09¢ 且与 0 无法区分。这和 019 在完全不同的市场、
用完全不同的方法得到的是同一个结论。

队首是一场延迟竞赛。本仓库走 REST API、跑在临时容器里，队首不是能选的位置。

## 6. 全所 markout 横截面（12 个市场，18 亿张真成交）

| 系列 | 结算数 | 张数 | markout 60s | 95% 区间 | t | 只算 ≥2000 张的扫盘 | 95% 区间 | t |
| --- | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |
| `KXATPCHALLENGERMATCH` | 188 | 141281226 | +0.150 | [+0.084,+0.206] | +4.84 | +0.076 | [−0.026,+0.171] | +1.50 |
| `KXWTACHALLENGERMATCH` | 143 | 130220796 | +0.120 | [+0.024,+0.251] | +2.05 | +0.102 | [−0.063,+0.332] | +0.98 |
| `KXITFMATCH` | 200 | 32906253 | +0.059 | [−0.106,+0.206] | +0.74 | +0.111 | [−0.187,+0.370] | +0.77 |
| `KXBTC15M` | 400 | 614882995 | +0.068 | [−0.056,+0.177] | +1.16 | **−0.145** | [−0.372,+0.049] | −1.36 |
| `KXBTCD` | 92 | 31524527 | +0.022 | [−0.090,+0.140] | +0.37 | +0.042 | [−0.284,+0.379] | +0.25 |
| `KXGOLD15M` | 300 | 65419498 | +0.095 | [+0.004,+0.192] | +2.02 | **−0.231** | [−0.529,+0.065] | −1.51 |
| `KXMLBGAME` | 152 | 326638733 | +0.058 | [−0.007,+0.134] | +1.63 | **−0.099** | [−0.197,+0.014] | −1.85 |
| `KXNCAAFGAME` | 168 | 145459283 | +0.064 | [−0.018,+0.143] | +1.56 | **−0.084** | [−0.182,+0.016] | −1.66 |
| `KXNFLGAME` | 28 | 209735859 | +0.031 | [−0.054,+0.108] | +0.75 | **−0.092** | [−0.198,−0.006] | −1.89 |
| `KXEPLGAME` | 30 | 100238566 | +0.109 | [−0.024,+0.218] | +1.81 | −0.073 | [−0.291,+0.110] | −0.72 |
| `KXCS2GAME` | 197 | 5531314 | +0.092 | [−0.133,+0.278] | +0.89 | +0.320 | [−0.442,+1.062] | +0.85 |
| `KXDAVISCUPMATCH` | 3 | 1369137 | +0.407 | [−0.225,+1.381] | +1.09 | +0.248 | [−0.018,+1.143] | +0.93 |

**整张表落在 +0.02 ~ +0.41 分之间，而这些市场的价差是 1–3 分。** 挂单方留下的不到
半条价差的十分之一。换成后排口径（只在 ≥2000 张的扫盘里成交），12 个里 6 个变负，
`KXNFLGAME` 的区间还不跨 0（−0.092，[−0.198, −0.006]）。**整轮扫描里统计上最站得住
的结论是负的那一个。**

顺带修正一条旧记录：018 用合成成交模型算出 `KXBTCD` 挂单 1 分钟 markout
**−0.11 分 / t=−3.94**。用真成交重算是 **+0.022 分、t=0.37** —— 那个负号也是成交模型
造出来的。诚实的答案是**零**，不是负。零同样不是生意，但记错方向会让人去修错的东西。

## 7. `KXBTC15M`：本轮唯一显著的结果，是我们自己的假阳性

第一次跑（10 天 / 400 次结算）：**+0.517 分/张，区间 [+0.29, +0.71]，t=4.86**，
名义 $322328482。全所名义额第一的市场，样本又够 —— 看上去像找到了。

复核发现 **400 个市场里 357 个（89%）的成交记录被分页上限截断**。
`/markets/trades` 从新到旧翻页，翻 20 页等于「只保留每个市场最后 20000 笔」，也就是
**只看临近结算的那一段**。换一个 4 天窗口、翻到底重跑 170 次结算（截断 0 个）：

| 口径 | 笔数 | 张数 | 挂单池 ¢/张 | 95% 区间 | t |
| --- | ---: | ---: | ---: | --- | ---: |
| 10 天 / 400 次结算 / 89% 被截断 | 7850745 | 615353670 | **+0.517** | [+0.29, +0.71] | +4.86 |
| 4 天 / 170 次结算 / 翻到底 | 5619582 | 433190014 | **+0.181** | [−0.20, +0.51] | +0.99 |
| 同一批市场人为截断到最后 20000 笔 | 3372510 | 290218518 | **+0.010** | [−0.42, +0.31] | +0.05 |

**同一个量在窗口和口径上晃动的幅度，比它自己声称的区间还大。** 截断和换窗口各自都
把它挪了半分以上，而 t=4.86 那个区间宽度只有 0.42 分。

翻到底之后再看其余各刀，没有一刀站得住：

- **60 秒 markout +0.0103 分/张，区间 [−0.144, +0.125]，t=0.15。** 做市真正在做的
  那件事是零。
- **只算 ≥2000 张的扫盘（后排口径）−0.2516 分，区间 [−0.554, −0.032]，不跨 0。**
  后排挂单方是亏的。
- 收成形状：最赚的 5 次结算占全部利润 **67%**，最差一次 **−$544090** 对总共
  **+$783219**，170 次里 96 次为正。
- 按持仓成本分桶，符号相对截断样本**全部翻转**（0.65–0.85 从 +8.93 变 −0.69，
  0.95–1.00 从 −0.42 变 −2.10）。截断样本里那个漂亮的「便宜那侧被高估」结构是假的。

### 7b. 第二个不重叠时间窗（样本外）

为了不让结论只站在一个窗口上，又取了一段**不与上面重叠**的 12 天（结算时间在 8–20 天前），
同样翻到底、同样 170 次结算：

| 切法 | 近 4 天（170 次结算） | 早 12 天（170 次结算） |
| --- | --- | --- |
| 挂单池 ¢/张 | +0.181 [−0.20, +0.51] t=0.99 | +0.256 [+0.03, +0.49] t=2.17 |
| 60 秒 markout ¢/张 | **+0.0103** [−0.144, +0.125] t=0.15 | **+0.0710** [−0.024, +0.154] t=1.58 |
| 只算 ≥2000 张扫盘（后排） | **−0.2516** [−0.554, −0.032] | **−0.1010** [−0.270, +0.041] |
| 最赚 5 次结算占比 | 67% | 42% |
| 持仓成本 0.65–0.85 | −0.687 [−6.54, +5.20] | **+6.430** [+1.38, +11.05] |
| 持仓成本 0.15–0.35 | +1.766 [−4.38, +7.82] | −4.967 [−9.98, +0.33] |

两个窗口合起来 340 次结算、8 亿张真成交、$4.16 亿名义，读法是一致的：

1. **挂单池可能真的是个小正数（+0.2 分上下）** —— 挂单方大致收走一丝价差，这不奇怪。
2. **但做市真正在做的那件事（markout）两个窗口都跨 0。**
3. **后排口径两个窗口都是负的。**
4. **按持仓成本分桶的结构在两个窗口之间符号整个翻转**（0.65–0.85 从 −0.69 变 +6.43，
   0.15–0.35 从 +1.77 变 −4.97）。**那不是结构，那是噪声。** 任何「便宜那侧被系统性
   高估」的说法都过不了这一关。

**另外要撤回 019 的一句话。** 019 说「判定所需的 tape 量这个交易所根本给不了」（高尾
需 1439 小时分钟 K 线，Kalshi 只回溯约 4 天）。这是对**分钟 K 线**说的，不是对交易所
说的：**结算结果和逐笔成交能回溯 30 天以上**，而 15 分钟盘一天 96 次结算，一天几千个
互不相干的结算标的。样本量在这个交易所不是瓶颈 —— 我们拿到了 400 次结算、6 亿张真
成交，答案是零。**真正的瓶颈是优势本身不存在。**

## 8. 没能测的两件事（环境所限，不是结论）

- **跨场馆价差。** 本容器出网白名单只放行 `external-api.kalshi.com` 和 GitHub。
  `clob.polymarket.com`、`gamma-api.polymarket.com`、`api.binance.com`、
  `api.pro.coinbase.com`、`api.predictit.org` 全部返回 000（连不上）。要测得先加白名单。
- **串关（`KXMVECROSSCATEGORY`，3 小时名义 $2927596，71968 个市场在交易）。** 唯一
  不需要相关性假设的关系是 Fréchet 界 `P(全中) ≤ min P(单腿)`。抓了成交量最大的 400 个
  串关，其中只有 4 个的全部单腿还在报价（其余单腿已收盘），这 4 个离界差 **40–55 分**。
  这个界对 8–9 条腿的串关松到不可能绑住。要判它就得先有一个相关性模型 —— 而 017 判掉的
  正是「靠自家模型比市场准」。**列为不可低成本证伪，不列为已测。**

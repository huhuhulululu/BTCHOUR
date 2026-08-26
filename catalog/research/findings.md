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

## Order book

ATM books are two-sided and deep at 1–4 cents. `yes_ask` on the market object is the real take price; 1-cent bids on both sides are inventory, not the touch for a 20% clip.

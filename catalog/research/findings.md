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

First pass used raw 1-minute realized vol (~0.30) and took two tickets:

| Hour | Ticket | EV | Result |
| --- | --- | --- | --- |
| `KXBTCD-26AUG2511` | NO `T79499.99` @ 0.78 | +21.9% | win +0.21 |
| `KXBTCD-26AUG2510` | YES `T78599.99` @ 0.77 | +21.9% | **loss −0.78** (BTC sold off through the strike) |

Net −0.57. The loser had only ~1.7σ of cushion once vol was understated. After flooring vol at `BTCHOUR_ANNUAL_VOL` (0.55), the same 8 hours replay at **0 takes / 0 pnl**. The previous winner is also filtered. Conservative idle is the point of the 20% gate.

## Order book

ATM books are two-sided and deep at 1–4 cents. `yes_ask` on the market object is the real take price; 1-cent bids on both sides are inventory, not the touch for a 20% clip.

"""Pull KXBTC (range/bucket) hourly tape. The repo's tape loader is hardcoded to
KXBTCD threshold tickers, so this is a parallel, read-only fetcher."""
import sys, json, gzip, pathlib
sys.path.insert(0, "/home/user/BTCHOUR")
from datetime import timedelta
from btchour.config import load_settings
from btchour.engine import make_client
from btchour.tickers import next_hourly_close, format_event_ticker

OUT = pathlib.Path("/home/user/BTCHOUR/data/kxbtc")
OUT.mkdir(parents=True, exist_ok=True)
S = load_settings()
c = make_client(S)
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 36

def bucket_center(m):
    lo, hi = m.get("floor_strike"), m.get("cap_strike")
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2.0
    if lo is not None:
        return float(lo)      # upper tail
    if hi is not None:
        return float(hi)      # lower tail
    return None

close = next_hourly_close()
tickers = [format_event_ticker(close - timedelta(hours=i), "KXBTC") for i in range(1, HOURS + 1)]
stored = skipped = 0
for i, ev in enumerate(tickers):
    path = OUT / f"{ev}.json.gz"
    if path.is_file():
        stored += 1; continue
    try:
        payload = c.get(f"/events/{ev}")
    except Exception as e:
        skipped += 1; print(f"  {ev}: event {type(e).__name__}", flush=True); continue
    markets = payload.get("markets") or []
    settled = [m for m in markets if (m.get("result") or "") in {"yes", "no"}]
    if not settled:
        skipped += 1; print(f"  {ev}: not settled ({len(markets)} markets)", flush=True); continue
    try:
        live = c.live_data(ev, "1h").get("live_data") or {}
    except Exception as e:
        skipped += 1; print(f"  {ev}: live {type(e).__name__}", flush=True); continue
    det = live.get("details") or {}
    series = det.get("timeseries") or []
    maturity_ms = int(det.get("maturity_ts_ms") or 0)
    if not series or not maturity_ms:
        skipped += 1; print(f"  {ev}: no spot path", flush=True); continue
    by_min = {}
    for p in series:
        by_min.setdefault((int(p["t"]) // 60_000) * 60_000, []).append(float(p["v"]))
    spots = {k: v[-1] for k, v in by_min.items()}
    lo_s, hi_s = min(spots.values()), max(spots.values())
    mid_s = (lo_s + hi_s) / 2.0
    cand = []
    for m in settled:
        ctr = bucket_center(m)
        if ctr is None: continue
        cand.append((abs(ctr - mid_s), m.get("ticker"), ctr, m.get("result"),
                     m.get("floor_strike"), m.get("cap_strike")))
    cand.sort()
    cand = cand[:26]
    start = int(maturity_ms / 1000) - 3600
    end = int(maturity_ms / 1000)
    candles = {}
    for _, tk, ctr, res, fl, cp in cand:
        try:
            d = c.get(f"/series/KXBTC/markets/{tk}/candlesticks",
                      {"start_ts": start, "end_ts": end, "period_interval": 1})
        except Exception:
            continue
        rows = {int(r["end_period_ts"]): r for r in d.get("candlesticks") or []}
        if rows:
            candles[tk] = {"center": ctr, "result": res, "floor": fl, "cap": cp, "rows": rows}
    if not candles:
        skipped += 1; print(f"  {ev}: no candles", flush=True); continue
    with gzip.open(path, "wt", encoding="utf-8") as h:
        json.dump({"event": ev, "spots": spots, "maturity_ms": maturity_ms, "markets": candles}, h)
    stored += 1
    if (i + 1) % 6 == 0:
        print(f"  {i+1}/{len(tickers)} stored={stored} skipped={skipped}", flush=True)
print(f"done stored={stored} skipped={skipped} files={len(list(OUT.glob('*.json.gz')))}", flush=True)

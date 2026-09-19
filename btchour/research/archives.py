"""Fetch Binance public archives, and Hyperliquid funding, reproducibly.

The Kalshi tape had to be committed to git because the exchange only looks
back four days: a sample nobody can re-pull is a sample nobody can check, and
that is why `.gitignore` keeps `data/archive/`. The Binance archives are the
opposite case. Every monthly zip at `data.binance.vision` is immutable, has a
published SHA-256 next to it, and downloads whole -- there is no cursor and no
page limit, so "did we reach the bottom" cannot go wrong the way it did in
decision 020. That makes the fetcher, not the bytes, the thing worth keeping
under version control, and it is why H1 and H2 were pulled from the archives
rather than from the REST endpoints.

Two operational notes, both learned the hard way:

* The per-file `.CHECKSUM` request doubles the request count and dominates
  wall clock on a wide pull -- 34k requests went from 3.4 hours to 20 minutes
  when `verify` was turned off for the cross-section. Keep verification on for
  a tape a conclusion rests on narrowly, off for a wide screen where a
  truncated file shows up immediately as a parse error.
* The bucket listing lives on the S3 host, not on `data.binance.vision`,
  which serves a Javascript browser page for any query string you give it. The
  listing is how the 471-symbol universe was enumerated; guessing symbol names
  is how you end up with a survivorship-filtered universe.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile

VISION = "https://data.binance.vision/data"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
HYPERLIQUID = "https://api.hyperliquid.xyz/info"

DATA_ROOT = os.path.join("data", "binance")

# Archive layouts, by the name this module uses for them.
PATHS = {
    "fundingRate": "futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{month}.zip",
    "klines": "futures/um/monthly/klines/{sym}/{iv}/{sym}-{iv}-{month}.zip",
    "markPriceKlines": "futures/um/monthly/markPriceKlines/{sym}/{iv}/{sym}-{iv}-{month}.zip",
    "premiumIndexKlines": "futures/um/monthly/premiumIndexKlines/{sym}/{iv}/{sym}-{iv}-{month}.zip",
    "spotKlines": "spot/monthly/klines/{sym}/{iv}/{sym}-{iv}-{month}.zip",
    "bookTicker": "futures/um/daily/bookTicker/{sym}/{sym}-bookTicker-{month}.zip",
}


def months(lo: str, hi: str) -> list[str]:
    """Inclusive list of `YYYY-MM` strings."""
    y, m = (int(x) for x in lo.split("-"))
    Y, M = (int(x) for x in hi.split("-"))
    out = []
    while (y, m) <= (Y, M):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


# Funding is not sampled on an interval we choose, so it gets its own slot
# name rather than a bar size.
FUNDING_SLOT = "8h"


def _slot(kind: str, interval: str) -> str:
    return FUNDING_SLOT if kind == "fundingRate" else interval


def _get(url: str, tries: int = 4) -> bytes | None:
    """None on a 404, which is an absent month rather than a failure."""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "btchour/archives"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 -- retried below
            if getattr(exc, "code", None) == 404:
                return None
            if i == tries - 1:
                raise
            time.sleep(2 ** i)
    return None


def fetch(
    kind: str,
    symbol: str,
    month: str,
    *,
    interval: str = "1h",
    root: str = DATA_ROOT,
    verify: bool = True,
) -> str:
    """Download one archive to `<root>/<kind>/<symbol>/<month>.csv`.

    Returns `cached`, `404`, `BADSUM`, or `ok:<bytes>`.
    """
    path = PATHS[kind].format(sym=symbol, month=month, iv=interval)
    # The interval belongs in the cache path. It did not use to, and the two
    # pulls that shared `klines/AVAXUSDT/2026-06.csv` -- one hourly, one daily
    # -- silently left hourly bars where daily ones were expected, because
    # `fetch` short-circuits on an existing file. Nothing downstream could see
    # it: the average of 365 *hours* of volume just looked like a quiet coin.
    dest = os.path.join(root, kind, _slot(kind, interval), symbol, f"{month}.csv")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return "cached"
    blob = _get(f"{VISION}/{path}")
    if blob is None:
        return "404"
    if verify:
        chk = _get(f"{VISION}/{path}.CHECKSUM")
        if chk and hashlib.sha256(blob).hexdigest() != chk.split()[0].decode():
            return "BADSUM"
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        data = z.read(z.namelist()[0]).decode()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as handle:
        handle.write(data)
    return f"ok:{len(data)}"


def list_symbols(prefix: str) -> list[str]:
    """Enumerate one level of the bucket. Paginated, and it follows the cursor.

    Decision 020's worst measurement error was a page limit mistaken for the
    end of the data, so this loop follows `NextContinuationToken` until the
    bucket says there is no more rather than stopping at the first page.
    """
    out: list[str] = []
    token = None
    while True:
        url = (
            f"{S3}?list-type=2&delimiter=/&max-keys=1000"
            f"&prefix={urllib.parse.quote(prefix)}"
        )
        if token:
            url += "&continuation-token=" + urllib.parse.quote(token)
        body = _get(url)
        if body is None:
            break
        text = body.decode()
        out += [
            p[len(prefix) :].rstrip("/")
            for p in re.findall(r"<Prefix>([^<]+)</Prefix>", text)
            if p != prefix
        ]
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", text)
        if not m:
            break
        token = m.group(1)
    return sorted(set(out))


def spot_perp_universe() -> dict[str, list[str]]:
    """Every USDT symbol with a spot book, a perp book, or both."""
    spot = [s for s in list_symbols("data/spot/monthly/klines/") if s.endswith("USDT")]
    perp = [
        s for s in list_symbols("data/futures/um/monthly/klines/") if s.endswith("USDT")
    ]
    return {
        "spot_usdt": spot,
        "perp_usdt": perp,
        "both": sorted(set(spot) & set(perp)),
    }


# --------------------------------------------------------------------------
# Hyperliquid. The second venue, and the only other one this container can
# reach: Binance and Bybit's REST endpoints answer 451 and 403 to this egress
# IP regardless of the allowlist, so the archives are the only Binance road in.
# --------------------------------------------------------------------------


def _hl_post(body: dict, tries: int = 5):
    req = urllib.request.Request(
        HYPERLIQUID,
        data=__import__("json").dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return __import__("json").load(r)
        except Exception:  # noqa: BLE001 -- retried below
            if i == tries - 1:
                raise
            time.sleep(1.5 ** i)
    return None


def hl_universe() -> list[str]:
    meta = _hl_post({"type": "meta"})
    return [u["name"] for u in meta["universe"] if not u.get("isDelisted")]


def hl_funding(coin: str, start_ms: int, end_ms: int) -> dict[int, tuple[float, float]]:
    """Hourly (rate, premium) by timestamp.

    The endpoint returns at most 500 rows, so this walks the cursor forward.
    Rates are hourly here and 4- or 8-hourly on Binance, and 282 of 407
    Binance perps settle on 4 hours while 92 of them changed interval inside a
    single year. So nothing downstream may annualize by assuming an interval:
    sum the settled rates over a window and scale by that window's length.
    """
    rows: dict[int, tuple[float, float]] = {}
    t = start_ms
    while t < end_ms:
        batch = _hl_post({"type": "fundingHistory", "coin": coin, "startTime": t})
        if not batch:
            break
        for x in batch:
            rows[x["time"]] = (float(x["fundingRate"]), float(x["premium"]))
        nxt = max(x["time"] for x in batch) + 1
        if nxt <= t:
            break
        t = nxt
        if len(batch) < 500:
            break
    return rows


def hl_coin_for(symbol: str) -> str:
    """`1000PEPEUSDT` and `PEPE` are the same coin on the two venues."""
    s = symbol[:-4] if symbol.endswith("USDT") else symbol
    for pre in ("1000000", "1000", "1M"):
        if s.startswith(pre) and len(s) > len(pre):
            return s[len(pre) :]
    return s

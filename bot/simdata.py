"""Made-up market data for the demo mode (no API keys needed)."""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

from .broker import ET

START = {"QQQ": 600.0, "SPY": 660.0, "IWM": 240.0}


def synthetic_day(symbols, d: date, last_close: dict, seed: int | None = None) -> dict[str, list[dict]]:
    """390 one-minute bars per symbol with random trends, chop and the odd sharp dip."""
    rng = random.Random(seed)
    out = {}
    common = [rng.gauss(0, 1) for _ in range(390)]     # symbols move together, like the real market
    regime_len = rng.randint(40, 120)
    drift = rng.gauss(0, 0.00012)
    for sym in symbols:
        px = last_close.get(sym, START.get(sym, 100.0)) * (1 + rng.gauss(0, 0.004))
        vol = {"IWM": 0.0011, "QQQ": 0.0009}.get(sym, 0.0007)
        bars = []
        local_drift, left = drift, regime_len
        for i in range(390):
            left -= 1
            if left <= 0:
                local_drift, left = rng.gauss(0, 0.00015), rng.randint(30, 120)
            shock = 0.7 * common[i] + 0.3 * rng.gauss(0, 1)
            if rng.random() < 0.004:          # occasional flush
                shock -= rng.uniform(3, 6)
            ret = local_drift + vol * shock
            o = px
            c = px * math.exp(ret)
            h = max(o, c) * (1 + abs(rng.gauss(0, vol / 3)))
            l = min(o, c) * (1 - abs(rng.gauss(0, vol / 3)))
            t = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET) + timedelta(minutes=i)
            v = rng.randint(2000, 9000) * (3 if i < 30 or i > 360 else 1)
            bars.append({"t": t, "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2), "v": v})
            px = c
        out[sym] = bars
        last_close[sym] = px
    return out


def weekdays_back(n: int, end: date | None = None) -> list[date]:
    d = end or datetime.now(ET).date()
    days = []
    while len(days) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
    return days[::-1]

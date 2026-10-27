"""Price indicators computed from 1-minute bars.

A bar is a dict: {"t": datetime (ET), "o": float, "h": float, "l": float, "c": float, "v": float}
"""
from __future__ import annotations


def ema(values: list[float], length: int) -> list[float]:
    """Exponential moving average. Returns a list the same length as values."""
    if not values:
        return []
    k = 2 / (length + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def atr(bars: list[dict], length: int = 14) -> list[float]:
    """Average True Range (Wilder smoothing) - how much price typically moves per bar."""
    if not bars:
        return []
    trs = []
    prev_close = bars[0]["c"]
    for b in bars:
        tr = max(b["h"] - b["l"], abs(b["h"] - prev_close), abs(b["l"] - prev_close))
        trs.append(tr)
        prev_close = b["c"]
    out = [trs[0]]
    for tr in trs[1:]:
        out.append((out[-1] * (length - 1) + tr) / length)
    return out


def session_vwap(bars: list[dict]) -> list[float | None]:
    """Volume-weighted average price that resets at the start of each trading day.

    Bars from earlier days get None for the parts of the list before today's
    session, so VWAP is only meaningful for today's bars.
    """
    out: list[float | None] = []
    cum_pv = cum_v = 0.0
    day = None
    for b in bars:
        d = b["t"].date()
        if d != day:
            day, cum_pv, cum_v = d, 0.0, 0.0
        typical = (b["h"] + b["l"] + b["c"]) / 3
        vol = max(b["v"], 1.0)
        cum_pv += typical * vol
        cum_v += vol
        out.append(cum_pv / cum_v)
    return out

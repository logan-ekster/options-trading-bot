"""Entry signals. Each strategy looks at completed 1-minute bars and returns a Scan.

Scan.signal is "call", "put" or None.
Scan.charge (0-100) is how close the setup is to firing - this drives the
"% CHARGED" bar on the dashboard, like the Signal Scanner in the video.
"""
from __future__ import annotations

from dataclasses import dataclass

from .indicators import atr, ema, session_vwap


@dataclass
class Scan:
    signal: str | None      # "call" / "put" / None
    label: str              # e.g. "EMA50 cross ▲ CALL · 0.49 ATR away"
    charge: int             # 0-100
    reason: str = ""        # explanation written to the thoughts log when it fires


def _today(bars):
    d = bars[-1]["t"].date()
    return [b for b in bars if b["t"].date() == d]


def _charge(dist_atr: float, max_away: float) -> int:
    return int(max(0, min(100, (1 - dist_atr / max_away) * 100)))


def ema_cross(bars: list[dict], s: dict) -> Scan:
    """Fire a CALL when price closes back above the EMA, a PUT when it closes below."""
    n = int(s.get("ema_length", 50))
    confirm = int(s.get("confirm_bars", 1))
    max_away = float(s.get("max_atr_away", 1.0))
    if len(bars) < n + confirm + 2:
        return Scan(None, f"EMA{n} warming up", 0)
    closes = [b["c"] for b in bars]
    e = ema(closes, n)
    a = atr(bars)[-1] or 1e-9
    dist = (closes[-1] - e[-1]) / a
    side = "▲ CALL" if closes[-1] < e[-1] else "▼ PUT"
    label = f"EMA{n} cross {side} · {abs(dist):.2f} ATR away"

    # crossed up: `confirm` closes above after being below
    before = closes[-confirm - 1] - e[-confirm - 1]
    after = [closes[-i] - e[-i] for i in range(1, confirm + 1)]
    if before <= 0 and all(x > 0 for x in after) and abs(dist) <= max_away:
        return Scan("call", f"EMA{n} cross ▲ CALL", 100,
                    f"closed above EMA{n} ({e[-1]:.2f})")
    if before >= 0 and all(x < 0 for x in after) and abs(dist) <= max_away:
        return Scan("put", f"EMA{n} cross ▼ PUT", 100,
                    f"closed below EMA{n} ({e[-1]:.2f})")
    return Scan(None, label, _charge(abs(dist), max_away * 3))


def vwap_cross(bars: list[dict], s: dict) -> Scan:
    """Fire when price crosses VWAP and holds for `confirm_bars` closes."""
    confirm = int(s.get("confirm_bars", 2))
    max_away = float(s.get("max_atr_away", 1.0))
    today = _today(bars)
    if len(today) < confirm + 5:
        return Scan(None, "VWAP warming up", 0)
    vw = session_vwap(today)
    closes = [b["c"] for b in today]
    a = atr(bars)[-1] or 1e-9
    dist = (closes[-1] - vw[-1]) / a
    side = "▲ CALL" if closes[-1] < vw[-1] else "▼ PUT"
    label = f"VWAP cross {side} · {abs(dist):.2f} ATR away"
    before = closes[-confirm - 1] - vw[-confirm - 1]
    after = [closes[-i] - vw[-i] for i in range(1, confirm + 1)]
    if before <= 0 and all(x > 0 for x in after) and abs(dist) <= max_away:
        return Scan("call", "VWAP cross ▲ CALL", 100, f"reclaimed VWAP ({vw[-1]:.2f})")
    if before >= 0 and all(x < 0 for x in after) and abs(dist) <= max_away:
        return Scan("put", "VWAP cross ▼ PUT", 100, f"lost VWAP ({vw[-1]:.2f})")
    return Scan(None, label, _charge(abs(dist), max_away * 3))


def trend(bars: list[dict], s: dict) -> Scan:
    """Trend pullback: EMAs stacked in one direction, price dips to the middle EMA
    and closes back in the trend direction."""
    f, m, sl = int(s.get("fast", 9)), int(s.get("mid", 21)), int(s.get("slow", 50))
    if len(bars) < sl + 5:
        return Scan(None, "TREND warming up", 0)
    closes = [b["c"] for b in bars]
    ef, em, es = ema(closes, f), ema(closes, m), ema(closes, sl)
    a = atr(bars)[-1] or 1e-9
    up = ef[-1] > em[-1] > es[-1]
    down = ef[-1] < em[-1] < es[-1]
    prev, last = bars[-2], bars[-1]
    if up:
        touched = prev["l"] <= em[-2] + 0.1 * a
        if touched and last["c"] > ef[-1] and last["c"] > last["o"]:
            return Scan("call", "TREND ▲ CALL", 100, f"uptrend pullback to EMA{m} bounced")
        dist = max(0.0, (prev["l"] - em[-2]) / a)
        return Scan(None, f"Uptrend · waiting for pullback ({dist:.2f} ATR)", _charge(dist, 3))
    if down:
        touched = prev["h"] >= em[-2] - 0.1 * a
        if touched and last["c"] < ef[-1] and last["c"] < last["o"]:
            return Scan("put", "TREND ▼ PUT", 100, f"downtrend pullback to EMA{m} rejected")
        dist = max(0.0, (em[-2] - prev["h"]) / a)
        return Scan(None, f"Downtrend · waiting for pullback ({dist:.2f} ATR)", _charge(dist, 3))
    return Scan(None, "No clear trend", 0)


def buy_the_dip(bars: list[dict], s: dict) -> Scan:
    """Calls only: price stretched well below VWAP, then a green reversal bar."""
    k = float(s.get("dip_atr", 1.5))
    today = _today(bars)
    if len(today) < 15:
        return Scan(None, "DIP warming up", 0)
    vw = session_vwap(today)
    a = atr(bars)[-1] or 1e-9
    prev, last = today[-2], today[-1]
    stretch = (vw[-2] - prev["l"]) / a
    if stretch >= k and last["c"] > last["o"] and last["c"] > prev["h"]:
        return Scan("call", "BUY THE DIP ▲ CALL", 100,
                    f"{stretch:.1f} ATR below VWAP then reversal bar")
    now_stretch = max(0.0, (vw[-1] - last["l"]) / a)
    return Scan(None, f"Dip depth {now_stretch:.2f}/{k:.1f} ATR", int(min(100, now_stretch / k * 100)))


def orb(bars: list[dict], s: dict) -> Scan:
    """Opening Range Breakout: the high/low of the first N minutes (default 15).
    A 1-minute close above the range high -> CALL, below the range low -> PUT.
    Only the first break in each direction counts."""
    n = int(s.get("range_minutes", 15))
    today = _today(bars)
    if not today:
        return Scan(None, "ORB waiting for the open", 0)
    start = today[0]["t"].replace(hour=9, minute=30, second=0, microsecond=0)
    rng = [b for b in today if (b["t"] - start).total_seconds() < n * 60]
    after = [b for b in today if (b["t"] - start).total_seconds() >= n * 60]
    if len(rng) < max(3, n // 3):
        return Scan(None, f"ORB building {n}-min range", int(100 * len(rng) / n))
    if not after:
        return Scan(None, f"ORB range set · waiting", 0)
    hi, lo = max(b["h"] for b in rng), min(b["l"] for b in rng)
    a = atr(bars)[-1] or 1e-9
    closes = [b["c"] for b in after]
    last, prev = closes[-1], (closes[-2] if len(closes) > 1 else rng[-1]["c"])
    broke_up_before = any(c > hi for c in closes[:-1])
    broke_dn_before = any(c < lo for c in closes[:-1])
    if last > hi and prev <= hi and not broke_up_before:
        return Scan("call", "ORB breakout ▲ CALL", 100, f"broke above {n}-min high ({hi:.2f})")
    if last < lo and prev >= lo and not broke_dn_before:
        return Scan("put", "ORB breakdown ▼ PUT", 100, f"broke below {n}-min low ({lo:.2f})")
    d_up, d_dn = (hi - last) / a, (last - lo) / a
    if broke_up_before and broke_dn_before:
        return Scan(None, "ORB both sides already broken", 0)
    if (d_up <= d_dn and not broke_up_before) or broke_dn_before:
        return Scan(None, f"ORB ▲ CALL · {max(d_up, 0):.2f} ATR to high", _charge(max(d_up, 0), 3))
    return Scan(None, f"ORB ▼ PUT · {max(d_dn, 0):.2f} ATR to low", _charge(max(d_dn, 0), 3))


def combo(bars: list[dict], s: dict) -> Scan:
    """The video's trigger: fire on ANY of VWAP cross, 50 EMA cross, or opening-range break.
    If two signals disagree on the same bar (one says call, one says put) it sits out."""
    allcfg = s.get("_all", {})
    use = s.get("use", ["vwap_cross", "ema_cross", "orb"])
    scans = [STRATEGIES[name](bars, allcfg.get(name, {})) for name in use if name in STRATEGIES and name != "combo"]
    fired = [x for x in scans if x.signal]
    sides = {x.signal for x in fired}
    if len(sides) == 1:
        f = fired[0]
        return Scan(f.signal, f.label, 100, f.reason)
    if len(sides) > 1:
        return Scan(None, "signals disagree (call vs put) · sitting out", 50)
    best = max(scans, key=lambda x: x.charge) if scans else Scan(None, "waiting", 0)
    return Scan(None, best.label, best.charge)


STRATEGIES = {
    "ema_cross": ema_cross,
    "vwap_cross": vwap_cross,
    "trend": trend,
    "buy_the_dip": buy_the_dip,
    "orb": orb,
    "combo": combo,
}

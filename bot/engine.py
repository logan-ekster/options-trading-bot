"""The engine: runs every worker, enforces the risk limits, writes dashboard state."""
from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .broker import ET, Contract
from .journal import Journal
from .strategies import STRATEGIES, Scan


def _mins(t: datetime) -> int:
    """Minutes since the 9:30 open (0-389) - x position on the tower charts."""
    return (t.hour - 9) * 60 + t.minute - 30


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class Position:
    contract: Contract
    qty: int
    entry: float
    entry_time: datetime
    trade_id: int
    fees: float = 0.0
    peak: float = 0.0
    last: float = 0.0
    trailing: bool = False


@dataclass
class Worker:
    name: str
    symbol: str
    strategy: str
    cfg: dict
    status: str = "warming up"
    day: date | None = None
    day_pnl: float = 0.0
    trades_today: int = 0
    last_exit: datetime | None = None
    scan: Scan = field(default_factory=lambda: Scan(None, "waiting for data", 0))
    position: Position | None = None
    thoughts: deque = field(default_factory=lambda: deque(maxlen=12))
    last_fire: str | None = None   # timestamp of last entry (dashboard beam)
    last_result: dict | None = None  # {"t": iso, "pnl": float} of last exit (dashboard pop-up)
    today_trades: list = field(default_factory=list)  # entries/exits today, for the tower chart

    echo: bool = False   # print thoughts to the terminal (paper/live mode)

    def think(self, now: datetime, text: str, tone: str = "info"):
        self.thoughts.appendleft({"t": now.strftime("%H:%M"), "text": text, "tone": tone})
        if self.echo:
            print(f"[{now:%H:%M}] {self.name:<12} {text}", flush=True)

    @property
    def off_duty(self) -> bool:
        return self.status in ("goal hit · off duty", "max loss · stopped", "done for day", "halted",
                               "team goal hit · off duty")


class Engine:
    def __init__(self, cfg: dict, broker, journal: Journal, state_path: str | Path | None = None,
                 mode_label: str = "PAPER"):
        self.cfg = cfg
        self.broker = broker
        self.journal = journal
        self.state_path = Path(state_path) if state_path else None
        self.mode_label = mode_label
        self.risk = cfg["risk"]
        self.exits = cfg["exits"]
        self.contract_cfg = cfg["contracts"]
        self.strat_cfg = cfg.get("strategy_settings", {})
        self.workers = [Worker(w["name"], w["symbol"].upper(), w["strategy"], w) for w in cfg["workers"]]
        for w in self.workers:
            if w.strategy not in STRATEGIES:
                raise ValueError(f"Unknown strategy '{w.strategy}' for worker {w.name}. "
                                 f"Choose from: {', '.join(STRATEGIES)}")
        self.prices: dict[str, float] = {}
        self.day: date | None = None
        self.halted_reason: str | None = None
        self.goal_reason: str | None = None
        self.ticker: deque = deque(maxlen=30)
        self.last_now: datetime | None = None
        self.chart: dict[str, list] = {}

    # ── day handling ────────────────────────────────────────
    def new_day(self, d: date):
        self.day = d
        self.halted_reason = None
        self.goal_reason = None
        for w in self.workers:
            # pick up anything already closed today (e.g. the bot was restarted mid-day),
            # so daily goals / loss limits still count the whole day
            w.day, w.day_pnl, w.last_exit = d, self.journal.day_pnl(str(d), w.name), None
            w.trades_today = self.journal.count_trades(str(d), w.name)
            w.today_trades = []
            w.thoughts.clear()
            w.thoughts.appendleft({"t": "", "text": f"── {d:%a %b %d} ──", "tone": "muted"})
            w.status = "watching" if w.position is None else "in trade"

    def day_total(self) -> float:
        return sum(w.day_pnl for w in self.workers)

    def open_count(self) -> int:
        return sum(1 for w in self.workers if w.position)

    # ── called once per completed 1-minute bar ──────────────
    def on_bars(self, bars_by_symbol: dict[str, list[dict]], now: datetime):
        self.last_now = now
        if self.day != now.date():
            self.new_day(now.date())
        for sym, bars in bars_by_symbol.items():
            if bars:
                self.prices[sym] = bars[-1]["c"]
                today = now.date()
                self.chart[sym] = [[_mins(b["t"]), round(b["c"], 2)] for b in bars if b["t"].date() == today][-390:]
        in_window = _t(self.risk["trade_window_start"]) <= now.time() <= _t(self.risk["trade_window_end"])

        for w in self.workers:
            bars = bars_by_symbol.get(w.symbol) or []
            if not bars:
                continue
            settings = dict(self.strat_cfg.get(w.strategy, {}) or {})
            if w.strategy == "combo":
                settings["_all"] = self.strat_cfg
            w.scan = STRATEGIES[w.strategy](bars, settings)
            if w.position or w.off_duty:
                continue
            if not in_window:
                w.status = "outside trade window"
                continue
            w.status = "watching"
            if w.scan.signal is None:
                continue
            # ── gates ──
            if self.halted_reason or self.goal_reason:
                continue
            if w.trades_today >= int(w.cfg.get("max_trades_per_day", 3)):
                w.status = "done for day"
                w.think(now, "max trades for today - done", "muted")
                continue
            cd = int(w.cfg.get("cooldown_minutes", 10))
            if w.last_exit and now - w.last_exit < timedelta(minutes=cd):
                continue
            if self.open_count() >= int(self.risk["max_open_trades_total"]):
                w.think(now, f"signal {w.scan.label} skipped - too many open trades", "muted")
                continue
            self._enter(w, w.scan, now)

    def _enter(self, w: Worker, scan: Scan, now: datetime):
        spot = self.prices[w.symbol]
        try:
            c = self.broker.find_contract(w.symbol, scan.signal, now, self.contract_cfg, spot=spot)
        except Exception as e:  # network / API hiccup - skip this signal
            w.think(now, f"contract lookup failed: {e}", "bad")
            return
        if c is None:
            w.think(now, "no tradable contract found", "muted")
            return
        px = self.broker.quote(c)
        if not px:
            w.think(now, "no quote for contract - skipped", "muted")
            return
        per = px * 100
        qty = min(int(self.risk["max_contracts_per_trade"]), math.floor(float(self.risk["max_dollars_per_trade"]) / per))
        if qty < 1:
            w.think(now, f"1 contract costs ${per:,.0f} > max per trade - skipped", "muted")
            return
        filled, avg, fees = self.broker.buy(c, qty)
        if not filled:
            w.think(now, "order didn't fill - cancelled", "muted")
            return
        arrow = "▲ CALL" if c.kind == "call" else "▼ PUT"
        tid = self.journal.open_trade(
            day=str(now.date()), worker=w.name, underlying=w.symbol, contract=c.symbol, kind=c.kind,
            strike=c.strike, expiry=str(c.expiry), qty=filled, entry_time=now.isoformat(),
            entry_price=avg, entry_reason=scan.reason)
        w.position = Position(c, filled, avg, now, tid, fees, peak=avg, last=avg)
        w.trades_today += 1
        w.status = "in trade"
        w.last_fire = now.isoformat()
        w.today_trades.append({"m": _mins(now - timedelta(minutes=1)), "kind": c.kind, "entry": avg,
                               "spot": round(spot, 2), "why": scan.label, "pnl": None, "xm": None, "reason": None})
        w.think(now, f"FIRE {arrow} ×{filled} @ {avg:.2f} ({w.symbol} {spot:.2f}) - {scan.reason}", "fire")
        self.ticker.appendleft(f"{w.name} {now:%a %m/%d %H:%M} bought {filled} {c.kind.upper()} @ {avg:.2f}")

    # ── called often (every bar in backtest, every ~15s live) ──
    def on_tick(self, now: datetime):
        self.last_now = now
        flatten = now.time() >= _t(self.risk["flatten_time"])
        for w in self.workers:
            p = w.position
            if not p:
                if flatten and not w.off_duty and w.status != "done for day":
                    w.status = "market closing"
                continue
            try:
                px = self.broker.quote(p.contract)
            except Exception:
                px = None
            if px is None:
                if flatten:
                    self._exit(w, now, "end of day flatten", p.last)
                continue
            p.last = px
            p.peak = max(p.peak, px)
            gain = (px - p.entry) / p.entry * 100
            peak_gain = (p.peak - p.entry) / p.entry * 100
            if peak_gain >= float(self.exits["trail_activate_pct"]):
                p.trailing = True
            mins = (now - p.entry_time).total_seconds() / 60

            reason = None
            if gain <= -float(self.exits["stop_loss_pct"]):
                reason = "stopped out"
            elif gain >= float(self.exits["take_profit_pct"]):
                reason = "profit locked"
            elif p.trailing and px <= p.peak * (1 - float(self.exits["trail_giveback_pct"]) / 100):
                reason = "rode the trail"
            elif mins >= float(self.exits["max_minutes_in_trade"]):
                reason = "time exit"
            elif flatten:
                reason = "end of day flatten"
            if reason:
                self._exit(w, now, reason)

        # account-wide kill switch
        unrealized = sum((w.position.last - w.position.entry) * 100 * w.position.qty
                         for w in self.workers if w.position)
        if not self.halted_reason and self.day_total() + unrealized <= -float(self.risk["max_daily_loss_total"]):
            self.halted_reason = f"daily loss limit ${self.risk['max_daily_loss_total']} hit"
            for w in self.workers:
                if w.position:
                    self._exit(w, now, "kill switch")
                w.status = "halted"
                w.think(now, f"HALTED - {self.halted_reason}", "bad")

        # account-wide profit target ("once you get up to a set amount it stops trading for the day")
        target = float(self.risk.get("daily_profit_target_total", 0) or 0)
        if target > 0 and not self.goal_reason and not self.halted_reason and self.day_total() >= target:
            self.goal_reason = f"team goal ${target:,.0f} hit"
            for w in self.workers:
                if not w.position:
                    w.status = "team goal hit · off duty"
                w.think(now, f"TEAM GOAL - up ${self.day_total():,.0f} today, done trading", "win")

    def _exit(self, w: Worker, now: datetime, reason: str, fallback_px: float | None = None):
        p = w.position
        try:
            px, fees = self.broker.sell(p.contract, p.qty)
        except Exception as e:
            msg = str(e).lower()
            if "insufficient qty" in msg or "position does not exist" in msg or "not found" in msg:
                # the position is already gone at the broker (expired / closed by hand)
                self.journal.close_trade(p.trade_id, now.isoformat(), None, "closed outside bot (check broker)", p.fees, 0)
                w.position = None
                w.last_exit = now
                w.status = "watching"
                w.think(now, f"{p.contract.symbol} was already closed at Alpaca - check the app for the result", "bad")
                return
            w.think(now, f"SELL FAILED ({e}) - check your broker app!", "bad")
            return
        if px is None:
            px = fallback_px or p.last
        fees_total = p.fees + fees
        pnl = round((px - p.entry) * 100 * p.qty - fees_total, 2)
        self.journal.close_trade(p.trade_id, now.isoformat(), px, reason, fees_total, pnl)
        w.day_pnl += pnl
        w.position = None
        w.last_exit = now
        w.last_result = {"t": now.isoformat(), "pnl": pnl, "reason": reason}
        if w.today_trades and w.today_trades[-1]["pnl"] is None:
            w.today_trades[-1].update({"pnl": pnl, "xm": _mins(now - timedelta(minutes=1)), "reason": reason})
        sign = "+" if pnl >= 0 else "-"
        w.think(now, f"{reason} {sign}${abs(pnl):,.0f} (sold @ {px:.2f})", "win" if pnl >= 0 else "loss")
        self.ticker.appendleft(f"{w.name} {now:%a %m/%d %H:%M} sold {sign}${abs(pnl):,.0f} · {reason}")
        w.status = "team goal hit · off duty" if self.goal_reason else "watching"
        if w.day_pnl >= float(w.cfg.get("daily_goal", 1e9)):
            w.status = "goal hit · off duty"
            w.think(now, f"daily goal ${w.cfg['daily_goal']} hit - off duty", "win")
        elif w.day_pnl <= -float(w.cfg.get("daily_max_loss", 1e9)):
            w.status = "max loss · stopped"
            w.think(now, f"daily max loss ${w.cfg['daily_max_loss']} hit - stopped for today", "bad")

    def flatten_all(self, now: datetime, reason: str = "end of day flatten"):
        for w in self.workers:
            if w.position:
                self._exit(w, now, reason)

    # ── restart recovery ────────────────────────────────────
    def recover_open_trades(self):
        """If the bot was restarted mid-trade, re-attach to positions still open at the broker."""
        from datetime import date as _d
        import time as _time
        for t in self.journal.open_trades():
            w = next((x for x in self.workers if x.name == t["worker"]), None)
            qty = None
            for _ in range(12):   # network may still be waking up - keep asking for ~2 minutes
                qty = self.broker.position_qty(t["contract"]) if hasattr(self.broker, "position_qty") else 0
                if qty is not None:
                    break
                _time.sleep(10)
            if qty is None:       # still can't confirm: keep managing it rather than forgetting it
                qty = int(t["qty"] or 1)
                if w:
                    w.think(datetime.now(ET), f"couldn't reach Alpaca to confirm {t['contract']} - managing it anyway", "bad")
            if w and qty > 0:
                c = Contract(t["contract"], t["underlying"], t["kind"], t["strike"], _d.fromisoformat(t["expiry"]))
                et = datetime.fromisoformat(t["entry_time"])
                w.position = Position(c, qty, t["entry_price"], et, t["id"], peak=t["entry_price"], last=t["entry_price"])
                w.status = "in trade"
                w.think(datetime.now(ET), f"resumed open trade {t['contract']} ×{qty}", "info")
            else:
                self.journal.close_trade(t["id"], datetime.now(ET).isoformat(), None,
                                         "closed outside bot (check broker)", 0, 0)

    # ── dashboard state ─────────────────────────────────────
    def snapshot(self, extra: dict | None = None) -> dict:
        totals = self.journal.worker_totals()
        workers = []
        for w in self.workers:
            p = w.position
            workers.append({
                "name": w.name, "symbol": w.symbol, "strategy": w.strategy, "status": w.status,
                "day_pnl": round(w.day_pnl, 2), "all_time": round((totals.get(w.name) or {}).get("pnl") or 0, 2),
                "trades_today": w.trades_today, "max_trades": w.cfg.get("max_trades_per_day"),
                "daily_goal": w.cfg.get("daily_goal"), "last_fire": w.last_fire,
                "last_result": w.last_result,
                "trades_all_time": (totals.get(w.name) or {}).get("n") or 0,
                "wins_all_time": (totals.get(w.name) or {}).get("wins") or 0,
                "scan": {"label": w.scan.label, "charge": w.scan.charge, "signal": w.scan.signal},
                "today_trades": w.today_trades,
                "position": None if not p else {
                    "contract": p.contract.symbol, "kind": p.contract.kind, "qty": p.qty,
                    "entry": p.entry, "last": p.last, "trailing": p.trailing,
                    "gain_pct": round((p.last - p.entry) / p.entry * 100, 1),
                    "unrealized": round((p.last - p.entry) * 100 * p.qty, 2)},
                "thoughts": list(w.thoughts),
            })
        st = self.journal.stats()
        snap = {
            "mode": self.mode_label, "broker": getattr(self.broker, "name", "?"),
            "updated": (self.last_now or datetime.now(ET)).isoformat(),
            "day": str(self.day) if self.day else None,
            "prices": self.prices, "day_total": round(self.day_total(), 2),
            "halted": self.halted_reason, "team_goal": self.goal_reason, "workers": workers,
            "profit_target": self.risk.get("daily_profit_target_total"),
            "loss_limit": self.risk.get("max_daily_loss_total"),
            "history": self.journal.daily_history(), "stats": st, "ticker": list(self.ticker),
            "chart": self.chart,
        }
        if extra:
            snap.update(extra)
        return snap

    def write_state(self, extra: dict | None = None):
        if not self.state_path:
            return
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.snapshot(extra), default=str))
        tmp.replace(self.state_path)

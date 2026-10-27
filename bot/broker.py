"""Brokers: where orders actually go.

SimBroker    - fake fills priced with Black-Scholes. Used for demo + backtest.
AlpacaBroker - Alpaca's API. paper=True uses the paper (fake money) account.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass
class Contract:
    symbol: str        # OCC symbol, e.g. QQQ251010C00715000
    underlying: str
    kind: str          # "call" / "put"
    strike: float
    expiry: date


def occ_symbol(underlying: str, expiry: date, kind: str, strike: float) -> str:
    return f"{underlying}{expiry:%y%m%d}{'C' if kind == 'call' else 'P'}{int(round(strike * 1000)):08d}"


def add_trading_days(d: date, n: int) -> date:
    """Add n weekdays (ignores market holidays - fine for simulation)."""
    while n > 0 or d.weekday() >= 5:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


# ── Black-Scholes (simulation pricing only) ─────────────────
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, years: float, vol: float, kind: str, r: float = 0.04) -> float:
    years = max(years, 1 / (365 * 24 * 60))
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    if kind == "call":
        return spot * _ncdf(d1) - strike * math.exp(-r * years) * _ncdf(d2)
    return strike * math.exp(-r * years) * _ncdf(-d2) - spot * _ncdf(-d1)


class SimBroker:
    """Pretend broker. The engine tells it the current stock prices; it prices
    options with Black-Scholes and fills every order with a little slippage."""

    paper = True
    name = "simulator"

    def __init__(self, sim_cfg: dict):
        self.iv = sim_cfg.get("implied_vol", {})
        self.slip = float(sim_cfg.get("slippage_per_contract", 0.03))
        self.fee = float(sim_cfg.get("fee_per_contract", 0.03))
        self.prices: dict[str, float] = {}
        self.now: datetime | None = None

    def set_market(self, symbol: str, price: float, now: datetime):
        self.prices[symbol] = price
        self.now = now

    def find_contract(self, symbol, kind, now, cfg, spot: float | None = None) -> Contract | None:
        spot = self.prices[symbol]
        off = int(cfg.get("strike_offset", 0))
        strike = round(spot) + (off if kind == "call" else -off)
        expiry = add_trading_days(now.date(), int(cfg.get("min_days_to_expiry", 1)))
        return Contract(occ_symbol(symbol, expiry, kind, strike), symbol, kind, float(strike), expiry)

    def _mid(self, c: Contract) -> float:
        close = datetime.combine(c.expiry, datetime.min.time(), ET).replace(hour=16)
        years = (close - self.now).total_seconds() / (365 * 24 * 3600)
        return max(0.01, bs_price(self.prices[c.underlying], c.strike, years, self.iv.get(c.underlying, 0.2), c.kind))

    def quote(self, c: Contract) -> float | None:
        return round(self._mid(c), 2)

    def buy(self, c: Contract, qty: int):
        return qty, round(self._mid(c) + self.slip, 2), self.fee * qty

    def sell(self, c: Contract, qty: int):
        return round(max(0.0, self._mid(c) - self.slip), 2), self.fee * qty


class AlpacaBroker:
    """Real Alpaca account. paper=True -> paper-api.alpaca.markets (fake money)."""

    def __init__(self, key: str, secret: str, paper: bool = True):
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        self.paper = paper
        self.name = "Alpaca PAPER" if paper else "Alpaca LIVE"
        self.trading = TradingClient(key, secret, paper=paper)
        self.stocks = StockHistoricalDataClient(key, secret)
        self.options = OptionHistoricalDataClient(key, secret)
        self.fee = 0.0

    # ── account / market ────────────────────────────────────
    def account_summary(self) -> dict:
        a = self.trading.get_account()
        return {
            "equity": float(a.equity),
            "buying_power": float(a.buying_power),
            "options_level": getattr(a, "options_trading_level", None),
            "status": str(a.status),
        }

    def market_open(self) -> bool:
        return bool(self.trading.get_clock().is_open)

    def get_bars(self, symbol: str, start: datetime, end: datetime | None = None, feed: str = "iex") -> list[dict]:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                               start=start, end=end, feed=DataFeed(feed), limit=10000)
        data = self.stocks.get_stock_bars(req).data.get(symbol, [])
        return [{"t": b.timestamp.astimezone(ET), "o": b.open, "h": b.high, "l": b.low,
                 "c": b.close, "v": b.volume} for b in data]

    # ── options ─────────────────────────────────────────────
    def find_contract(self, symbol, kind, now, cfg, spot: float | None = None) -> Contract | None:
        from alpaca.trading.enums import AssetStatus, ContractType
        from alpaca.trading.requests import GetOptionContractsRequest

        if spot is None:
            raise ValueError("spot price required")
        today = now.date()
        req = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            type=ContractType.CALL if kind == "call" else ContractType.PUT,
            status=AssetStatus.ACTIVE,
            expiration_date_gte=today + timedelta(days=int(cfg.get("min_days_to_expiry", 1))),
            expiration_date_lte=today + timedelta(days=int(cfg.get("max_days_to_expiry", 7))),
            strike_price_gte=str(round(spot * 0.97, 2)),
            strike_price_lte=str(round(spot * 1.03, 2)),
            limit=1000,
        )
        res = self.trading.get_option_contracts(req)
        contracts = [c for c in (res.option_contracts or []) if c.tradable]
        if not contracts:
            return None
        first_exp = min(c.expiration_date for c in contracts)
        same_exp = sorted({float(c.strike_price) for c in contracts if c.expiration_date == first_exp})
        atm_i = min(range(len(same_exp)), key=lambda i: abs(same_exp[i] - spot))
        off = int(cfg.get("strike_offset", 0))
        i = atm_i + off if kind == "call" else atm_i - off
        i = max(0, min(len(same_exp) - 1, i))
        strike = same_exp[i]
        match = next(c for c in contracts if c.expiration_date == first_exp and float(c.strike_price) == strike)
        return Contract(match.symbol, symbol, kind, strike, first_exp)

    def _bid_ask(self, c: Contract):
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.requests import OptionLatestQuoteRequest

        q = self.options.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=c.symbol, feed=OptionsFeed.INDICATIVE))[c.symbol]
        return float(q.bid_price or 0), float(q.ask_price or 0)

    def quote(self, c: Contract) -> float | None:
        bid, ask = self._bid_ask(c)
        if bid <= 0 or ask <= 0:
            return None
        return round((bid + ask) / 2, 2)

    def _wait_fill(self, order_id, seconds: float = 20):
        deadline = time.time() + seconds
        while time.time() < deadline:
            o = self.trading.get_order_by_id(order_id)
            status = str(o.status).split(".")[-1].lower()
            if status == "filled":
                return o
            if status in ("canceled", "expired", "rejected"):
                return o
            time.sleep(1)
        return self.trading.get_order_by_id(order_id)

    def buy(self, c: Contract, qty: int):
        """Limit order a bit above the middle of the bid/ask; if it doesn't fill in 20s,
        try once more at the ask (only if the spread is reasonable). Cancels anything unfilled.
        Returns (filled_qty, avg_price, fees) or (0, None, 0)."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        def attempt(limit: float, wait: float, want: int):
            o = self.trading.submit_order(LimitOrderRequest(
                symbol=c.symbol, qty=want, side=OrderSide.BUY, time_in_force=TimeInForce.DAY, limit_price=limit))
            o = self._wait_fill(o.id, wait)
            if str(o.status).split(".")[-1].lower() != "filled":
                try:
                    self.trading.cancel_order_by_id(o.id)
                except Exception:
                    pass
                time.sleep(1)
                o = self.trading.get_order_by_id(o.id)
            n = int(float(o.filled_qty or 0))
            return n, (float(o.filled_avg_price) if n else None)

        bid, ask = self._bid_ask(c)
        if bid <= 0 or ask <= 0:
            return 0, None, 0.0
        mid = (bid + ask) / 2
        n1, p1 = attempt(round(min(ask, mid + 0.25 * (ask - bid)), 2), 20, qty)
        if n1 >= qty:
            return n1, p1, 0.0
        bid, ask = self._bid_ask(c)
        n2, p2 = 0, None
        if bid > 0 and ask > 0 and (ask - bid) / ((ask + bid) / 2) <= 0.15:
            n2, p2 = attempt(round(ask, 2), 10, qty - n1)
        total = n1 + n2
        if total == 0:
            return 0, None, 0.0
        avg = ((p1 or 0) * n1 + (p2 or 0) * n2) / total
        return total, round(avg, 4), 0.0

    def sell(self, c: Contract, qty: int):
        """Market sell to close. Returns (avg_price, fees)."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        o = self.trading.submit_order(MarketOrderRequest(
            symbol=c.symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY))
        o = self._wait_fill(o.id, 30)
        if o.filled_avg_price:
            return float(o.filled_avg_price), 0.0
        bid, _ = self._bid_ask(c)
        return bid, 0.0

    def position_qty(self, occ: str):
        """Contracts held, 0 if Alpaca says there's no such position, None if we couldn't ask (network)."""
        try:
            return int(float(self.trading.get_open_position(occ).qty))
        except Exception as e:
            msg = str(e).lower()
            if "position does not exist" in msg or "not found" in msg or "40410000" in msg:
                return 0
            return None

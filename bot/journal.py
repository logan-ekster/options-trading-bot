"""Trade journal - every trade is saved to data/trades_<mode>.db (SQLite)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT, worker TEXT, underlying TEXT, contract TEXT, kind TEXT,
    strike REAL, expiry TEXT, qty INTEGER,
    entry_time TEXT, entry_price REAL, entry_reason TEXT,
    exit_time TEXT, exit_price REAL, exit_reason TEXT,
    fees REAL DEFAULT 0, pnl REAL, status TEXT DEFAULT 'open'
);
"""


class Journal:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def open_trade(self, **t) -> int:
        cols = ",".join(t)
        cur = self.db.execute(f"INSERT INTO trades ({cols}) VALUES ({','.join('?' * len(t))})", list(t.values()))
        self.db.commit()
        return cur.lastrowid

    def close_trade(self, trade_id: int, exit_time, exit_price, exit_reason, fees, pnl):
        self.db.execute(
            "UPDATE trades SET exit_time=?, exit_price=?, exit_reason=?, fees=?, pnl=?, status='closed' WHERE id=?",
            (exit_time, exit_price, exit_reason, fees, pnl, trade_id))
        self.db.commit()

    def open_trades(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM trades WHERE status='open'")]

    def day_pnl(self, day: str, worker: str | None = None) -> float:
        q, args = "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='closed' AND day=?", [day]
        if worker:
            q += " AND worker=?"
            args.append(worker)
        return float(self.db.execute(q, args).fetchone()[0])

    def count_trades(self, day: str, worker: str) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM trades WHERE day=? AND worker=?", (day, worker)).fetchone()[0])

    def daily_history(self, limit: int = 60):
        rows = self.db.execute(
            "SELECT day, SUM(pnl) pnl, COUNT(*) n, SUM(pnl>0) wins FROM trades WHERE status='closed' "
            "GROUP BY day ORDER BY day DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows][::-1]

    def worker_totals(self):
        rows = self.db.execute(
            "SELECT worker, SUM(pnl) pnl, COUNT(*) n, SUM(pnl>0) wins FROM trades WHERE status='closed' GROUP BY worker")
        return {r["worker"]: dict(r) for r in rows}

    def recent(self, limit: int = 40):
        rows = self.db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def stats(self):
        r = self.db.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(pnl),0) total, SUM(pnl>0) wins, "
            "COALESCE(AVG(CASE WHEN pnl>0 THEN pnl END),0) avg_win, "
            "COALESCE(AVG(CASE WHEN pnl<=0 THEN pnl END),0) avg_loss, "
            "COALESCE(MIN(pnl),0) worst FROM trades WHERE status='closed'").fetchone()
        return dict(r)

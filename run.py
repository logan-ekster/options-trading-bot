#!/usr/bin/env python3
"""
Options Bot City - start here.

    python run.py check              test your Alpaca keys and account
    python run.py demo               fake market, no keys needed - watch the dashboard work
    python run.py backtest --days 20 replay the last 20 trading days of REAL prices (estimated option prices)
    python run.py compare            test the ideas in experiments.yaml side by side on the same history
    python run.py paper              trade on your Alpaca PAPER account (fake money) - the main mode
    python run.py report             print stats from your paper trades
    python run.py live               real money (only after weeks of paper results) - asks you to confirm

Dashboard: open http://localhost:8050 while it runs.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from bot import dashboard_server
from bot.broker import ET, AlpacaBroker, SimBroker
from bot.engine import Engine
from bot.journal import Journal
from bot.simdata import synthetic_day, weekdays_back

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


# ── helpers ─────────────────────────────────────────────────
def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def keys(live: bool):
    load_env()
    prefix = "ALPACA_LIVE_" if live else "ALPACA_PAPER_"
    k, s = os.environ.get(prefix + "KEY"), os.environ.get(prefix + "SECRET")
    if not k or not s or "PASTE" in k:
        sys.exit(f"\n✖ Missing {prefix}KEY / {prefix}SECRET in the .env file. See SETUP step 4.\n")
    return k, s


def ensure_single_instance(cfg):
    """Stop a second copy from trading at the same time (e.g. auto-start + manual start)."""
    import socket
    port = int(cfg.get("dashboard_port", 8050))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        if sk.connect_ex(("127.0.0.1", port)) == 0:
            sys.exit(f"\n✖ The bot (or another copy) is already running - its dashboard is on port {port}.\n"
                     f"  Open http://localhost:{port} to watch it. If it's stuck, close the other Terminal window first.\n")


def start_dashboard(cfg, state_file: Path, open_browser=True):
    url = dashboard_server.start(state_file, int(cfg.get("dashboard_port", 8050)))
    print(f"\n  Dashboard → {url}\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return url


def regular_hours(bars):
    return [b for b in bars if (9, 30) <= (b["t"].hour, b["t"].minute) <= (15, 59)]


# ── replay (demo + backtest share this) ─────────────────────
def replay(engine: Engine, broker: SimBroker, days: list, warmup: dict, delay: float, label: str):
    history = {s: list(v) for s, v in warmup.items()}
    for d, bars_by_sym in days:
        engine.new_day(d)
        stamps = sorted({b["t"] for bars in bars_by_sym.values() for b in bars})
        idx = {s: {b["t"]: b for b in bars} for s, bars in bars_by_sym.items()}
        now = None
        for i, ts in enumerate(stamps):
            now = ts + timedelta(minutes=1)
            for s in idx:
                b = idx[s].get(ts)
                if b:
                    history.setdefault(s, []).append(b)
                    broker.set_market(s, b["c"], now)
            broker.now = now
            engine.on_tick(now)
            engine.on_bars({s: h[-500:] for s, h in history.items()}, now)
            if delay and i % 2 == 0:
                engine.write_state({"replay": label})
                time.sleep(delay)
        if now:
            engine.flatten_all(now, "end of day flatten")
        engine.write_state({"replay": label})
        print(f"  {d}  day P&L {engine.day_total():+9.2f}   trades {sum(w.trades_today for w in engine.workers)}")
        for s in history:
            history[s] = history[s][-500:]


def print_report(journal: Journal, title: str):
    st = journal.stats()
    hist = journal.daily_history(1000)
    n = st["n"] or 0
    print(f"\n══ {title} ══")
    if not n:
        print("  no closed trades yet")
        return
    green = sum(1 for h in hist if h["pnl"] > 0)
    print(f"  trades: {n}   win rate: {100 * (st['wins'] or 0) / n:.0f}%")
    print(f"  total P&L: ${st['total']:,.2f}   avg win: ${st['avg_win']:,.2f}   avg loss: ${st['avg_loss']:,.2f}")
    print(f"  green days: {green}/{len(hist)}   worst trade: ${st['worst']:,.2f}")
    worst_day = min(hist, key=lambda h: h["pnl"])
    best_day = max(hist, key=lambda h: h["pnl"])
    print(f"  best day: {best_day['day']} ${best_day['pnl']:,.2f}   worst day: {worst_day['day']} ${worst_day['pnl']:,.2f}")
    for name, t in journal.worker_totals().items():
        print(f"    {name:<14} ${t['pnl']:>9,.2f}  ({t['n']} trades, {100 * t['wins'] / t['n']:.0f}% wins)")


# ── commands ────────────────────────────────────────────────
def cmd_demo(cfg, args):
    if not args.no_dashboard:
        ensure_single_instance(cfg)
    for f in DATA.glob("trades_demo.db*"):
        f.unlink()
    journal = Journal(DATA / "trades_demo.db")
    broker = SimBroker(cfg.get("simulation", {}))
    state = DATA / "state.json"
    engine = Engine(cfg, broker, journal, state, mode_label="DEMO · FAKE MARKET")
    if not args.no_dashboard:
        start_dashboard(cfg, state, not args.no_browser)
    syms = sorted({w.symbol for w in engine.workers})
    last = {}
    dates = weekdays_back(args.days + 1)
    warm = synthetic_day(syms, dates[0], last, seed=args.seed)
    days = [(d, synthetic_day(syms, d, last, seed=(args.seed or 0) + i + 1)) for i, d in enumerate(dates[1:])]
    print(f"Demo: {args.days} made-up trading days. Ctrl+C to stop.")
    replay(engine, broker, days, warm, args.delay, "demo")
    print_report(journal, "DEMO RESULTS (fake market - means nothing about real profits)")
    if not args.no_dashboard:
        print("\nDashboard still up - press Ctrl+C to quit.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def load_history(cfg, n_days: int):
    """Download real 1-minute bars once. Returns (days, warmup) ready for replay()."""
    k, s = keys(live=False)
    alp = AlpacaBroker(k, s, paper=True)
    syms = sorted({w["symbol"].upper() for w in cfg["workers"]})
    end = datetime.now(ET) - timedelta(minutes=20)
    start = end - timedelta(days=int(n_days * 1.6) + 6)
    print(f"Downloading 1-minute bars for {', '.join(syms)} ...")
    raw = {}
    for sym in syms:
        try:
            raw[sym] = regular_hours(alp.get_bars(sym, start, end, feed="sip"))
        except Exception:
            raw[sym] = regular_hours(alp.get_bars(sym, start, end, feed="iex"))
        print(f"  {sym}: {len(raw[sym])} bars")
    all_days = sorted({b["t"].date() for bars in raw.values() for b in bars})
    if len(all_days) < 2:
        sys.exit("Not enough data returned.")
    test_days = all_days[-n_days:] if len(all_days) > n_days else all_days[1:]
    warm_day = all_days[all_days.index(test_days[0]) - 1]
    warmup = {s: [b for b in raw[s] if b["t"].date() == warm_day] for s in syms}
    days = [(d, {s: [b for b in raw[s] if b["t"].date() == d] for s in syms}) for d in test_days]
    return days, warmup


def cmd_backtest(cfg, args):
    days, warmup = load_history(cfg, args.days)
    for f in DATA.glob("trades_backtest.db*"):
        f.unlink()
    journal = Journal(DATA / "trades_backtest.db")
    sim = SimBroker(cfg.get("simulation", {}))
    engine = Engine(cfg, sim, journal, DATA / "state.json", mode_label="BACKTEST · ESTIMATED")
    if args.dashboard:
        ensure_single_instance(cfg)
        start_dashboard(cfg, DATA / "state.json")
    print(f"\nReplaying {len(days)} days ({days[0][0]} → {days[-1][0]}) ...")
    replay(engine, sim, days, warmup, 0.02 if args.dashboard else 0, "backtest")
    print_report(journal, "BACKTEST (real stock prices, ESTIMATED option prices)")
    print("\n  Option prices here are estimated with a formula (Black-Scholes), not real fills.")
    print("  Treat this as a rough sanity check. Paper trading is the real test.")


# ── compare: many variants on the same history ──────────────
def apply_variant(base: dict, variant: dict) -> dict:
    """Copy the config and apply one experiment: `set` (dotted paths), `disable` (bot names),
    `worker_set` (per-bot overrides for every bot)."""
    import copy
    cfg = copy.deepcopy(base)
    for path, value in (variant.get("set") or {}).items():
        node = cfg
        keys_ = path.split(".")
        for k in keys_[:-1]:
            node = node.setdefault(k, {})
        node[keys_[-1]] = value
    off = {n.upper() for n in (variant.get("disable") or [])}
    if off:
        cfg["workers"] = [w for w in cfg["workers"] if w["name"].upper() not in off]
    for k, v in (variant.get("worker_set") or {}).items():
        for w in cfg["workers"]:
            w[k] = v
    for name, overrides in (variant.get("per_worker") or {}).items():
        for w in cfg["workers"]:
            if w["name"].upper() == str(name).upper():
                w.update(overrides)
    if not cfg["workers"]:
        raise ValueError(f"variant '{variant.get('name')}' disables every bot")
    return cfg


def run_variant(cfg, days, warmup) -> dict:
    journal = Journal(":memory:")
    sim = SimBroker(cfg.get("simulation", {}))
    engine = Engine(cfg, sim, journal, None, mode_label="COMPARE")
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        replay(engine, sim, days, warmup, 0, "compare")
    st = journal.stats()
    hist = journal.daily_history(10000)
    pnls = [h["pnl"] for h in hist]
    equity = peak = dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    n = st["n"] or 0
    return {
        "total": round(st["total"], 2), "trades": n,
        "win_rate": round(100 * (st["wins"] or 0) / n, 1) if n else 0.0,
        "green_days": sum(1 for p in pnls if p > 0), "days": len(days),
        "worst_day": round(min(pnls), 2) if pnls else 0.0,
        "avg_win": round(st["avg_win"], 2), "avg_loss": round(st["avg_loss"], 2),
        "max_drawdown": round(dd, 2),
        "by_bot": {k: round(v["pnl"], 2) for k, v in journal.worker_totals().items()},
    }


def cmd_compare(cfg, args):
    exp_file = ROOT / args.file
    if not exp_file.exists():
        sys.exit(f"Can't find {args.file}")
    variants = yaml.safe_load(exp_file.read_text()).get("experiments") or []
    variants = [{"name": "baseline (your config.yaml)"}] + [v for v in variants if v.get("name")]
    if args.only:
        wanted = {x.strip().lower() for x in args.only.split(",")}
        variants = [variants[0]] + [v for v in variants[1:] if v["name"].lower() in wanted]
    days, warmup = load_history(cfg, args.days)
    print(f"\nTesting {len(variants)} variants on the same {len(days)} days "
          f"({days[0][0]} → {days[-1][0]}) ...\n")
    rows = []
    for v in variants:
        try:
            r = run_variant(apply_variant(cfg, v), days, warmup)
        except Exception as e:
            print(f"  ✖ {v['name']}: {e}")
            continue
        r["name"] = v["name"]
        rows.append(r)
        print(f"  ✔ {v['name']}")

    base = rows[0]
    hdr = f"{'variant':<34}{'total':>10}{'vs base':>10}{'trades':>8}{'win%':>7}{'green':>8}{'worst day':>11}{'max DD':>10}"
    print("\n" + hdr + "\n" + "─" * len(hdr))
    for r in rows:
        diff = r["total"] - base["total"]
        print(f"{r['name'][:33]:<34}{r['total']:>10,.0f}{diff:>+10,.0f}{r['trades']:>8}{r['win_rate']:>7.0f}"
              f"{str(r['green_days']) + '/' + str(r['days']):>8}{r['worst_day']:>11,.0f}{r['max_drawdown']:>10,.0f}")
    print("\n  total/worst day/max DD in $. max DD = biggest drop from a high point.")
    print("  A variant only 'wins' if it beats baseline on total AND isn't worse on worst day / max DD.")
    print("  Option prices are estimated - use this to pick what to watch, not as proof.")

    out = DATA / "compare_results.csv"
    import csv
    with out.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["run_date", "days", "variant", "total", "vs_base", "trades", "win_rate", "green_days",
                     "worst_day", "max_drawdown", "avg_win", "avg_loss"])
        for r in rows:
            wr.writerow([datetime.now(ET).date(), r["days"], r["name"], r["total"], round(r["total"] - base["total"], 2),
                         r["trades"], r["win_rate"], r["green_days"], r["worst_day"], r["max_drawdown"],
                         r["avg_win"], r["avg_loss"]])
    print(f"\n  Saved to {out.relative_to(ROOT)}")


def cmd_check(cfg, args):
    live = args.live
    k, s = keys(live)
    b = AlpacaBroker(k, s, paper=not live)
    a = b.account_summary()
    print(f"\n✔ Connected to {b.name}")
    print(f"  equity ${a['equity']:,.2f}   buying power ${a['buying_power']:,.2f}   status {a['status']}")
    print(f"  options trading level: {a['options_level']}  (need 2+ to buy calls/puts)")
    print(f"  market open right now: {b.market_open()}")
    bars = b.get_bars("QQQ", datetime.now(ET) - timedelta(days=5))
    print(f"  QQQ 1-min bars received: {len(bars)}  (last {bars[-1]['t']:%a %H:%M} @ {bars[-1]['c']})" if bars else "  no QQQ bars?")
    c = b.find_contract("QQQ", "call", datetime.now(ET), cfg["contracts"], spot=bars[-1]["c"] if bars else 500)
    if c:
        print(f"  sample contract: {c.symbol} (strike {c.strike}, expires {c.expiry})  mid quote: {b.quote(c)}")
    print("\nAll good.\n")


def cmd_run(cfg, args, live: bool):
    if live:
        if cfg.get("mode") != "live":
            sys.exit("✖ config.yaml still says `mode: paper`. Change it to `mode: live` only if you're sure.")
        print("\n⚠  LIVE MODE - REAL MONEY. Options can lose 100% of what you put in, fast.")
        print(f"   Max loss per day is set to ${cfg['risk']['max_daily_loss_total']}.")
        if input("   Type  I ACCEPT THE RISK  to continue: ").strip() != "I ACCEPT THE RISK":
            sys.exit("Cancelled.")
    ensure_single_instance(cfg)
    k, s = keys(live)
    broker = AlpacaBroker(k, s, paper=not live)
    journal = Journal(DATA / ("trades_live.db" if live else "trades_paper.db"))
    state = DATA / "state.json"
    engine = Engine(cfg, broker, journal, state, mode_label="LIVE · REAL MONEY" if live else "PAPER · FAKE MONEY")
    for w in engine.workers:
        w.echo = True
    engine.recover_open_trades()
    start_dashboard(cfg, state, not args.no_browser)
    syms = sorted({w.symbol for w in engine.workers})
    history = {sym: [] for sym in syms}
    last_minute = None
    clock_checked, is_open = 0.0, False
    print(f"Running on {broker.name}. Leave this window open. Ctrl+C stops (and sells anything open).\n")
    last_loop = datetime.now(ET)
    try:
        while True:
            now = datetime.now(ET)
            gap = (now - last_loop).total_seconds()
            if gap > 120:
                print(f"[{now:%H:%M}] ⚠ the bot was paused for {gap / 60:.0f} min - the Mac was probably asleep. "
                      f"Keep it plugged in with the lid open.", flush=True)
            last_loop = now
            try:
                if time.time() - clock_checked > 60:
                    is_open, clock_checked = broker.market_open(), time.time()
                if not is_open:
                    if getattr(args, "exit_after_close", False) and now.time() >= datetime.strptime("16:10", "%H:%M").time():
                        print(f"[{now:%H:%M}] market closed for the day - auto-start run finished.")
                        engine.write_state()
                        break
                    for w in engine.workers:
                        if not w.position:
                            w.status = "market closed · sleeping"
                    engine.write_state()
                    time.sleep(30)
                    continue
                minute = now.replace(second=0, microsecond=0)
                if minute != last_minute and now.second >= 3:
                    for sym in syms:
                        h = history[sym]
                        since = h[-1]["t"] + timedelta(minutes=1) if h else now - timedelta(days=5)
                        new = [b for b in regular_hours(broker.get_bars(sym, since))
                               if b["t"] + timedelta(minutes=1) <= now and (not h or b["t"] > h[-1]["t"])]
                        history[sym] = (h + new)[-500:]
                    engine.on_bars(history, now)
                    last_minute = minute
                engine.on_tick(now)
                engine.write_state()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"[{now:%H:%M:%S}] error (will retry): {e}")
            time.sleep(15)
    except KeyboardInterrupt:
        print("\nStopping - closing any open bot positions ...")
        engine.flatten_all(datetime.now(ET), "bot stopped")
        engine.write_state()
        print("Done.")


def cmd_report(cfg, args):
    name = {"paper": "trades_paper.db", "live": "trades_live.db", "backtest": "trades_backtest.db"}[args.which]
    if not (DATA / name).exists():
        sys.exit(f"No {args.which} trades yet.")
    print_report(Journal(DATA / name), f"{args.which.upper()} RESULTS")


def main():
    p = argparse.ArgumentParser(description="Options Bot City")
    sub = p.add_subparsers(dest="cmd")
    d = sub.add_parser("demo"); d.add_argument("--days", type=int, default=5)
    d.add_argument("--delay", type=float, default=0.03); d.add_argument("--seed", type=int, default=None)
    d.add_argument("--no-dashboard", action="store_true"); d.add_argument("--no-browser", action="store_true")
    cp = sub.add_parser("compare"); cp.add_argument("--days", type=int, default=30)
    cp.add_argument("--file", default="experiments.yaml"); cp.add_argument("--only", default=None)
    b = sub.add_parser("backtest"); b.add_argument("--days", type=int, default=20)
    b.add_argument("--dashboard", action="store_true")
    c = sub.add_parser("check"); c.add_argument("--live", action="store_true")
    for name in ("paper", "live"):
        x = sub.add_parser(name); x.add_argument("--no-browser", action="store_true")
        x.add_argument("--exit-after-close", action="store_true", help="quit after 4:10 PM ET (used by auto-start)")
    r = sub.add_parser("report"); r.add_argument("which", nargs="?", default="paper", choices=["paper", "live", "backtest"])
    args = p.parse_args()
    cfg = load_cfg()
    DATA.mkdir(exist_ok=True)
    if args.cmd == "demo":
        cmd_demo(cfg, args)
    elif args.cmd == "backtest":
        cmd_backtest(cfg, args)
    elif args.cmd == "compare":
        cmd_compare(cfg, args)
    elif args.cmd == "check":
        cmd_check(cfg, args)
    elif args.cmd == "paper":
        cmd_run(cfg, args, live=False)
    elif args.cmd == "live":
        cmd_run(cfg, args, live=True)
    elif args.cmd == "report":
        cmd_report(cfg, args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()

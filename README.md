# Options Bot City: Automated Options Day-Trading Bot

A Python bot that runs several intraday options strategies side by side, manages risk automatically, and shows everything on a live 3D dashboard. Built and tested on an Alpaca **paper** (simulated money) account.

> Educational project. Not financial advice. Paper trade only.

## What it does

- **Multiple strategy "workers"** run at once, each with its own trigger: EMA cross, VWAP cross, opening-range breakout (ORB), trend, buy-the-dip, and a combo signal.
- **Technical indicators** written from scratch: EMA, ATR, and session VWAP on 1-minute bars.
- **Contract selection** picks call or put contracts by days-to-expiry and strike offset, with OCC symbol generation.
- **Account-wide risk controls**: max daily loss, daily profit target, per-trade dollar cap, max open trades, a trading window, and an end-of-day flatten.
- **Exit engine**: stop loss, trailing stop, take profit, and a max time in trade.
- **Backtesting** replays real historical prices, with option prices estimated by a Black-Scholes model.
- **Experiment runner** (`compare`) tests config variations side by side on the same history.
- **Trade journal**: a SQLite log of every trade, plus a `report` command for stats.
- **Live dashboard** at `localhost:8050`, built with Three.js (3D "city" view, one tower per bot), plus a classic view.

## Modes

```
python run.py check              # verify Alpaca keys and account
python run.py demo               # synthetic market, no keys needed
python run.py backtest --days 20 # replay recent real price history
python run.py compare            # A/B test the ideas in experiments.yaml
python run.py paper              # trade the Alpaca PAPER account
python run.py report             # stats from paper trades
```

## Project structure

```
run.py              CLI entry point and mode runner
bot/engine.py       Trading loop, positions, exits, risk limits
bot/strategies.py   Entry signals (EMA, VWAP, ORB, trend, dip, combo)
bot/indicators.py   EMA, ATR, VWAP
bot/broker.py       Alpaca broker + simulated broker, Black-Scholes pricing
bot/journal.py      SQLite trade journal
bot/simdata.py      Synthetic market data for demo mode
dashboard/          Three.js live dashboard
config.yaml         All strategy and risk settings
experiments.yaml    Variations for the compare runner
```

## Setup

```
pip install -r requirements.txt
cp .env.example .env    # add your Alpaca PAPER keys
python run.py demo
```

## Results

I ran it on paper for a month and logged the results. They didn't justify risking real money, so I stopped there. The win was building a testable system and making that call from data.

## Built with

Python, alpaca-py, PyYAML, SQLite, Three.js. Developed with AI pair programming (Claude).

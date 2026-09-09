# Apex Multi-Asset Trader

A single-owner paper-trading web app for Bitcoin, EUR/USD, GBP/USD, gold and oil. All quotes are simulated. No exchange credentials, broker connection or real-money execution is implemented.

## Working features
- FastAPI dashboard with simulated market quotes and a 24-hour equity chart.
- $10,000 paper account, long-only holdings, partial/full sells, realised/unrealised P&L and order history.
- Persisted SQLite account, quotes, positions, settings and daily risk state.
- Entry checks against cash, cumulative position exposure and total position risk to a stop. Default stop: 2% below entry. Additional buys retain the stop.
- Stops fill at the next simulated quote, which can differ from the stop price.
- UTC daily equity-loss circuit breaker, latched until the next UTC day. It blocks manual and automatic buys; sells remain available.
- Optional demo mean-reversion strategy for Bitcoin and gold, disabled by default. This is not backtested or a profitability claim.
- Password sign-in, 12-hour HttpOnly session cookie, request-header CSRF checks, and login attempt throttling.

## Local setup
Python 3.12 recommended.

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000. Local loopback use can omit APP_PASSWORD. Never expose an unprotected local instance on a public interface.

## Railway deployment
Use the included Dockerfile and railway.toml, one service instance and one Uvicorn worker. The simulation and account transaction lock belong to a single process; do not scale replicas or workers.

Set APP_PASSWORD to a random password of at least 16 characters, APEX_PRODUCTION=1, and TRADING_DB=/data/trading.db. Attach a persistent Railway volume at /data before launch. Set PORT=8000 and route the public domain to 8000. The production app refuses to start without its password. /health remains public; account APIs require authentication. Keep secrets in Railway variables, never in Git.

Back up the SQLite volume before upgrades. Stopping the service and copying the database is the simplest consistent offline backup; use SQLite's backup API for an online backup. Health checks return 503 when the simulation thread is stale. Rotate APP_PASSWORD to invalidate all sessions.

## Simulation limits
Prices are generated around historic demo seed values and are not current market prices. Fills omit spreads, fees, slippage models and financing. There is no leverage, shorting or currency conversion: each symbol is represented as unlevered USD-valued units. Decimal quantities and cash use floating point and are suitable for this simulation, not broker-grade settlement. Daily limits cannot guarantee a maximum loss during quote gaps. A stopped server does not simulate missed quotes.

The original Freqtrade/MT5 examples are preserved under legacy/ for reference only. They are not connected to the web app and are not validated live adapters; legacy/config.json is a commented draft, not executable JSON. The current service is not a Freqtrade fork. A future real-market release needs separately tested broker adapters, real data, contract specifications, accounting, authentication and execution reconciliation.

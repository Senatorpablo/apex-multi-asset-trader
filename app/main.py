"""Single-owner paper-trading service. No broker credentials or live execution."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import random
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv('TRADING_DB', str(BASE_DIR / 'data' / 'trading.db')))
APP_PASSWORD = os.getenv('APP_PASSWORD', '')
PRODUCTION = bool(os.getenv('RAILWAY_ENVIRONMENT_ID')) or os.getenv('APEX_PRODUCTION') == '1'
ASSETS = {
    'BTCUSD': {'name': 'Bitcoin', 'asset_class': 'crypto', 'price': 64000., 'vol': .0025},
    'EURUSD': {'name': 'EUR / USD', 'asset_class': 'forex', 'price': 1.085, 'vol': .00035},
    'GBPUSD': {'name': 'GBP / USD', 'asset_class': 'forex', 'price': 1.275, 'vol': .0004},
    'XAUUSD': {'name': 'Gold', 'asset_class': 'commodity', 'price': 2350., 'vol': .0012},
    'WTIUSD': {'name': 'WTI Crude Oil', 'asset_class': 'commodity', 'price': 78., 'vol': .0018},
}
LOCK = threading.RLock()
STOP = threading.Event()
ENGINE_THREAD = None
LAST_TICK = 0.
LOGIN_FAILURES = []
DEFAULTS = {'risk_per_trade': .005, 'max_position_pct': .20,
            'daily_loss_limit_pct': .03, 'auto_trade': False}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''
        CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1),
          starting_balance REAL NOT NULL, cash REAL NOT NULL,
          realised_pnl REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS positions(symbol TEXT PRIMARY KEY, qty REAL NOT NULL,
          avg_price REAL NOT NULL, updated_at TEXT NOT NULL, stop_loss REAL);
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
          symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL,
          notional REAL NOT NULL, realised_pnl REAL NOT NULL DEFAULT 0, source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS prices(symbol TEXT PRIMARY KEY,price REAL NOT NULL,ts TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS daily_risk(day TEXT PRIMARY KEY, opening_equity REAL NOT NULL,
          halted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS equity_history(ts TEXT PRIMARY KEY,equity REAL NOT NULL);
        ''')
        if 'stop_loss' not in [r['name'] for r in con.execute('PRAGMA table_info(positions)')]:
            con.execute('ALTER TABLE positions ADD COLUMN stop_loss REAL')
            con.execute('UPDATE positions SET stop_loss=avg_price*.98')
        con.execute('INSERT OR IGNORE INTO account VALUES(1,10000,10000,0,?)', (now_iso(),))
        for k, v in DEFAULTS.items():
            con.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (k,json.dumps(v)))
        for s, spec in ASSETS.items():
            con.execute('INSERT OR IGNORE INTO prices VALUES(?,?,?)', (s,spec['price'],now_iso()))


def settings_from(con):
    return {r['key']: json.loads(r['value']) for r in con.execute('SELECT * FROM settings')}


def positions_from(con):
    return [dict(r) for r in con.execute('''SELECT p.*, m.price AS mark_price,
        (m.price-p.avg_price)*p.qty AS unrealised_pnl, m.price*p.qty AS market_value
        FROM positions p JOIN prices m USING(symbol) ORDER BY symbol''')]


def snapshot_from(con):
    a = dict(con.execute('SELECT * FROM account WHERE id=1').fetchone())
    ps = positions_from(con)
    value = sum(p['market_value'] for p in ps)
    return {**a, 'equity': a['cash']+value, 'positions_value': value,
            'unrealised_pnl': sum(p['unrealised_pnl'] for p in ps)}


def daily_risk(con, equity):
    day = now_iso()[:10]
    con.execute('INSERT OR IGNORE INTO daily_risk VALUES(?,?,0)', (day,equity))
    r = dict(con.execute('SELECT * FROM daily_risk WHERE day=?', (day,)).fetchone())
    loss = settings_from(con)['daily_loss_limit_pct']
    if equity <= r['opening_equity'] * (1-loss):
        con.execute('UPDATE daily_risk SET halted=1 WHERE day=?', (day,))
        r['halted'] = 1
    return {**r, 'loss_pct': max(0,1-equity/r['opening_equity']) if r['opening_equity'] > 0 else 1}


def account_snapshot():
    with LOCK, db() as con:
        a = snapshot_from(con)
        return {**a, 'daily_risk': daily_risk(con,a['equity'])}


def execute_paper_trade(symbol, side, qty, source='manual', stop_loss=None):
    symbol = symbol.upper()
    if symbol not in ASSETS or side not in ('buy','sell'):
        raise HTTPException(400,'Unsupported symbol or side')
    if not math.isfinite(qty) or qty <= 0:
        raise HTTPException(400,'Quantity must be a finite positive number')
    # One serialized transaction for validation and fill, shared by manual and auto orders.
    with LOCK:
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            a = snapshot_from(con)
            risk = daily_risk(con,a['equity'])
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            a = snapshot_from(con)
            s = settings_from(con)
            price = con.execute('SELECT price FROM prices WHERE symbol=?',(symbol,)).fetchone()[0]
            pos = con.execute('SELECT * FROM positions WHERE symbol=?',(symbol,)).fetchone()
            old_qty, old_avg = (pos['qty'],pos['avg_price']) if pos else (0.,0.)
            notional = price*qty
            if not math.isfinite(notional) or notional >= 1e12:
                raise HTTPException(400,'Order is too large')
            realised = 0.
            if side == 'buy':
                if risk['halted']:
                    raise HTTPException(400,'Daily loss limit reached. New buys paused until the next UTC day; sells remain available.')
                if notional > a['cash']:
                    raise HTTPException(400,'Insufficient paper cash')
                if (old_qty+qty)*price > a['equity']*s['max_position_pct']+1e-8:
                    raise HTTPException(400,'Total position would exceed the position cap')
                stop = stop_loss if stop_loss is not None else (pos['stop_loss'] if pos else price*.98)
                if stop is None or not math.isfinite(stop) or not 0 < stop < price:
                    raise HTTPException(400,'Buy stop must be positive and below the current price')
                if pos and abs(stop-pos['stop_loss']) > 1e-8:
                    raise HTTPException(400,'Additional buys must retain the existing position stop')
                if (price-stop)*(old_qty+qty) > a['equity']*s['risk_per_trade']+1e-8:
                    raise HTTPException(400,'Position risk to its stop exceeds the risk budget')
                new_qty = old_qty+qty
                avg = (old_qty*old_avg+notional)/new_qty
                con.execute('UPDATE account SET cash=cash-? WHERE id=1',(notional,))
                con.execute('''INSERT INTO positions VALUES(?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
                    qty=excluded.qty,avg_price=excluded.avg_price,updated_at=excluded.updated_at,
                    stop_loss=excluded.stop_loss''',(symbol,new_qty,avg,now_iso(),stop))
            else:
                # Reducing exposure must never be blocked by entry limits.
                if qty > old_qty:
                    raise HTTPException(400,'Cannot sell more than the held quantity; shorting is disabled')
                realised = (price-old_avg)*qty
                con.execute('UPDATE account SET cash=cash+?,realised_pnl=realised_pnl+? WHERE id=1',(notional,realised))
                if old_qty-qty <= 1e-12:
                    con.execute('DELETE FROM positions WHERE symbol=?',(symbol,))
                else:
                    con.execute('UPDATE positions SET qty=?,updated_at=? WHERE symbol=?',(old_qty-qty,now_iso(),symbol))
            cur = con.execute('''INSERT INTO trades(ts,symbol,side,qty,price,notional,realised_pnl,source)
                VALUES(?,?,?,?,?,?,?,?)''',(now_iso(),symbol,side,qty,price,notional,realised,source))
            result = {'id':cur.lastrowid,'symbol':symbol,'side':side,'qty':qty,'price':price,'realised_pnl':realised}
        return result


def strategy_tick():
    with LOCK, db() as con:
        ps = positions_from(con)
        s = settings_from(con)
        a = snapshot_from(con)
        daily_risk(con,a['equity'])
        prices = {r['symbol']:r['price'] for r in con.execute('SELECT * FROM prices')}
    for p in ps:
        if p['stop_loss'] is not None and p['mark_price'] <= p['stop_loss']:
            execute_paper_trade(p['symbol'],'sell',p['qty'],'stop-loss')
    if not s['auto_trade']:
        return
    held = {p['symbol']:p for p in ps}
    for symbol in ('BTCUSD','XAUUSD'):
        price = prices[symbol]
        deviation = price/ASSETS[symbol]['price']-1
        try:
            if deviation < -.01 and symbol not in held:
                qty = min(a['equity']*.02, a['cash']*.02)/price
                execute_paper_trade(symbol,'buy',qty,'auto-demo')
            elif deviation > .01 and symbol in held:
                execute_paper_trade(symbol,'sell',held[symbol]['qty'],'auto-demo')
        except HTTPException:
            pass


def market_tick():
    global LAST_TICK
    with LOCK, db() as con:
        # Capture the UTC opening balance before changing quotes.
        daily_risk(con,snapshot_from(con)['equity'])
        for row in con.execute('SELECT * FROM prices').fetchall():
            price = max(.00001,row['price']*(1+random.gauss(0,ASSETS[row['symbol']]['vol'])))
            con.execute('UPDATE prices SET price=?,ts=? WHERE symbol=?',(price,now_iso(),row['symbol']))
        ts = now_iso()[:16]
        con.execute('INSERT OR REPLACE INTO equity_history VALUES(?,?)',(ts,snapshot_from(con)['equity']))
        con.execute('DELETE FROM equity_history WHERE ts NOT IN (SELECT ts FROM equity_history ORDER BY ts DESC LIMIT 1440)')
    strategy_tick()
    LAST_TICK = time.time()


def market_loop():
    while not STOP.is_set():
        try:
            market_tick()
        except Exception:
            logging.exception('Paper engine tick failed')
        STOP.wait(2)


@asynccontextmanager
async def lifespan(app):
    global ENGINE_THREAD
    if PRODUCTION and len(APP_PASSWORD) < 16:
        raise RuntimeError('Set APP_PASSWORD to at least 16 characters for production')
    init_db()
    STOP.clear()
    if os.getenv('APEX_DISABLE_ENGINE') != '1':
        ENGINE_THREAD = threading.Thread(target=market_loop,daemon=True)
        ENGINE_THREAD.start()
    yield
    STOP.set()
    if ENGINE_THREAD:
        ENGINE_THREAD.join(timeout=10)


app = FastAPI(title='Apex Multi-Asset Trader',version='1.1.0',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=BASE_DIR/'app'/'static'),name='static')


def valid_session(value):
    try:
        ts, sig = value.split('.')
        expected = hmac.new(APP_PASSWORD.encode(),ts.encode(),hashlib.sha256).hexdigest()
        return 0 <= time.time()-int(ts) < 43200 and hmac.compare_digest(sig,expected)
    except (ValueError,AttributeError):
        return False


@app.middleware('http')
async def protect(request: Request, call_next):
    path = request.url.path
    if path.startswith('/api/') and path not in ('/api/login','/api/session'):
        if APP_PASSWORD and not valid_session(request.cookies.get('apex_session')):
            return JSONResponse({'detail':'Sign in to continue'},status_code=401)
    if path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):
        if request.headers.get('X-Apex-Request') != '1':
            return JSONResponse({'detail':'Missing request protection header'},status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


class LoginRequest(BaseModel):
    password: str = Field(max_length=256)


@app.post('/api/login')
def login(req: LoginRequest, response: Response):
    with LOCK:
        now = time.time()
        LOGIN_FAILURES[:] = [t for t in LOGIN_FAILURES if now-t < 300]
        if len(LOGIN_FAILURES) >= 10:
            raise HTTPException(429,'Too many attempts. Try again in five minutes.')
        if not APP_PASSWORD or not secrets.compare_digest(req.password.encode(),APP_PASSWORD.encode()):
            LOGIN_FAILURES.append(now)
            raise HTTPException(401,'Incorrect password')
        LOGIN_FAILURES.clear()
    ts = str(int(time.time()))
    sig = hmac.new(APP_PASSWORD.encode(),ts.encode(),hashlib.sha256).hexdigest()
    response.set_cookie('apex_session',ts+'.'+sig,httponly=True,secure=PRODUCTION,samesite='strict',max_age=43200)
    return {'ok':True}


@app.get('/api/session')
def session(request: Request):
    return {'authenticated':not APP_PASSWORD or valid_session(request.cookies.get('apex_session')),
            'password_required':bool(APP_PASSWORD)}


@app.post('/api/logout')
def logout(response: Response):
    response.delete_cookie('apex_session')
    return {'ok':True}


@app.get('/',response_class=HTMLResponse)
def home():
    return (BASE_DIR/'app'/'templates'/'index.html').read_text(encoding='utf-8')


@app.get('/health')
def health():
    with db() as con:
        con.execute('SELECT 1 FROM account LIMIT 1').fetchone()
    running = bool(ENGINE_THREAD and ENGINE_THREAD.is_alive() and time.time()-LAST_TICK < 30)
    status = 200 if running or os.getenv('APEX_DISABLE_ENGINE') == '1' else 503
    return JSONResponse({'status':'ok' if status == 200 else 'degraded','mode':'paper',
                         'feed':'simulated','engine_running':running},status_code=status)


class OrderRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False,extra='forbid')
    symbol: str = Field(max_length=10)
    side: Literal['buy','sell']
    qty: float = Field(gt=0,le=1e9)
    stop_loss: float | None = Field(default=None,gt=0)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False,extra='forbid')
    risk_per_trade: float = Field(ge=.001,le=.02)
    max_position_pct: float = Field(ge=.05,le=.5)
    daily_loss_limit_pct: float = Field(ge=.01,le=.1)
    auto_trade: bool = False


@app.get('/api/market')
def market():
    with LOCK, db() as con:
        quotes = {r['symbol']:dict(r) for r in con.execute('SELECT * FROM prices')}
    return {'mode':'paper','feed':'simulated','assets':[
        {'symbol':s,'name':spec['name'],'asset_class':spec['asset_class'],
         'price':quotes[s]['price'],'updated_at':quotes[s]['ts']} for s,spec in ASSETS.items()]}


@app.get('/api/account')
def account():
    return account_snapshot()


@app.get('/api/positions')
def positions():
    with LOCK, db() as con:
        return positions_from(con)


@app.get('/api/trades')
def trades(limit: int = 50):
    with LOCK, db() as con:
        return [dict(r) for r in con.execute('SELECT * FROM trades ORDER BY id DESC LIMIT ?', (max(1,min(200,limit)),))]


@app.get('/api/history')
def history():
    with LOCK, db() as con:
        return [dict(r) for r in con.execute('SELECT * FROM equity_history ORDER BY ts')]


@app.get('/api/settings')
def settings():
    with LOCK, db() as con:
        return settings_from(con)


@app.put('/api/settings')
def update_settings(req: SettingsRequest):
    with LOCK, db() as con:
        for k,v in req.model_dump().items():
            con.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,json.dumps(v)))
    return settings()


@app.post('/api/orders')
def place_order(order: OrderRequest):
    return execute_paper_trade(order.symbol,order.side,order.qty,stop_loss=order.stop_loss)

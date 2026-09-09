import math
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import main as m

@pytest.fixture
def client(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'DB_PATH',tmp_path/'test.db')
 monkeypatch.setattr(m,'APP_PASSWORD','')
 monkeypatch.setattr(m,'PRODUCTION',False)
 monkeypatch.setenv('APEX_DISABLE_ENGINE','1')
 m.LOGIN_FAILURES.clear()
 with TestClient(m.app,headers={'X-Apex-Request':'1'}) as c:
  yield c

def order(c,qty=.001,side='buy',**extra):
 return c.post('/api/orders',json={'symbol':'BTCUSD','side':side,'qty':qty,**extra})

def quote(symbol,price):
 with m.LOCK,m.db() as con:con.execute('UPDATE prices SET price=? WHERE symbol=?',(price,symbol))

def test_health_dashboard_market(client):
 assert client.get('/health').json()['mode']=='paper'
 assert 'not live market data' in client.get('/').text
 assert len(client.get('/api/market').json()['assets'])==5
 assert client.get('/api/account').json()['equity']==10000

def test_roundtrip_accounting(client):
 assert order(client,qty=.01).status_code==200
 quote('BTCUSD',65000)
 a=client.get('/api/account').json()
 assert a['cash']==9360 and a['equity']==10010
 assert order(client,qty=.004,side='sell').status_code==200
 a=client.get('/api/account').json()
 assert a['realised_pnl']==pytest.approx(4)
 assert a['unrealised_pnl']==pytest.approx(6)
 assert order(client,qty=.006,side='sell').status_code==200
 assert client.get('/api/positions').json()==[]
 assert client.get('/api/account').json()['cash']==10010

def test_cumulative_cap(client):
 assert order(client,qty=.02).status_code==200
 r=order(client,qty=.02)
 assert r.status_code==400 and 'position cap' in r.text
 assert client.get('/api/account').json()['cash']==8720
 assert len(client.get('/api/trades').json())==1

def test_risk_to_stop(client):
 r=order(client,qty=.01,stop_loss=50000)
 assert r.status_code==400 and 'risk budget' in r.text
 assert order(client,stop_loss=65000).status_code==400
 assert order(client,stop_loss=63000).status_code==200
 assert order(client,stop_loss=62000).status_code==400

def test_exit_not_blocked_by_cap(client):
 assert order(client,qty=.03).status_code==200
 quote('BTCUSD',128000)
 assert order(client,qty=.03,side='sell').status_code==200
 assert client.get('/api/account').json()['cash']==11920

def test_loss_limit_manual_latched(client):
 assert order(client,qty=.03).status_code==200
 quote('BTCUSD',50000)
 r=order(client,qty=.001)
 assert r.status_code==400 and 'Daily loss' in r.text
 quote('BTCUSD',64000)
 assert order(client).status_code==400
 assert order(client,qty=.03,side='sell').status_code==200

def test_day_rollover(client,monkeypatch):
 assert order(client,qty=.03).status_code==200
 quote('BTCUSD',50000)
 assert order(client).status_code==400
 quote('BTCUSD',64000)
 monkeypatch.setattr(m,'now_iso',lambda:'2099-01-02T00:00:00+00:00')
 assert order(client,qty=.001).status_code==200

def test_stop_fill_at_quote(client):
 assert order(client,qty=.01,stop_loss=63000).status_code==200
 quote('BTCUSD',62000)
 m.strategy_tick()
 assert client.get('/api/positions').json()==[]
 trade=client.get('/api/trades').json()[0]
 assert trade['source']=='stop-loss' and trade['price']==62000
 assert client.get('/api/account').json()['realised_pnl']==-20

@pytest.mark.parametrize('payload',[{'qty':0},{'qty':-1},{'symbol':'BAD'},{'side':'short'},{'qty':1e10},{'stop_loss':-10}])
def test_invalid_orders(client,payload):
 data={'symbol':'BTCUSD','side':'buy','qty':.001,**payload}
 assert client.post('/api/orders',json=data).status_code in (400,422)
 assert client.get('/api/account').json()['cash']==10000

def test_no_short(client):
 assert order(client,side='sell').status_code==400

def test_nonfinite_internal(client):
 with pytest.raises(HTTPException):m.execute_paper_trade('BTCUSD','buy',math.inf)

def test_concurrent_fills_serialized(client):
 def buy(_):
  try:m.execute_paper_trade('BTCUSD','buy',.02);return True
  except HTTPException:return False
 with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(buy,range(8)))
 assert sum(results)==1
 assert client.get('/api/account').json()['cash']==8720

def test_persistence(client):
 assert order(client,qty=.01).status_code==200
 quote('BTCUSD',65000)
 before=client.get('/api/account').json()
 m.init_db()
 assert client.get('/api/account').json()==before
 assert client.get('/api/market').json()['assets'][0]['price']==65000

def test_login_logout_csrf(client,monkeypatch):
 monkeypatch.setattr(m,'APP_PASSWORD','a-test-password-long-enough')
 assert client.get('/api/account').status_code==401
 assert order(client).status_code==401
 assert client.post('/api/login',json={'password':'wrong'}).status_code==401
 r=client.post('/api/login',json={'password':m.APP_PASSWORD})
 assert r.status_code==200 and 'HttpOnly' in r.headers['set-cookie']
 assert client.get('/api/account').status_code==200
 assert order(client).status_code==200
 assert client.post('/api/orders',headers={'X-Apex-Request':''},json={'symbol':'BTCUSD','side':'buy','qty':.001}).status_code==403
 assert client.post('/api/logout').status_code==200
 assert client.get('/api/account').status_code==401

def test_rate_limit(client,monkeypatch):
 monkeypatch.setattr(m,'APP_PASSWORD','a-test-password-long-enough')
 for _ in range(10):assert client.post('/api/login',json={'password':'wrong'}).status_code==401
 assert client.post('/api/login',json={'password':'wrong'}).status_code==429

def test_settings_and_history(client):
 assert client.put('/api/settings',json={**m.DEFAULTS,'max_position_pct':2}).status_code==422
 assert client.put('/api/settings',json={**m.DEFAULTS,'auto_trade':True}).status_code==200
 m.market_tick()
 assert len(client.get('/api/history').json())==1

def test_production_requires_password(client,monkeypatch):
 monkeypatch.setattr(m,'PRODUCTION',True)
 with pytest.raises(RuntimeError,match='APP_PASSWORD'):
  with TestClient(m.app):pass

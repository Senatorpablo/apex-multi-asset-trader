"""
Custom exchange adapter for MetaTrader 5.

This module provides a thin wrapper around the `MetaTrader5` Python API to expose
methods similar to those provided by CCXT.  Freqtrade will import this class
based on the `name` specified in your config.  You must install the
`MetaTrader5` package separately and have MT5 running on your system.

WARNING: This is a simplified example for educational purposes.  It does not
handle all edge cases and should be extended and thoroughly tested before
real trading.  Use at your own risk.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore

class Mt5Exchange:
    """MetaTrader 5 exchange adapter compatible with Freqtrade."""

    def __init__(self, login: int, password: str, server: str, **kwargs: Any) -> None:
        if mt5 is None:
            raise ImportError("MetaTrader5 package is required for Mt5Exchange."
                              "Run `pip install MetaTrader5` to install it.")
        self.login = login
        self.password = password
        self.server = server
        self.connected = False
        self._initialize()

    def _initialize(self) -> None:
        # Initialize MT5 terminal
        if not mt5.initialize(server=self.server, login=self.login, password=self.password):
            code, msg = mt5.last_error()
            raise RuntimeError(f"MT5 initialize failed: {code} {msg}")
        self.connected = True

    def fetch_balance(self) -> Dict[str, float]:
        """Return account balance, margin and equity."""
        acc = mt5.account_info()
        return {
            'free': acc.balance,
            'used': acc.margin,
            'total': acc.equity
        }

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: Optional[int] = None,
                    limit: int = 100) -> List[List[Any]]:
        """Fetch OHLCV data for the specified symbol and timeframe.

        :param symbol: trading instrument (e.g. 'EURUSD')
        :param timeframe: timeframe string ('1m', '5m', '1h', '1d')
        :param since: unused; fetches most recent `limit` candles
        :param limit: number of candles to fetch
        :return: list of [timestamp, open, high, low, close, volume] rows
        """
        tf_map = {
            '1m': mt5.TIMEFRAME_M1,
            '5m': mt5.TIMEFRAME_M5,
            '15m': mt5.TIMEFRAME_M15,
            '30m': mt5.TIMEFRAME_M30,
            '1h': mt5.TIMEFRAME_H1,
            '4h': mt5.TIMEFRAME_H4,
            '1d': mt5.TIMEFRAME_D1
        }
        if timeframe not in tf_map:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        rates = mt5.copy_rates_from_pos(symbol, tf_map[timeframe], 0, limit)
        df = pd.DataFrame(rates)
        # Convert seconds to milliseconds to match CCXT / Freqtrade expectations
        df['time'] = pd.to_datetime(df['time'], unit='s')
        result = []
        for _, row in df.iterrows():
            result.append([
                int(row['time'].timestamp() * 1000),  # ms timestamp
                float(row['open']),
                float(row['high']),
                float(row['low']),
                float(row['close']),
                float(row['tick_volume'])
            ])
        return result

    def fetch_ticker(self, symbol: str) -> Dict[str, float]:
        """Return the current bid/ask prices."""
        tick = mt5.symbol_info_tick(symbol)
        return {
            'bid': float(tick.bid),
            'ask': float(tick.ask),
            'last': float(tick.last)
        }

    def create_order(self, symbol: str, order_type: str, side: str, amount: float,
                     price: Optional[float] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Place an order.  Only market and limit orders are implemented in this example.

        :param symbol: instrument to trade (e.g. 'EURUSD')
        :param order_type: 'market' or 'limit'
        :param side: 'buy' or 'sell'
        :param amount: volume to trade (in lots)
        :param price: price for limit orders (ignored for market orders)
        :param params: optional dict for stop_loss, take_profit and other MT5 params
        :return: dict containing order id and raw MT5 response
        """
        if params is None:
            params = {}
        if order_type not in ('market', 'limit'):
            raise ValueError("order_type must be 'market' or 'limit'")
        action = mt5.TRADE_ACTION_DEAL if order_type == 'market' else mt5.TRADE_ACTION_PENDING
        order_type_code = mt5.ORDER_TYPE_BUY if side == 'buy' else mt5.ORDER_TYPE_SELL
        request = {
            'action': action,
            'symbol': symbol,
            'volume': amount,
            'type': order_type_code,
            'price': price or 0.0,
            'sl': params.get('stop_loss'),
            'tp': params.get('take_profit'),
            'deviation': params.get('deviation', 5),
            'comment': params.get('comment', 'mt5-order'),
            'magic': params.get('magic', 0),
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MT5 order failed: {result.retcode} {result.comment}")
        return {
            'id': result.order,
            'info': result._asdict()
        }

    def cancel_order(self, order_id: int, symbol: str) -> bool:
        """Cancel a pending order by order id."""
        req = {
            'action': mt5.TRADE_ACTION_REMOVE,
            'order': order_id,
            'symbol': symbol,
            'comment': 'cancel'
        }
        res = mt5.order_send(req)
        return res.retcode == mt5.TRADE_RETCODE_DONE

    def close(self) -> None:
        """Shutdown MT5 connection."""
        if self.connected:
            mt5.shutdown()
            self.connected = False

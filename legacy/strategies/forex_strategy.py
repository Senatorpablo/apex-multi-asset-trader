"""
ForexStrategy: Example strategy for trading foreign exchange pairs.

This strategy trades on a 1‑hour timeframe using moving averages and
volatility‑based position sizing.  The position size is calculated
so that each trade risks a fixed percentage of account equity.
"""
from __future__ import annotations

from typing import Dict, Tuple
import math
from pandas import DataFrame
from freqtrade.strategy import IStrategy
import pandas_ta as ta

class ForexStrategy(IStrategy):
    timeframe = '1h'
    stoploss = -0.01  # 1% stoploss (fallback)
    minimal_roi = {
        "0": 0.015,  # 1.5%
        "120": 0.005
    }

    # Custom risk parameters
    max_risk_per_trade = 0.005  # 0.5% of account equity

    def populate_indicators(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """Compute indicators used by the strategy."""
        # Simple moving averages
        dataframe['sma_fast'] = ta.sma(dataframe['close'], length=20)
        dataframe['sma_slow'] = ta.sma(dataframe['close'], length=50)
        # Average true range for volatility
        dataframe['atr'] = ta.atr(dataframe['high'], dataframe['low'], dataframe['close'], length=14)
        return dataframe

    def position_size_by_risk(self, equity: float, entry: float, stop: float,
                              atr: float) -> float:
        """Calculate trade size (in lots) based on risk percentage and ATR."""
        risk_amount = equity * self.max_risk_per_trade
        per_unit_risk = max(1e-8, abs(entry - stop))
        # Many brokers specify lot sizes where 1 lot = 100k units; adjust to your broker's contract size
        contract_size = 100000.0
        lots = (risk_amount / per_unit_risk) / contract_size
        return max(0.0, lots)

    def populate_entry_trend(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """Generate buy signals."""
        dataframe.loc[
            (dataframe['sma_fast'] > dataframe['sma_slow']) &
            (dataframe['close'] > dataframe['sma_fast']),
            'enter_long'
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """Generate exit signals."""
        dataframe.loc[
            (dataframe['sma_fast'] < dataframe['sma_slow']),
            'exit_long'
        ] = 1
        return dataframe

    # Optional: implement custom entry confirmation to integrate market regime filters
    # def confirm_trade_entry(self, pair: str, order_type: str, amount: float, price: float,
    #                        time_in_force: str, **kwargs) -> bool:
    #     # Call an external risk service here and return False to skip trades when risk is high
    #     return True

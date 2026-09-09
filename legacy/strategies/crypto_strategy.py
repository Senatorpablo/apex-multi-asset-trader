"""
CryptoStrategy: A simple example strategy for trading cryptocurrency pairs.

This strategy uses fast and slow exponential moving averages to generate
entry and exit signals on a 5‑minute timeframe.  It is intended as a
baseline; you should customise the indicators, timeframes and risk
management to suit your trading style.
"""
from typing import Dict
from pandas import DataFrame
from freqtrade.strategy import IStrategy
import pandas_ta as ta

class CryptoStrategy(IStrategy):
    # Define the timeframe and minimal return on investment (ROI)
    timeframe = '5m'
    stoploss = -0.02  # 2% stoploss
    minimal_roi = {
        "0": 0.02,  # 2% ROI
        "60": 0.01,  # 1% after 1 hour
        "120": 0    # break even after 2 hours
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """Add technical indicators to the dataframe."""
        # Calculate exponential moving averages
        dataframe['ema_fast'] = ta.ema(dataframe['close'], length=10)
        dataframe['ema_slow'] = ta.ema(dataframe['close'], length=30)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """Define entry conditions for long trades."""
        dataframe.loc[
            (dataframe['ema_fast'] > dataframe['ema_slow']) &
            (dataframe['close'] > dataframe['ema_fast']),
            'enter_long'
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """Define exit conditions for long trades."""
        dataframe.loc[
            (dataframe['ema_fast'] < dataframe['ema_slow']),
            'exit_long'
        ] = 1
        return dataframe

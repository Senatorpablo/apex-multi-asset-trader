"""
CommodityStrategy: Template for trading commodities (e.g. gold, oil).

This strategy runs on a 30‑minute timeframe and uses Donchian channels to
identify breakouts.  You can modify the indicators and entry rules to fit
the specific commodity market you plan to trade.
"""
from typing import Dict
from pandas import DataFrame
from freqtrade.strategy import IStrategy
import pandas_ta as ta

class CommodityStrategy(IStrategy):
    timeframe = '30m'
    stoploss = -0.015  # 1.5% stoploss
    minimal_roi = {
        "0": 0.02,
        "90": 0.01,
        "180": 0
    }

    donchian_length = 20

    def populate_indicators(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """Calculate Donchian channel and midline."""
        # Donchian channels: upper and lower bands
        don = ta.donchian(high=dataframe['high'], low=dataframe['low'], close=dataframe['close'], length=self.donchian_length)
        dataframe['dc_upper'] = don['DONCHIAN_20_U']
        dataframe['dc_lower'] = don['DONCHIAN_20_L']
        dataframe['dc_mid'] = (dataframe['dc_upper'] + dataframe['dc_lower']) / 2.0
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """Enter long when price breaks above upper Donchian band."""
        dataframe.loc[
            (dataframe['close'] > dataframe['dc_upper']),
            'enter_long'
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """Exit when price crosses back below the midline."""
        dataframe.loc[
            (dataframe['close'] < dataframe['dc_mid']),
            'exit_long'
        ] = 1
        return dataframe

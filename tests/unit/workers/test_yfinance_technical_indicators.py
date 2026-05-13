"""Unit tests for yfinance technical indicators.

Tests the fetch_technical_indicators function and helper functions
for calculating RSI, MA alignment, Bollinger Bands, etc.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "workers", "data_collector")
)
if _WORKER_ROOT not in sys.path:
    sys.path.insert(0, _WORKER_ROOT)


# ─── Helper function tests ─────────────────────────────────────────


class TestCalculateRSI:
    """Test RSI calculation using Wilder's smoothing method."""
    
    def test_rsi_with_sufficient_data(self):
        """RSI calculation with 15+ prices should return valid RSI."""
        from clients.yfinance_client import _calculate_rsi
        
        # Simple uptrend: prices increasing
        prices = [100.0 + i for i in range(20)]
        rsi = _calculate_rsi(prices)
        
        assert rsi is not None
        assert 0 <= rsi <= 100
        assert rsi > 50  # Uptrend should have RSI > 50
    
    def test_rsi_with_insufficient_data(self):
        """RSI calculation with < 15 prices should return None."""
        from clients.yfinance_client import _calculate_rsi
        
        prices = [100.0, 101.0, 102.0]
        rsi = _calculate_rsi(prices)
        
        assert rsi is None
    
    def test_rsi_all_gains(self):
        """RSI with all gains should return 100."""
        from clients.yfinance_client import _calculate_rsi
        
        # All prices increasing
        prices = [100.0 + i * 2 for i in range(20)]
        rsi = _calculate_rsi(prices)
        
        assert rsi is not None
        assert rsi == 100.0
    
    def test_rsi_precision(self):
        """RSI should be rounded to 2 decimal places."""
        from clients.yfinance_client import _calculate_rsi
        
        # Mixed gains and losses
        prices = [100, 102, 101, 103, 102, 104, 103, 105, 104, 106, 
                 105, 107, 106, 108, 107, 109, 108, 110, 109, 111]
        rsi = _calculate_rsi(prices)
        
        assert rsi is not None
        # Check that it has at most 2 decimal places
        assert len(str(rsi).split('.')[-1]) <= 2


class TestCalculateMAAlignment:
    """Test moving average alignment classification."""
    
    def test_bullish_alignment(self):
        """MA5 > MA20 > MA60 should return 'bullish'."""
        from clients.yfinance_client import _calculate_ma_alignment
        
        alignment = _calculate_ma_alignment(110.0, 105.0, 100.0)
        assert alignment == "bullish"
    
    def test_bearish_alignment(self):
        """MA5 < MA20 < MA60 should return 'bearish'."""
        from clients.yfinance_client import _calculate_ma_alignment
        
        alignment = _calculate_ma_alignment(100.0, 105.0, 110.0)
        assert alignment == "bearish"
    
    def test_neutral_alignment(self):
        """Mixed MA order should return 'neutral'."""
        from clients.yfinance_client import _calculate_ma_alignment
        
        # MA5 > MA60 > MA20 (not bullish or bearish)
        alignment = _calculate_ma_alignment(110.0, 100.0, 105.0)
        assert alignment == "neutral"
    
    def test_missing_ma_values(self):
        """Any None MA value should return None."""
        from clients.yfinance_client import _calculate_ma_alignment
        
        assert _calculate_ma_alignment(None, 105.0, 100.0) is None
        assert _calculate_ma_alignment(110.0, None, 100.0) is None
        assert _calculate_ma_alignment(110.0, 105.0, None) is None


# ─── Integration tests with mocked yfinance ────────────────────────


class TestFetchTechnicalIndicators:
    """Test the main fetch_technical_indicators function."""
    
    @pytest.mark.asyncio
    async def test_empty_history_returns_null_indicators(self, monkeypatch):
        """When yfinance returns empty history, all indicators should be None."""
        from clients.yfinance_client import fetch_technical_indicators
        
        class MockTicker:
            def history(self, period, interval):
                import pandas as pd
                return pd.DataFrame()  # Empty dataframe
        
        def mock_ticker(symbol):
            return MockTicker()
        
        import clients.yfinance_client as yf_module
        monkeypatch.setattr(yf_module.yf, "Ticker", mock_ticker)
        
        result = await fetch_technical_indicators("005930.KS", date(2026, 4, 28))
        
        assert result.ticker == "005930.KS"
        assert result.volume is None
        assert result.rsi is None
        assert result.ma_alignment is None
        assert result.bb_position is None
    
    @pytest.mark.asyncio
    async def test_insufficient_data_returns_partial_indicators(self, monkeypatch):
        """With < 20 days of data, only some indicators should be calculated."""
        from clients.yfinance_client import fetch_technical_indicators
        import pandas as pd
        
        class MockTicker:
            def history(self, period, interval):
                # Return only 10 days of data
                dates = pd.date_range(end='2026-04-28', periods=10)
                return pd.DataFrame({
                    'Close': [100.0 + i for i in range(10)],
                    'Volume': [1000000 + i * 10000 for i in range(10)]
                }, index=dates)
        
        def mock_ticker(symbol):
            return MockTicker()
        
        import clients.yfinance_client as yf_module
        monkeypatch.setattr(yf_module.yf, "Ticker", mock_ticker)
        
        result = await fetch_technical_indicators("005930.KS", date(2026, 4, 28))
        
        # Should have volume but not enough for 20-day calculations
        assert result.volume is not None
        assert result.volume_ratio is None  # Need 20 days
        assert result.ma_20d is None  # Need 20 days
        assert result.bb_position is None  # Need 20 days
    
    @pytest.mark.asyncio
    async def test_sufficient_data_calculates_all_indicators(self, monkeypatch):
        """With 60+ days of data, all indicators should be calculated."""
        from clients.yfinance_client import fetch_technical_indicators
        import pandas as pd
        
        class MockTicker:
            def history(self, period, interval):
                # Return 60 days of data with uptrend
                dates = pd.date_range(end='2026-04-28', periods=60)
                return pd.DataFrame({
                    'Close': [100.0 + i * 0.5 for i in range(60)],
                    'Volume': [1000000 + i * 10000 for i in range(60)]
                }, index=dates)
        
        def mock_ticker(symbol):
            return MockTicker()
        
        import clients.yfinance_client as yf_module
        monkeypatch.setattr(yf_module.yf, "Ticker", mock_ticker)
        
        result = await fetch_technical_indicators("005930.KS", date(2026, 4, 28))
        
        # All indicators should be calculated
        assert result.volume is not None
        assert result.volume_ratio is not None
        assert result.rsi is not None
        assert result.ma_5d is not None
        assert result.ma_20d is not None
        assert result.ma_60d is not None
        assert result.ma_alignment == "bullish"  # Uptrend
        assert result.bb_upper is not None
        assert result.bb_lower is not None
        assert result.bb_position is not None
        assert result.trading_value is not None
    
    @pytest.mark.asyncio
    async def test_exception_returns_empty_indicators(self, monkeypatch):
        """When yfinance raises exception, should return empty TechnicalIndicators."""
        from clients.yfinance_client import fetch_technical_indicators
        
        def mock_ticker(symbol):
            raise Exception("API Error")
        
        import clients.yfinance_client as yf_module
        monkeypatch.setattr(yf_module.yf, "Ticker", mock_ticker)
        
        result = await fetch_technical_indicators("005930.KS", date(2026, 4, 28))
        
        # Should return TechnicalIndicators with all None values
        assert result.ticker == "005930.KS"
        assert result.volume is None
        assert result.rsi is None
    
    @pytest.mark.asyncio
    async def test_bollinger_position_validation(self, monkeypatch):
        """Bollinger position outside [0, 1] should be set to None."""
        from clients.yfinance_client import fetch_technical_indicators
        import pandas as pd
        
        class MockTicker:
            def history(self, period, interval):
                # Create data where current price is way above upper band
                dates = pd.date_range(end='2026-04-28', periods=60)
                closes = [100.0] * 59 + [200.0]  # Last price much higher
                return pd.DataFrame({
                    'Close': closes,
                    'Volume': [1000000] * 60
                }, index=dates)
        
        def mock_ticker(symbol):
            return MockTicker()
        
        import clients.yfinance_client as yf_module
        monkeypatch.setattr(yf_module.yf, "Ticker", mock_ticker)
        
        result = await fetch_technical_indicators("005930.KS", date(2026, 4, 28))
        
        # Bollinger position should be None because it's > 1
        assert result.bb_position is None
    
    @pytest.mark.asyncio
    async def test_volume_ratio_zero_average(self, monkeypatch):
        """Volume ratio should be None when 20-day average is zero."""
        from clients.yfinance_client import fetch_technical_indicators
        import pandas as pd
        
        class MockTicker:
            def history(self, period, interval):
                dates = pd.date_range(end='2026-04-28', periods=60)
                # All volumes zero (including last 20 days)
                volumes = [0] * 60
                return pd.DataFrame({
                    'Close': [100.0] * 60,
                    'Volume': volumes
                }, index=dates)
        
        def mock_ticker(symbol):
            return MockTicker()
        
        import clients.yfinance_client as yf_module
        monkeypatch.setattr(yf_module.yf, "Ticker", mock_ticker)
        
        result = await fetch_technical_indicators("005930.KS", date(2026, 4, 28))
        
        # Volume ratio should be None because 20-day average is zero
        assert result.volume_ratio is None

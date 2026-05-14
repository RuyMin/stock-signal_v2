"""테스트용 signals/news/macro_indicators 데이터 생성."""
from datetime import date


class SignalFactory:
    @staticmethod
    async def create(
        pool,
        d: date,
        ticker: str,
        consecutive_buy_days: int = 3,
        agency_net_buy: int = 1_000_000_000,
        foreign_net_buy: int = 500_000_000,
        one_day_net_buy: int | None = None,
        three_day_avg_net_buy: int | None = None,
        volume_ratio: float | None = None,
        rsi: float | None = None,
        ma_alignment: str | None = None,
        bollinger_position: float | None = None,
        trading_value: int | None = None,
    ) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO signals (
                    date, ticker, agency_buy, agency_sell, agency_net_buy,
                    foreign_buy, foreign_sell, foreign_net_buy, consecutive_buy_days,
                    one_day_net_buy, three_day_avg_net_buy, volume_ratio,
                    rsi, ma_alignment, bollinger_position, trading_value
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                ON CONFLICT (date, ticker) DO UPDATE SET
                    consecutive_buy_days = EXCLUDED.consecutive_buy_days,
                    one_day_net_buy = EXCLUDED.one_day_net_buy,
                    three_day_avg_net_buy = EXCLUDED.three_day_avg_net_buy,
                    volume_ratio = EXCLUDED.volume_ratio,
                    rsi = EXCLUDED.rsi,
                    ma_alignment = EXCLUDED.ma_alignment,
                    bollinger_position = EXCLUDED.bollinger_position,
                    trading_value = EXCLUDED.trading_value
                """,
                d, ticker,
                agency_net_buy + 100_000_000, 100_000_000, agency_net_buy,
                foreign_net_buy + 100_000_000, 100_000_000, foreign_net_buy,
                consecutive_buy_days,
                one_day_net_buy, three_day_avg_net_buy, volume_ratio,
                rsi, ma_alignment, bollinger_position, trading_value,
            )

    @staticmethod
    async def create_news(
        pool, d: date, ticker: str, title: str = "테스트 뉴스 헤드라인"
    ) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO news (date, ticker, title, source) VALUES ($1, $2, $3, 'naver')",
                d, ticker, title,
            )

    @staticmethod
    async def create_macro(
        pool,
        d: date,
        us10y: float = 4.2,
        dxy: float = 105.0,
        wti: float = 80.0,
        sp500: float = 5000.0,
        gold: float = 2300.0,
    ) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO macro_indicators (date, us10y, dxy, wti, sp500, gold)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (date) DO UPDATE SET
                    us10y = EXCLUDED.us10y, dxy = EXCLUDED.dxy
                """,
                d, us10y, dxy, wti, sp500, gold,
            )

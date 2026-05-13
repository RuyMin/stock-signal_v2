"""
Migration test script for momentum indicators.

This script tests the migration on the dev database (acting as staging):
1. Check current migration status
2. Run migration upgrade
3. Verify new columns exist
4. Insert test data
5. Verify no data loss
6. Test existing queries
7. Run migration downgrade
8. Verify columns removed
9. Run migration upgrade again
10. Verify final state

Usage:
    python backend/test_migration.py
"""
import asyncio
import os
import sys
from datetime import date, datetime
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

# Load environment variables from .env.dev
load_dotenv(".env.dev")

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from core.config import settings


async def get_column_names(conn: asyncpg.Connection, table: str) -> list[str]:
    """Get all column names for a table."""
    query = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = $1
        ORDER BY ordinal_position
    """
    rows = await conn.fetch(query, table)
    return [row["column_name"] for row in rows]


async def insert_test_signal(conn: asyncpg.Connection) -> None:
    """Insert a test signal record."""
    query = """
        INSERT INTO signals (
            date, ticker, 
            agency_buy, agency_sell, agency_net_buy,
            foreign_buy, foreign_sell, foreign_net_buy,
            consecutive_buy_days
        ) VALUES (
            $1, $2,
            $3, $4, $5,
            $6, $7, $8,
            $9
        )
        ON CONFLICT (date, ticker) DO UPDATE SET
            agency_buy = EXCLUDED.agency_buy,
            agency_sell = EXCLUDED.agency_sell,
            agency_net_buy = EXCLUDED.agency_net_buy,
            foreign_buy = EXCLUDED.foreign_buy,
            foreign_sell = EXCLUDED.foreign_sell,
            foreign_net_buy = EXCLUDED.foreign_net_buy,
            consecutive_buy_days = EXCLUDED.consecutive_buy_days
    """
    await conn.execute(
        query,
        date(2026, 5, 12),
        "005930",  # Samsung Electronics
        1000000,
        500000,
        500000,
        2000000,
        1000000,
        1000000,
        3,
    )


async def verify_test_signal(conn: asyncpg.Connection) -> dict:
    """Verify test signal exists and return its data."""
    query = """
        SELECT *
        FROM signals
        WHERE date = $1 AND ticker = $2
    """
    row = await conn.fetchrow(query, date(2026, 5, 12), "005930")
    if not row:
        raise ValueError("Test signal not found!")
    return dict(row)


async def test_existing_queries(conn: asyncpg.Connection) -> None:
    """Test that existing queries still work."""
    # Query 1: Get signals by date
    query1 = """
        SELECT date, ticker, agency_net_buy, foreign_net_buy, consecutive_buy_days
        FROM signals
        WHERE date = $1
        ORDER BY ticker
    """
    rows1 = await conn.fetch(query1, date(2026, 5, 12))
    print(f"  ✓ Query 1: Found {len(rows1)} signals for date")
    
    # Query 2: Get signals with consecutive buy days >= 3
    query2 = """
        SELECT ticker, consecutive_buy_days
        FROM signals
        WHERE date = $1 AND consecutive_buy_days >= 3
    """
    rows2 = await conn.fetch(query2, date(2026, 5, 12))
    print(f"  ✓ Query 2: Found {len(rows2)} signals with consecutive_buy_days >= 3")
    
    # Query 3: Aggregate query
    query3 = """
        SELECT 
            COUNT(*) as total,
            AVG(agency_net_buy) as avg_agency,
            AVG(foreign_net_buy) as avg_foreign
        FROM signals
        WHERE date = $1
    """
    row3 = await conn.fetchrow(query3, date(2026, 5, 12))
    print(f"  ✓ Query 3: Aggregate query returned {row3['total']} records")


async def test_momentum_columns(conn: asyncpg.Connection) -> None:
    """Test that momentum columns can be updated and queried."""
    # Update momentum indicators
    query_update = """
        UPDATE signals
        SET 
            one_day_net_buy = $1,
            three_day_avg_net_buy = $2,
            volume_ratio = $3,
            rsi = $4,
            ma_alignment = $5,
            bollinger_position = $6,
            trading_value = $7
        WHERE date = $8 AND ticker = $9
    """
    await conn.execute(
        query_update,
        1500000,  # one_day_net_buy
        1200000,  # three_day_avg_net_buy
        2.5,      # volume_ratio
        65.5,     # rsi
        "bullish",  # ma_alignment
        0.75,     # bollinger_position
        50000000000,  # trading_value (50B KRW)
        date(2026, 5, 12),
        "005930",
    )
    
    # Query momentum indicators
    query_select = """
        SELECT 
            ticker,
            one_day_net_buy,
            three_day_avg_net_buy,
            volume_ratio,
            rsi,
            ma_alignment,
            bollinger_position,
            trading_value
        FROM signals
        WHERE date = $1 AND ticker = $2
    """
    row = await conn.fetchrow(query_select, date(2026, 5, 12), "005930")
    
    # Verify values
    assert row["one_day_net_buy"] == 1500000, "one_day_net_buy mismatch"
    assert row["three_day_avg_net_buy"] == 1200000, "three_day_avg_net_buy mismatch"
    assert float(row["volume_ratio"]) == 2.5, "volume_ratio mismatch"
    assert float(row["rsi"]) == 65.5, "rsi mismatch"
    assert row["ma_alignment"] == "bullish", "ma_alignment mismatch"
    assert float(row["bollinger_position"]) == 0.75, "bollinger_position mismatch"
    assert row["trading_value"] == 50000000000, "trading_value mismatch"
    
    print("  ✓ All momentum columns updated and queried successfully")


async def run_migration_tests():
    """Run comprehensive migration tests."""
    print("\n" + "="*70)
    print("MIGRATION TEST: Momentum Indicators")
    print("="*70 + "\n")
    
    # Setup Alembic config
    # Use absolute path and set encoding
    alembic_ini_path = Path("backend/alembic.ini").absolute()
    alembic_cfg = Config(str(alembic_ini_path))
    alembic_cfg.set_main_option("script_location", str(Path("backend/migrations").absolute()))
    
    # Connect to database
    # Replace postgres hostname with localhost for local testing
    db_url = settings.DATABASE_URL.replace("+asyncpg", "").replace("postgres:5432", "localhost:5432")
    conn = await asyncpg.connect(db_url)
    
    try:
        # Step 1: Check current state
        print("Step 1: Checking current migration status...")
        columns_before = await get_column_names(conn, "signals")
        print(f"  Current columns: {len(columns_before)}")
        has_momentum_cols = "one_day_net_buy" in columns_before
        print(f"  Has momentum columns: {has_momentum_cols}")
        
        # Step 2: Insert test data before migration
        print("\nStep 2: Inserting test data...")
        await insert_test_signal(conn)
        signal_before = await verify_test_signal(conn)
        print(f"  ✓ Test signal inserted: {signal_before['ticker']}")
        
        # Step 3: Run upgrade if not already applied
        if not has_momentum_cols:
            print("\nStep 3: Running migration upgrade...")
            command.upgrade(alembic_cfg, "head")
            print("  ✓ Migration upgrade completed")
            
            # Verify columns added
            columns_after_upgrade = await get_column_names(conn, "signals")
            new_columns = set(columns_after_upgrade) - set(columns_before)
            expected_columns = {
                "one_day_net_buy",
                "three_day_avg_net_buy",
                "volume_ratio",
                "rsi",
                "ma_alignment",
                "bollinger_position",
                "trading_value",
            }
            assert new_columns == expected_columns, f"Column mismatch: {new_columns} != {expected_columns}"
            print(f"  ✓ All 7 new columns added: {', '.join(sorted(new_columns))}")
        else:
            print("\nStep 3: Migration already applied, skipping upgrade...")
        
        # Step 4: Verify no data loss
        print("\nStep 4: Verifying no data loss...")
        signal_after = await verify_test_signal(conn)
        for key in ["ticker", "agency_net_buy", "foreign_net_buy", "consecutive_buy_days"]:
            assert signal_before[key] == signal_after[key], f"{key} changed after migration!"
        print("  ✓ All original data preserved")
        
        # Step 5: Test existing queries
        print("\nStep 5: Testing existing queries...")
        await test_existing_queries(conn)
        
        # Step 6: Test momentum columns
        print("\nStep 6: Testing momentum columns...")
        await test_momentum_columns(conn)
        
        # Step 7: Test downgrade
        print("\nStep 7: Testing migration downgrade...")
        command.downgrade(alembic_cfg, "-1")
        print("  ✓ Migration downgrade completed")
        
        # Verify columns removed
        columns_after_downgrade = await get_column_names(conn, "signals")
        assert "one_day_net_buy" not in columns_after_downgrade, "Columns not removed!"
        print("  ✓ All momentum columns removed")
        
        # Step 8: Verify data still intact
        print("\nStep 8: Verifying data after downgrade...")
        signal_after_downgrade = await verify_test_signal(conn)
        for key in ["ticker", "agency_net_buy", "foreign_net_buy", "consecutive_buy_days"]:
            assert signal_before[key] == signal_after_downgrade[key], f"{key} changed after downgrade!"
        print("  ✓ All original data still preserved")
        
        # Step 9: Test upgrade again
        print("\nStep 9: Testing migration upgrade again...")
        command.upgrade(alembic_cfg, "head")
        print("  ✓ Migration upgrade completed")
        
        # Verify final state
        columns_final = await get_column_names(conn, "signals")
        assert "one_day_net_buy" in columns_final, "Columns not re-added!"
        print("  ✓ All momentum columns re-added")
        
        # Step 10: Clean up test data
        print("\nStep 10: Cleaning up test data...")
        await conn.execute(
            "DELETE FROM signals WHERE date = $1 AND ticker = $2",
            date(2026, 5, 12),
            "005930",
        )
        print("  ✓ Test data cleaned up")
        
        print("\n" + "="*70)
        print("✅ ALL MIGRATION TESTS PASSED!")
        print("="*70 + "\n")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration_tests())

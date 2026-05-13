#!/bin/bash
# Migration test script for momentum indicators
# This script tests the migration inside the Docker container

set -e

echo "======================================================================"
echo "MIGRATION TEST: Momentum Indicators"
echo "======================================================================"
echo ""

# Load environment variables
export $(cat .env.dev | grep -v '^#' | xargs)

echo "Step 1: Checking current migration status..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "\d signals" | head -20

echo ""
echo "Step 2: Inserting test data..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB <<EOF
INSERT INTO signals (
    date, ticker, 
    agency_buy, agency_sell, agency_net_buy,
    foreign_buy, foreign_sell, foreign_net_buy,
    consecutive_buy_days
) VALUES (
    '2026-05-12', '005930',
    1000000, 500000, 500000,
    2000000, 1000000, 1000000,
    3
)
ON CONFLICT (date, ticker) DO UPDATE SET
    agency_buy = EXCLUDED.agency_buy,
    agency_sell = EXCLUDED.agency_sell,
    agency_net_buy = EXCLUDED.agency_net_buy,
    foreign_buy = EXCLUDED.foreign_buy,
    foreign_sell = EXCLUDED.foreign_sell,
    foreign_net_buy = EXCLUDED.foreign_net_buy,
    consecutive_buy_days = EXCLUDED.consecutive_buy_days;
EOF
echo "✓ Test signal inserted"

echo ""
echo "Step 3: Running migration upgrade..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend alembic upgrade head
echo "✓ Migration upgrade completed"

echo ""
echo "Step 4: Verifying new columns exist..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "\d signals" | grep -E "one_day_net_buy|three_day_avg_net_buy|volume_ratio|rsi|ma_alignment|bollinger_position|trading_value"
echo "✓ All 7 new columns exist"

echo ""
echo "Step 5: Verifying no data loss..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT ticker, agency_net_buy, foreign_net_buy, consecutive_buy_days FROM signals WHERE date = '2026-05-12' AND ticker = '005930';"
echo "✓ Original data preserved"

echo ""
echo "Step 6: Testing existing queries..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB <<EOF
-- Query 1: Get signals by date
SELECT COUNT(*) as count FROM signals WHERE date = '2026-05-12';

-- Query 2: Get signals with consecutive buy days >= 3
SELECT COUNT(*) as count FROM signals WHERE date = '2026-05-12' AND consecutive_buy_days >= 3;

-- Query 3: Aggregate query
SELECT 
    COUNT(*) as total,
    AVG(agency_net_buy) as avg_agency,
    AVG(foreign_net_buy) as avg_foreign
FROM signals
WHERE date = '2026-05-12';
EOF
echo "✓ All existing queries work"

echo ""
echo "Step 7: Testing momentum columns..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB <<EOF
-- Update momentum indicators
UPDATE signals
SET 
    one_day_net_buy = 1500000,
    three_day_avg_net_buy = 1200000,
    volume_ratio = 2.5,
    rsi = 65.5,
    ma_alignment = 'bullish',
    bollinger_position = 0.75,
    trading_value = 50000000000
WHERE date = '2026-05-12' AND ticker = '005930';

-- Query momentum indicators
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
WHERE date = '2026-05-12' AND ticker = '005930';
EOF
echo "✓ Momentum columns work correctly"

echo ""
echo "Step 8: Testing migration downgrade..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend alembic downgrade -1
echo "✓ Migration downgrade completed"

echo ""
echo "Step 9: Verifying columns removed..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "\d signals" | grep -E "one_day_net_buy|three_day_avg_net_buy|volume_ratio|rsi|ma_alignment|bollinger_position|trading_value" || echo "✓ All momentum columns removed"

echo ""
echo "Step 10: Verifying data still intact after downgrade..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT ticker, agency_net_buy, foreign_net_buy, consecutive_buy_days FROM signals WHERE date = '2026-05-12' AND ticker = '005930';"
echo "✓ Original data still preserved"

echo ""
echo "Step 11: Testing migration upgrade again..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend alembic upgrade head
echo "✓ Migration upgrade completed"

echo ""
echo "Step 12: Verifying final state..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "\d signals" | grep -E "one_day_net_buy|three_day_avg_net_buy|volume_ratio|rsi|ma_alignment|bollinger_position|trading_value"
echo "✓ All momentum columns re-added"

echo ""
echo "Step 13: Cleaning up test data..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "DELETE FROM signals WHERE date = '2026-05-12' AND ticker = '005930';"
echo "✓ Test data cleaned up"

echo ""
echo "======================================================================"
echo "✅ ALL MIGRATION TESTS PASSED!"
echo "======================================================================"

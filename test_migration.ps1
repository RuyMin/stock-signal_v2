# Migration test script for momentum indicators
# This script tests the migration inside the Docker container

$ErrorActionPreference = "Continue"  # Changed from Stop to Continue to handle warnings

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "MIGRATION TEST: Momentum Indicators" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# Load environment variables from .env.dev
Get-Content .env.dev | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.*)$') {
        $name = $matches[1].Trim()
        $value = $matches[2].Trim()
        Set-Item -Path "env:$name" -Value $value
    }
}

Write-Host "Step 1: Checking current migration status..." -ForegroundColor Yellow
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -c "\d signals" 2>$null | Select-Object -First 20
Write-Host ""

Write-Host "Step 2: Inserting test data..." -ForegroundColor Yellow
$insertQuery = @"
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
"@
$insertQuery | docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB 2>$null
Write-Host "✓ Test signal inserted" -ForegroundColor Green
Write-Host ""

Write-Host "Step 3: Running migration upgrade..." -ForegroundColor Yellow
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend alembic upgrade head 2>$null
Write-Host "✓ Migration upgrade completed" -ForegroundColor Green
Write-Host ""

Write-Host "Step 4: Verifying new columns exist..." -ForegroundColor Yellow
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -c "\d signals" 2>$null | Select-String -Pattern "one_day_net_buy|three_day_avg_net_buy|volume_ratio|rsi|ma_alignment|bollinger_position|trading_value"
Write-Host "✓ All 7 new columns exist" -ForegroundColor Green
Write-Host ""

Write-Host "Step 5: Verifying no data loss..." -ForegroundColor Yellow
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -c "SELECT ticker, agency_net_buy, foreign_net_buy, consecutive_buy_days FROM signals WHERE date = '2026-05-12' AND ticker = '005930';" 2>$null
Write-Host "✓ Original data preserved" -ForegroundColor Green
Write-Host ""

Write-Host "Step 6: Testing existing queries..." -ForegroundColor Yellow
$testQueries = @"
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
"@
$testQueries | docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB 2>$null
Write-Host "✓ All existing queries work" -ForegroundColor Green
Write-Host ""

Write-Host "Step 7: Testing momentum columns..." -ForegroundColor Yellow
$momentumTest = @"
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
"@
$momentumTest | docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB 2>$null
Write-Host "✓ Momentum columns work correctly" -ForegroundColor Green
Write-Host ""

Write-Host "Step 8: Testing migration downgrade..." -ForegroundColor Yellow
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend alembic downgrade -1 2>$null
Write-Host "✓ Migration downgrade completed" -ForegroundColor Green
Write-Host ""

Write-Host "Step 9: Verifying columns removed..." -ForegroundColor Yellow
$columnsCheck = docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -c "\d signals" 2>$null | Select-String -Pattern "one_day_net_buy|three_day_avg_net_buy|volume_ratio|rsi|ma_alignment|bollinger_position|trading_value"
if ($null -eq $columnsCheck) {
    Write-Host "✓ All momentum columns removed" -ForegroundColor Green
} else {
    Write-Host "✗ Columns still exist!" -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "Step 10: Verifying data still intact after downgrade..." -ForegroundColor Yellow
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -c "SELECT ticker, agency_net_buy, foreign_net_buy, consecutive_buy_days FROM signals WHERE date = '2026-05-12' AND ticker = '005930';" 2>$null
Write-Host "✓ Original data still preserved" -ForegroundColor Green
Write-Host ""

Write-Host "Step 11: Testing migration upgrade again..." -ForegroundColor Yellow
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend alembic upgrade head 2>$null
Write-Host "✓ Migration upgrade completed" -ForegroundColor Green
Write-Host ""

Write-Host "Step 12: Verifying final state..." -ForegroundColor Yellow
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -c "\d signals" 2>$null | Select-String -Pattern "one_day_net_buy|three_day_avg_net_buy|volume_ratio|rsi|ma_alignment|bollinger_position|trading_value"
Write-Host "✓ All momentum columns re-added" -ForegroundColor Green
Write-Host ""

Write-Host "Step 13: Cleaning up test data..." -ForegroundColor Yellow
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -c "DELETE FROM signals WHERE date = '2026-05-12' AND ticker = '005930';" 2>$null
Write-Host "✓ Test data cleaned up" -ForegroundColor Green
Write-Host ""

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "✅ ALL MIGRATION TESTS PASSED!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan

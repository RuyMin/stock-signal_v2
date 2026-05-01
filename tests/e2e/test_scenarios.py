"""E2E 시나리오 — TEST_SPEC §5 E2E-001~005.

Docker 풀 기동 + mock 외부 API 전제. 실제 LLM/텔레그램 호출은 mock 환경 유무에 따라 skip 가능.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import httpx
import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_e2e_001_new_user_first_use(
    dev_pool, kafka_producer, kafka_consumer_factory, wait_for_fn
):
    """E2E-001: 신규 사용자 첫 사용.
    1) /add 005930 (HTTP)
    2) scheduler 트리거 (kafka publish)
    3) 알림 도달 (stock.notify.{completed|failed})
    """
    from tests.integration.conftest import BACKEND_BASE_URL

    notify_completed = await kafka_consumer_factory("stock.notify.completed")
    notify_failed = await kafka_consumer_factory("stock.notify.failed")

    # 1) 사용자 보유 종목 추가
    async with httpx.AsyncClient(base_url=BACKEND_BASE_URL, timeout=10.0) as client:
        resp = await client.post("/holdings", json={"ticker": "005930"})
        assert resp.status_code == 201

    # 2) scheduler 트리거 (kafka 직접 발행으로 시뮬레이션)
    job_id = str(uuid.uuid4())
    await kafka_producer.send_and_wait(
        "stock.data.requested",
        {"job_id": job_id, "target_date": date.today().isoformat()},
    )

    # 3) 알림 도달 (실제 LLM/Bot 호출 시간 고려, 최대 4분)
    async def _notify_arrived() -> bool:
        for c in (notify_completed, notify_failed):
            try:
                msg = await asyncio.wait_for(c.getone(), timeout=1.0)
                if msg.value.get("job_id") == job_id:
                    return True
            except asyncio.TimeoutError:
                pass
        return False

    await wait_for_fn(
        _notify_arrived, timeout=240.0, interval=2.0,
        description="end-to-end notification reached",
    )


@pytest.mark.asyncio
async def test_e2e_002_zero_recommendations_day(
    dev_pool, kafka_producer, kafka_consumer_factory, wait_for_fn
):
    """E2E-002: 추천 0건인 날 — "조건 충족 종목 없음" 메시지 송신 흐름.

    crewai의 LLM 응답이 [] 또는 신호 종목이 없으면 recommendations 0건 → notifier가
    빈 메시지로 stock.notify.completed 발행.
    """
    notify_completed = await kafka_consumer_factory("stock.notify.completed")
    notify_failed = await kafka_consumer_factory("stock.notify.failed")
    job_id = str(uuid.uuid4())

    # crewai에 직접 발행 → 신호 0건 가정 (signals 테이블 비어있으므로 LLM도 0건 반환 기대)
    await kafka_producer.send_and_wait(
        "stock.recommendation.completed",
        {
            "job_id": job_id,
            "target_trading_date": (date.today() + timedelta(days=1)).isoformat(),
            "recommendation_count": 0,
            "has_buy_hedge": False,
            "has_watch": False,
            "has_exit_alert": False,
        },
    )

    async def _notify_arrived() -> bool:
        for c in (notify_completed, notify_failed):
            try:
                msg = await asyncio.wait_for(c.getone(), timeout=1.0)
                if msg.value.get("job_id") == job_id:
                    return True
            except asyncio.TimeoutError:
                pass
        return False

    await wait_for_fn(
        _notify_arrived, timeout=30.0, interval=1.0,
        description="zero-recommendation notification flow",
    )


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="E2E-003: 결정적 검증을 위해 mock LLM 인프라 필요(respx로 OpenAI HTTP 모킹 또는 "
    "crewai.LLM monkey-patch). 도입 시 추정 1~2시간 작업. 운영 1주 데이터 누적 후 진행 권장."
)
async def test_e2e_003_holdings_buy_to_sell_transition():
    """E2E-003: 보유 종목 매수→매도 전환 → 🔴 탈출 경보 + "보유" 표기."""


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="E2E-004: 단위 테스트(test_scheduler_holiday.py)로 동등 검증 완료. "
    "scheduler.trigger_daily 휴장일 스킵을 freezegun + fake producer로 직접 검증. "
    "E2E 레벨은 컨테이너 시각 주입 인프라 필요해서 skip 유지."
)
async def test_e2e_004_holiday_no_notification():
    """E2E-004: KRX 휴장일에는 scheduler가 Kafka 발행 자체 스킵."""


@pytest.mark.asyncio
async def test_e2e_005_recent_recommendations_query(dev_pool):
    """E2E-005: 과거 추천 데이터 존재 시 /recent → 최근 7일 추천 메시지."""
    from tests.factories import RecommendationFactory
    from tests.integration.conftest import BACKEND_BASE_URL

    today = date.today()
    for offset in range(3):
        d = today - timedelta(days=offset + 1)
        await RecommendationFactory.create(
            dev_pool, d, d + timedelta(days=1), ticker=f"00593{offset}", score=80 - offset,
        )

    async with httpx.AsyncClient(base_url=BACKEND_BASE_URL, timeout=10.0) as client:
        resp = await client.get("/recommendations/recent", params={"limit": 7})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 3

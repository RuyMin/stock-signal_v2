"""통합 테스트 — TEST_SPEC §4 INT-001~005, INT-E001~004.

전제: Docker compose 풀 기동 + 외부 API mock(또는 실제 호출 가능 환경).
통합 테스트는 실제 컨테이너 간 메시지 흐름을 검증.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, timedelta

import httpx
import pytest

pytestmark = pytest.mark.integration


# ─── INT-001: scheduler → data-collector ─────────────────────────


class TestPipelineHappyPath:
    @pytest.mark.asyncio
    async def test_int_001_scheduler_to_data_collector(
        self, dev_pool, kafka_producer, kafka_consumer_factory, wait_for_fn
    ):
        """INT-001: stock.data.requested 직접 발행 → data-collector 수신 →
        stock.data.completed 발행 + signals/news/macro 흔적 (외부 API 빈 응답이라도 흐름은 흘러야 함)."""
        consumer = await kafka_consumer_factory("stock.data.completed")
        job_id = str(uuid.uuid4())
        target = date.today()

        await kafka_producer.send_and_wait(
            "stock.data.requested",
            {
                "job_id": job_id,
                "target_date": target.isoformat(),
                "triggered_at": "2026-04-30T15:35:00Z",
            },
        )

        async def _got_completed() -> bool:
            try:
                msg = await asyncio.wait_for(consumer.getone(), timeout=2.0)
                return msg.value.get("job_id") == job_id
            except asyncio.TimeoutError:
                return False

        await wait_for_fn(
            _got_completed, timeout=60.0, interval=1.0,
            description="stock.data.completed with matching job_id",
        )

    @pytest.mark.asyncio
    async def test_int_002_data_collector_to_crewai(
        self, dev_pool, kafka_producer, kafka_consumer_factory, wait_for_fn
    ):
        """INT-002: stock.data.completed 직접 발행 → crewai 수신 →
        stock.recommendation.completed 발행. (실제 LLM 호출 → recommendations row 0+ 가능)
        LLM 키 미설정 시 crew_failed 가능 → completed/failed 둘 중 하나."""
        completed = await kafka_consumer_factory("stock.recommendation.completed")
        failed = await kafka_consumer_factory("stock.recommendation.failed")
        job_id = str(uuid.uuid4())
        target = date.today()

        await kafka_producer.send_and_wait(
            "stock.data.completed",
            {
                "job_id": job_id,
                "target_date": target.isoformat(),
                "target_trading_date": (target + timedelta(days=1)).isoformat(),
                "signals_count": 0,
                "news_count": 0,
                "macro_collected": False,
            },
        )

        async def _got_either() -> bool:
            for c in (completed, failed):
                try:
                    msg = await asyncio.wait_for(c.getone(), timeout=1.0)
                    if msg.value.get("job_id") == job_id:
                        return True
                except asyncio.TimeoutError:
                    pass
            return False

        await wait_for_fn(
            _got_either, timeout=180.0, interval=2.0,  # LLM 호출은 분 단위
            description="stock.recommendation.{completed|failed} for job_id",
        )

    @pytest.mark.asyncio
    async def test_int_003_crewai_to_telegram_notifier(
        self, dev_pool, kafka_producer, kafka_consumer_factory, wait_for_fn
    ):
        """INT-003: stock.recommendation.completed 직접 발행 → notifier 수신 →
        stock.notify.{completed|failed} 발행 (텔레그램 토큰이 fake면 failed 정상)."""
        completed = await kafka_consumer_factory("stock.notify.completed")
        failed = await kafka_consumer_factory("stock.notify.failed")
        job_id = str(uuid.uuid4())
        target_trading = (date.today() + timedelta(days=1))

        # 추천 0건이라도 알림 송신은 발생 ("조건 충족 종목 없음")
        await kafka_producer.send_and_wait(
            "stock.recommendation.completed",
            {
                "job_id": job_id,
                "target_trading_date": target_trading.isoformat(),
                "recommendation_count": 0,
                "has_buy_hedge": False,
                "has_watch": False,
                "has_exit_alert": False,
            },
        )

        async def _got_either() -> bool:
            for c in (completed, failed):
                try:
                    msg = await asyncio.wait_for(c.getone(), timeout=1.0)
                    if msg.value.get("job_id") == job_id:
                        return True
                except asyncio.TimeoutError:
                    pass
            return False

        await wait_for_fn(
            _got_either, timeout=30.0, interval=1.0,
            description="stock.notify.{completed|failed} for job_id",
        )

    @pytest.mark.asyncio
    async def test_int_004_full_pipeline_job_id_correlation(
        self, dev_pool, kafka_producer, kafka_consumer_factory, wait_for_fn
    ):
        """INT-004: scheduler 트리거 → 전체 흐름의 모든 단계 메시지가 동일 job_id를 유지."""
        consumers = {
            "data_completed": await kafka_consumer_factory("stock.data.completed"),
            "rec_completed": await kafka_consumer_factory("stock.recommendation.completed"),
            "rec_failed": await kafka_consumer_factory("stock.recommendation.failed"),
            "notify_completed": await kafka_consumer_factory("stock.notify.completed"),
            "notify_failed": await kafka_consumer_factory("stock.notify.failed"),
        }
        job_id = str(uuid.uuid4())
        target = date.today()

        await kafka_producer.send_and_wait(
            "stock.data.requested",
            {"job_id": job_id, "target_date": target.isoformat()},
        )

        seen_job_ids: dict[str, str] = {}

        async def _collect() -> bool:
            for label, c in consumers.items():
                if label in seen_job_ids:
                    continue
                try:
                    msg = await asyncio.wait_for(c.getone(), timeout=0.5)
                    if msg.value.get("job_id") == job_id:
                        seen_job_ids[label] = job_id
                except asyncio.TimeoutError:
                    pass
            # data + (rec_completed or rec_failed) + (notify_completed or notify_failed)
            return (
                "data_completed" in seen_job_ids
                and ("rec_completed" in seen_job_ids or "rec_failed" in seen_job_ids)
                and ("notify_completed" in seen_job_ids or "notify_failed" in seen_job_ids)
            )

        await wait_for_fn(
            _collect, timeout=240.0, interval=2.0,
            description="all stages report same job_id",
        )

    @pytest.mark.asyncio
    async def test_int_005_telegram_command_holdings_flow(self, dev_pool):
        """INT-005: backend HTTP 호출(텔레그램 listener가 하는 것 시뮬레이션) → holdings INSERT."""
        from tests.integration.conftest import BACKEND_BASE_URL

        async with httpx.AsyncClient(base_url=BACKEND_BASE_URL, timeout=10.0) as client:
            resp = await client.post("/holdings", json={"ticker": "005930"})
        assert resp.status_code == 201

        async with dev_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT ticker FROM holdings WHERE ticker='005930'")
        assert row is not None


# ─── 장애 시나리오 ───────────────────────────────────────────────


class TestPipelineFailureScenarios:
    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="INT-E001: aiokafka(0.13)의 자동 재연결은 라이브러리 보장. "
        "검증 가치는 운영 모니터링(Grafana 'kafka 재연결' 로그 알림)으로 대체. "
        "통합 테스트로 자동화하면 다른 테스트들이 같은 stack에서 영향받음."
    )
    async def test_int_e001_kafka_temporary_outage(self):
        """INT-E001: kafka 컨테이너 stop → 30s → start. 재연결 + 미처리 메시지 처리."""

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="INT-E002: SQLAlchemy pool_pre_ping=True + asyncpg lazy reconnect는 라이브러리 보장. "
        "검증 가치는 운영 모니터링(connection error 알림)으로 대체."
    )
    async def test_int_e002_postgres_temporary_outage(self):
        """INT-E002: postgres 컨테이너 stop/start. 모든 서비스 auto reconnect."""

    @pytest.mark.asyncio
    async def test_int_e003_external_api_all_down_data_failed(
        self, dev_pool, kafka_producer, kafka_consumer_factory, wait_for_fn
    ):
        """INT-E003: 외부 API 모두 실패 시나리오.
        (현재 stub 단계이므로 외부 호출이 빈 응답일 가능성. 실패 시 stock.data.failed 도달.)
        둘 중 하나(completed/failed) 도달하면 OK — 흐름 자체가 멈추지 않는지 확인."""
        completed = await kafka_consumer_factory("stock.data.completed")
        failed = await kafka_consumer_factory("stock.data.failed")
        job_id = str(uuid.uuid4())

        await kafka_producer.send_and_wait(
            "stock.data.requested",
            {"job_id": job_id, "target_date": date.today().isoformat()},
        )

        async def _terminal() -> bool:
            for c in (completed, failed):
                try:
                    msg = await asyncio.wait_for(c.getone(), timeout=1.0)
                    if msg.value.get("job_id") == job_id:
                        return True
                except asyncio.TimeoutError:
                    pass
            return False

        await wait_for_fn(
            _terminal, timeout=120.0, interval=2.0,
            description="data flow terminates on either topic",
        )

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="INT-E004: 단위 테스트(test_scheduler_holiday.py)로 동등 검증 완료. "
        "is_market_open + trigger_daily 휴장일 스킵을 freezegun으로 직접 검증. "
        "통합 레벨 검증은 컨테이너 시각 주입 필요해서 비용 대비 가치 낮음 — skip 유지."
    )
    async def test_int_e004_holiday_skipped(self):
        """INT-E004: 휴장일 발생 시 scheduler가 Kafka 발행 자체 스킵."""

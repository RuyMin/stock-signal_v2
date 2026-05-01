"""공통 헤더 검증 — TEST_SPEC API-H001 ~ API-H003."""
import pytest


class TestCommonHeaders:
    @pytest.mark.asyncio
    async def test_api_h001_request_id_auto_generated(self, api_client, db_pool):
        """API-H001: X-Request-ID 없이 요청 → 서버가 자동 생성하여 응답 헤더 포함."""
        resp = await api_client.get("/health")
        assert "x-request-id" in {k.lower() for k in resp.headers.keys()}
        # UUID 형식 확인
        rid = resp.headers["x-request-id"]
        assert len(rid) >= 8

    @pytest.mark.asyncio
    async def test_api_h002_request_id_echoed(self, api_client, db_pool):
        """API-H002: X-Request-ID 포함 → 동일 값 에코."""
        custom = "test-request-id-abc-123"
        resp = await api_client.get("/health", headers={"X-Request-ID": custom})
        assert resp.headers["x-request-id"] == custom

    @pytest.mark.asyncio
    async def test_api_h003_response_time_present(self, api_client, db_pool):
        """API-H003: 응답에 X-Response-Time(ms 정수) 포함."""
        resp = await api_client.get("/health")
        rt = resp.headers.get("x-response-time")
        assert rt is not None
        assert rt.isdigit()

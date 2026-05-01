"""GET /health — TEST_SPEC API-011."""
import pytest


class TestHealth:
    @pytest.mark.asyncio
    async def test_api_011_health_ok(self, api_client):
        """API-011: 헬스체크 → 200 + {"status":"ok"}"""
        resp = await api_client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

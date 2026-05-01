"""GET /jobs/{job_id} — TEST_SPEC API-010, API-E010, API-E011."""
import pytest


class TestGetJob:
    @pytest.mark.asyncio
    async def test_api_010_existing_job(self, api_client, db_pool):
        """API-010: 존재하는 job → 200 + JobStatusResponse."""
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO jobs (job_type, status, progress) "
                "VALUES ('stock-recommendation', 'completed', 100) "
                "RETURNING id"
            )
            job_id = str(row["id"])

        resp = await api_client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == job_id
        assert body["status"] == "completed"
        assert body["progress"] == 100

    @pytest.mark.asyncio
    async def test_api_e010_nonexistent_job(self, api_client, db_pool):
        """API-E010: 존재하지 않는 UUID → 404 JOB_NOT_FOUND."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await api_client.get(f"/jobs/{fake_id}")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "JOB_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_api_e011_invalid_uuid_format(self, api_client, db_pool):
        """API-E011: 잘못된 UUID 형식 → 400 INVALID_REQUEST."""
        resp = await api_client.get("/jobs/not-a-uuid")
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "INVALID_REQUEST"

"""Backend FastAPI HTTP 클라이언트 (multi-user 대응).

명령어 핸들러가 사용. 모든 도메인 메서드는 chat_id를 받아서 backend에 전달.
모든 메서드는 (status_code, json_body) 반환.
"""
import os
import uuid
from typing import Any, Optional

import httpx

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")


class BackendClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=BACKEND_URL, timeout=httpx.Timeout(10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"X-Request-ID": str(uuid.uuid4())}

    # ─── users ──────────────────────────────────────────────

    async def register_user(
        self, chat_id: int, telegram_username: Optional[str] = None
    ) -> tuple[int, dict[str, Any]]:
        resp = await self._client.post(
            "/users/register",
            json={"chat_id": chat_id, "telegram_username": telegram_username},
            headers=self._headers(),
        )
        return resp.status_code, _safe_json(resp)

    async def get_user(self, chat_id: int) -> tuple[int, dict[str, Any]]:
        resp = await self._client.get(
            f"/users/by-chat-id/{chat_id}", headers=self._headers()
        )
        return resp.status_code, _safe_json(resp)

    async def approve_user(
        self, target_chat_id: int, approved_by_chat_id: int
    ) -> tuple[int, dict[str, Any]]:
        resp = await self._client.post(
            f"/users/{target_chat_id}/approve",
            json={"approved_by_chat_id": approved_by_chat_id},
            headers=self._headers(),
        )
        return resp.status_code, _safe_json(resp)

    async def list_users(self) -> tuple[int, dict[str, Any]]:
        resp = await self._client.get("/users", headers=self._headers())
        return resp.status_code, _safe_json(resp)

    # ─── holdings (사용자별, chat_id 필수) ─────────────────────

    async def add_holding(
        self, ticker: str, chat_id: int
    ) -> tuple[int, dict[str, Any]]:
        resp = await self._client.post(
            "/holdings",
            json={"ticker": ticker, "chat_id": chat_id},
            headers=self._headers(),
        )
        return resp.status_code, _safe_json(resp)

    async def remove_holding(
        self, ticker: str, chat_id: int
    ) -> tuple[int, dict[str, Any]]:
        resp = await self._client.delete(
            f"/holdings/{ticker}",
            params={"chat_id": chat_id},
            headers=self._headers(),
        )
        return resp.status_code, _safe_json(resp)

    async def list_holdings(self, chat_id: int) -> tuple[int, dict[str, Any]]:
        resp = await self._client.get(
            "/holdings", params={"chat_id": chat_id}, headers=self._headers()
        )
        return resp.status_code, _safe_json(resp)

    # ─── recommendations (시장 공통, chat_id 무관) ─────────────

    async def get_recent_recommendations(
        self, limit: int = 7
    ) -> tuple[int, dict[str, Any]]:
        resp = await self._client.get(
            "/recommendations/recent",
            params={"limit": limit},
            headers=self._headers(),
        )
        return resp.status_code, _safe_json(resp)

    async def get_recommendations_by_date(
        self, date_str: str
    ) -> tuple[int, dict[str, Any]]:
        resp = await self._client.get(
            "/recommendations", params={"date": date_str}, headers=self._headers()
        )
        return resp.status_code, _safe_json(resp)


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    if not resp.content:
        return {}
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}

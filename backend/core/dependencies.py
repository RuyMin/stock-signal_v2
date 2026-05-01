"""FastAPI 미들웨어 및 Depends 헬퍼.

API_CONTRACT_SKILL.md 표준:
- 모든 응답에 X-Request-ID 에코
- 모든 응답에 X-Response-Time (ms)
- 요청에서 X-Request-ID 헤더 추출 (없으면 자동 생성)

순수 ASGI 미들웨어 — BaseHTTPMiddleware는 starlette/anyio TaskGroup과 FastAPI 예외 핸들러
조합에서 ExceptionGroup으로 감싸지는 알려진 이슈가 있어 회피.
"""
import time
import uuid
from typing import Awaitable, Callable

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class ResponseHeadersMiddleware:
    """순수 ASGI 미들웨어. send 래퍼로 응답 시작 시 헤더 주입."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extract_or_create_request_id(scope)
        # 라우터/예외 핸들러에서 참조 가능하도록 scope.state에 저장
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        start = time.time()

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                elapsed_ms = int((time.time() - start) * 1000)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                headers.append((b"x-response-time", str(elapsed_ms).encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _extract_or_create_request_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == b"x-request-id":
            try:
                return value.decode("latin-1")
            except UnicodeDecodeError:
                break
    return str(uuid.uuid4())


def get_request_id(request: Request) -> str:
    """라우터에서 request_id를 Depends로 추출."""
    state = getattr(request, "state", None)
    if state is not None and hasattr(state, "request_id"):
        return state.request_id
    return str(uuid.uuid4())


# 하위 호환 — main.py가 add_response_headers 이름으로 import 중인 경우를 위한 alias
add_response_headers = ResponseHeadersMiddleware

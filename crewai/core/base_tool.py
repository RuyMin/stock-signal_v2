"""VibeBaseTool — CrewAI BaseTool의 얇은 래퍼.

CREWAI_TOOL_SKILL.md 규칙:
- args_schema 필수 (Pydantic)
- 예외를 raise하지 않고 문자열로 반환
- 접두사: success: | error:file_not_found: | error:permission: | error:timeout: | error:unknown:
- DB 쓰기 금지 (READ only)

NOTE: CrewAI 1.x에서 BaseTool이 crewai.tools 모듈로 이전.
0.30.x는 crewai_tools.BaseTool 사용. 본 프로젝트는 2026-04-30에 1.14.3로 업그레이드 —
0.30/crewai-tools 0.2.6 + pydantic 2.7 조합에서 args_schema V1 validator가 V2 BaseModel을
서브클래스로 인식 못 하는 호환성 버그를 회피하기 위함.
"""
from crewai.tools import BaseTool


class VibeBaseTool(BaseTool):
    """모든 stock-signal Tool의 부모. 표준 에러 접두사를 강제."""

    @staticmethod
    def ok(payload: str) -> str:
        return f"success: {payload}"

    @staticmethod
    def err_unknown(detail: str) -> str:
        return f"error:unknown: {detail}"

    @staticmethod
    def err_not_found(detail: str) -> str:
        return f"error:file_not_found: {detail}"

    @staticmethod
    def err_timeout(detail: str) -> str:
        return f"error:timeout: {detail}"

"""BaseAgent — Vibe Framework Agent의 부모.

CREWAI_SKILL.md §base_agent.py 단순화 (stock-signal 적용):
- Supabase Realtime 등 미사용 부분 제거
- structlog 자동 로그
"""
from typing import Sequence

import structlog
from crewai import Agent

logger = structlog.get_logger()


class BaseAgent:
    """직접 인스턴스화 금지 — 반드시 상속."""

    role: str = NotImplemented
    goal: str = NotImplemented
    backstory: str = NotImplemented
    tools: Sequence = ()
    verbose: bool = True
    allow_delegation: bool = False

    def build(self) -> Agent:
        if self.role is NotImplemented or self.goal is NotImplemented:
            raise NotImplementedError("Agent must define role/goal/backstory")
        logger.info("agent_built", role=self.role, tools=[t.name for t in self.tools])
        return Agent(
            role=self.role,
            goal=self.goal,
            backstory=self.backstory,
            tools=list(self.tools),
            verbose=self.verbose,
            allow_delegation=self.allow_delegation,
        )

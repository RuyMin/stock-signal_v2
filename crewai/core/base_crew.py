"""BaseCrew — Vibe Framework Crew의 부모.

CREWAI_SKILL.md §base_crew.py 기반, stock-signal에 맞게 단순화:
- MinIO 체크포인트 / Supabase Realtime 푸시 제거
- jobs 테이블 update / Kafka DLQ 발행은 main.py가 처리 (Crew 외부)
- on_complete() 훅에서 결과 저장 (Subclass override)
- kickoff()는 동기. main.py가 asyncio.to_thread(crew.kickoff, inputs)으로 호출.
"""
import uuid
from typing import Any, Optional

import structlog
from crewai import Crew, Process

logger = structlog.get_logger()


class BaseCrew:
    crew_name: str = NotImplemented
    version: str = "1.0.0"
    process: Process = Process.sequential

    def __init__(self, job_id: Optional[str] = None) -> None:
        if self.crew_name is NotImplemented:
            raise NotImplementedError("Crew must define crew_name")
        self.job_id = job_id or str(uuid.uuid4())

    # ── Subclass에서 반드시 구현 ─────────────────────────

    def setup_agents(self) -> list:
        raise NotImplementedError

    def setup_tasks(self, agents: list) -> list:
        raise NotImplementedError

    # ── Subclass에서 선택 구현 (post-processing) ──────────

    def on_complete(self, raw_result: Any, inputs: dict) -> dict:
        """Crew kickoff 결과 후처리. PG 저장, 카운트 산출 등.
        반환값이 main.py로 전달되어 Kafka completed 이벤트의 페이로드가 됨.
        """
        return {"raw_result": str(raw_result)}

    # ── 진입점 (동기) ─────────────────────────────────────

    def kickoff(self, inputs: dict) -> dict:
        structlog.contextvars.bind_contextvars(job_id=self.job_id, crew=self.crew_name)
        logger.info("crew_started", inputs_keys=list(inputs.keys()))
        try:
            agents = self.setup_agents()
            tasks = self.setup_tasks(agents)

            crew = Crew(
                agents=agents,
                tasks=tasks,
                process=self.process,
                verbose=True,
            )
            raw_result = crew.kickoff(inputs=inputs)
            output = self.on_complete(raw_result, inputs)

            logger.info("crew_completed", **{k: v for k, v in output.items() if isinstance(v, (int, str, bool))})
            return output
        except Exception as exc:  # noqa: BLE001
            logger.error("crew_failed", error=str(exc), exc_info=True)
            raise
        finally:
            structlog.contextvars.unbind_contextvars("job_id", "crew")

---
name: vibe-framework-crewai
description: >
  Vibe Framework의 CrewAI 표준 패턴 정의.
  Agent, Task, Crew 생성 시 반드시 참조. Base 클래스 상속 없이
  CrewAI 코드를 작성하는 것은 절대 금지. 모든 CrewAI 관련 코드 작성 시 이 문서를 먼저 읽을 것.
---

# CrewAI Skill

## 핵심 원칙

1. `crewai/core/` 의 Base 클래스를 **반드시 상속**한다
2. `crewai/core/` 내 파일은 **절대 수정하지 않는다**
3. 재사용 가능한 Tool은 `crewai/tools/` 에 추가한다
4. 프로젝트별 Crew는 `crewai/crews/{crew_name}/` 에만 작성한다
5. 새 Crew 작성 순서: `agents.py` → `tasks.py` → `crew.py`

---

## Base 클래스 정의

### base_agent.py

```python
# crewai/core/base_agent.py
from crewai import Agent
from abc import abstractmethod
from typing import List, Optional
import logging
from datetime import datetime
from .db import get_db
from .models import AgentLog

logger = logging.getLogger(__name__)

class BaseAgent:
    """
    모든 Vibe Framework Agent의 부모 클래스.
    직접 인스턴스화 금지 — 반드시 상속하여 사용.
    """

    # 하위 클래스에서 반드시 정의
    role: str = NotImplemented
    goal: str = NotImplemented
    backstory: str = NotImplemented
    tools: List = []
    verbose: bool = True
    allow_delegation: bool = False

    def build(self) -> Agent:
        """CrewAI Agent 인스턴스 반환. 프레임워크가 자동으로 로깅/에러처리 주입."""
        return Agent(
            role=self.role,
            goal=self.goal,
            backstory=self.backstory,
            tools=self.tools,
            verbose=self.verbose,
            allow_delegation=self.allow_delegation,
            callbacks=[self._on_step_complete, self._on_error],
        )

    def _on_step_complete(self, output: str, **kwargs):
        """Agent 스텝 완료 시 자동 로깅."""
        logger.info(f"[{self.role}] Step complete: {output[:100]}...")

    def _on_error(self, error: Exception, **kwargs):
        """Agent 에러 시 자동 기록 및 Kafka 재시도 이벤트 발행."""
        logger.error(f"[{self.role}] Error: {str(error)}")
        # Kafka Dead Letter Queue 발행은 BaseCrew에서 처리
        raise error
```

### base_task.py

```python
# crewai/core/base_task.py
from crewai import Task
from abc import abstractmethod
from typing import Optional, List

class BaseTask:
    """
    모든 Vibe Framework Task의 부모 클래스.
    """

    # 하위 클래스에서 반드시 정의
    description: str = NotImplemented
    expected_output: str = NotImplemented

    def build(self, agent, context: Optional[List[Task]] = None) -> Task:
        return Task(
            description=self.description,
            expected_output=self.expected_output,
            agent=agent,
            context=context or [],
        )
```

### base_crew.py

```python
# crewai/core/base_crew.py
from crewai import Crew, Process
from abc import abstractmethod
from typing import Optional
import uuid
from datetime import datetime
from .db import get_db
from .models import Job
from .kafka import publish_event
from .minio import save_checkpoint
import logging

logger = logging.getLogger(__name__)

class BaseCrew:
    """
    모든 Vibe Framework Crew의 부모 클래스.
    자동 처리 항목:
    - job_id 관리
    - 상태 PostgreSQL 기록
    - 중간 산출물 MinIO 저장
    - 실패 시 Kafka DLQ 발행
    - 완료 시 Supabase Realtime 푸시
    """

    # 하위 클래스에서 반드시 정의
    crew_name: str = NotImplemented
    version: str = "1.0.0"
    process: Process = Process.sequential

    def __init__(self, job_id: Optional[str] = None):
        self.job_id = job_id or str(uuid.uuid4())
        self._agents = []
        self._tasks = []

    @abstractmethod
    def setup_agents(self) -> list:
        """Agent 인스턴스 목록 반환. 하위 클래스에서 구현."""
        pass

    @abstractmethod
    def setup_tasks(self, agents: list) -> list:
        """Task 인스턴스 목록 반환. 하위 클래스에서 구현."""
        pass

    async def kickoff(self, inputs: dict) -> dict:
        """Crew 실행 진입점. 직접 오버라이드 금지."""
        await self._update_job_status("processing", progress=0)
        try:
            agents = self.setup_agents()
            tasks = self.setup_tasks(agents)
            crew = Crew(
                agents=agents,
                tasks=tasks,
                process=self.process,
                verbose=True,
            )
            result = crew.kickoff(inputs=inputs)
            await self._update_job_status("completed", progress=100, result=result)
            await self._push_realtime_event("completed", result)
            return {"job_id": self.job_id, "status": "completed", "result": result}

        except Exception as e:
            logger.error(f"[{self.crew_name}] Crew failed: {str(e)}")
            await self._update_job_status("failed", error=str(e))
            await self._publish_dlq(inputs, str(e))
            raise

    async def _update_job_status(self, status: str, progress: int = 0,
                                   result=None, error: str = None):
        # PostgreSQL job 상태 업데이트
        pass  # 구현체는 core/db.py 참조

    async def _push_realtime_event(self, event: str, data):
        # Supabase Realtime 푸시
        pass  # 구현체는 core/realtime.py 참조

    async def _publish_dlq(self, inputs: dict, error: str):
        # Kafka Dead Letter Queue 발행
        await publish_event(
            topic=f"{self.crew_name}.process.failed",
            payload={"job_id": self.job_id, "inputs": inputs, "error": error}
        )
```

---

## 새 Crew 작성 패턴

### 1. agents.py

```python
# crewai/crews/{crew_name}/agents.py
from crewai.core.base_agent import BaseAgent
from crewai.tools.video_tools import VideoAnalyzerTool
from crewai.tools.storage_tools import MinIODownloadTool

class VideoAnalyzerAgent(BaseAgent):
    role = "영상 분석 전문가"
    goal = "원본 영상의 핵심 장면, 오디오, 텍스트를 정확히 분석한다"
    backstory = "10년 경력의 영상 편집자로, 콘텐츠의 핵심을 빠르게 파악하는 전문가"
    tools = [VideoAnalyzerTool(), MinIODownloadTool()]

class ContentStrategistAgent(BaseAgent):
    role = "콘텐츠 전략가"
    goal = "협찬 가이드라인을 분석하고 최적의 릴스 전략을 수립한다"
    backstory = "브랜드 마케팅과 SNS 바이럴 콘텐츠 전문가"
    tools = []
    allow_delegation = True  # 다른 Agent에게 위임 가능

# ... 추가 Agent
```

### 2. tasks.py

```python
# crewai/crews/{crew_name}/tasks.py
from crewai.core.base_task import BaseTask

class VideoAnalysisTask(BaseTask):
    description = """
    MinIO에서 job_id={job_id}의 원본 영상을 다운로드하여 분석하라.
    분석 항목:
    1. 주요 장면 타임스탬프 목록
    2. 음성 텍스트 (Whisper 활용)
    3. 핵심 메시지 및 분위기
    결과는 구조화된 JSON으로 반환할 것.
    """
    expected_output = "장면 분석 결과 JSON (timestamps, transcript, key_messages)"

class ScriptWritingTask(BaseTask):
    description = """
    이전 분석 결과와 협찬 가이드라인을 바탕으로 릴스 스크립트를 작성하라.
    요구사항:
    - 최대 60초 분량
    - 협찬 가이드라인 100% 준수
    - 훅(Hook) → 본문 → CTA 구조
    """
    expected_output = "릴스 스크립트 (타임라인 포함)"

# ... 추가 Task
```

### 3. crew.py

```python
# crewai/crews/{crew_name}/crew.py
from crewai.core.base_crew import BaseCrew
from crewai import Process
from .agents import VideoAnalyzerAgent, ContentStrategistAgent
from .tasks import VideoAnalysisTask, ScriptWritingTask

class ReelsGenerationCrew(BaseCrew):
    crew_name = "reels-generation"
    version = "1.0.0"
    process = Process.sequential  # 순서 중요한 경우 sequential

    def setup_agents(self):
        return [
            VideoAnalyzerAgent().build(),
            ContentStrategistAgent().build(),
            # ScriptWriterAgent().build(),
            # VideoEditorAgent().build(),
            # QAReviewerAgent().build(),
        ]

    def setup_tasks(self, agents):
        analyzer, strategist = agents[0], agents[1]
        
        analysis_task = VideoAnalysisTask().build(agent=analyzer)
        script_task = ScriptWritingTask().build(
            agent=strategist,
            context=[analysis_task]  # 이전 Task 결과를 컨텍스트로 전달
        )
        return [analysis_task, script_task]
```

---

## Kafka Consumer 연동 패턴

```python
# crewai/main.py — Crew를 Kafka Consumer로 실행
from kafka import KafkaConsumer
import json
import asyncio
from crews.reels_generation.crew import ReelsGenerationCrew

consumer = KafkaConsumer(
    'reels.generation.requested',
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_deserializer=lambda m: json.loads(m.decode('utf-8'))
)

async def process_message(message):
    job_id = message['job_id']
    crew = ReelsGenerationCrew(job_id=job_id)
    await crew.kickoff(inputs=message['inputs'])

for message in consumer:
    asyncio.run(process_message(message.value))
```

---

## 금지 패턴

```python
# ❌ Base 클래스 상속 없이 직접 생성
agent = Agent(role="분석가", goal="...", backstory="...")

# ❌ Crew에서 직접 API 호출
class MyCrew(BaseCrew):
    def kickoff(self, inputs):
        response = openai.chat.complete(...)  # 금지

# ❌ core/ 파일 수정
# base_agent.py, base_task.py, base_crew.py 는 절대 수정 금지

# ✅ 올바른 패턴
class MyAgent(BaseAgent):
    role = "..."
    goal = "..."
    backstory = "..."
    tools = [MyCustomTool()]
```

---

## Tool 작성 규칙

Tool 작성 규칙은 `crewai/CREWAI_TOOL_SKILL.md` 참조.

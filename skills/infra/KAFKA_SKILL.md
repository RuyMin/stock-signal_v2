---
name: vibe-framework-kafka
description: >
  Vibe Framework의 Kafka 토픽 설계 및 Consumer/Producer 사용 규칙.
  Kafka 토픽 추가, Consumer 작성, 메시지 스키마 정의 시 참조.
---

# Kafka Skill

## 토픽 네이밍 규칙

```
{서비스}.{리소스}.{이벤트}

예시:
reels.generation.requested    # 릴스 생성 요청
reels.generation.completed    # 릴스 생성 완료
reels.generation.failed       # 릴스 생성 실패 (DLQ)
video.analysis.requested
video.analysis.completed
```

모든 서비스는 `.requested` / `.completed` / `.failed` 세 토픽을 기본으로 가진다.

---

## 메시지 스키마 표준

```python
# shared/schemas/events.py — 모든 Kafka 이벤트의 기본 구조
from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime

class KafkaEvent(BaseModel):
    """모든 Kafka 메시지의 기본 스키마. 반드시 상속하여 사용."""
    job_id: str
    timestamp: datetime = datetime.utcnow()
    version: str = "1.0"

class JobRequestedEvent(KafkaEvent):
    inputs: dict[str, Any]

class JobCompletedEvent(KafkaEvent):
    result_url: str
    metadata: Optional[dict] = None

class JobFailedEvent(KafkaEvent):
    error: str
    retry_count: int = 0
    max_retries: int = 3
```

---

## Worker Consumer 표준 패턴

```python
# workers/{name}/main.py
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
import json
import asyncio
import logging
from processor import process

logger = logging.getLogger(__name__)

TOPIC_IN  = "reels.generation.requested"
TOPIC_OUT = "reels.generation.completed"
TOPIC_ERR = "reels.generation.failed"

async def run():
    consumer = AIOKafkaConsumer(
        TOPIC_IN,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id="reels-generation-workers",
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest',
        enable_auto_commit=False,  # 수동 커밋으로 처리 보장
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    )

    await consumer.start()
    await producer.start()

    try:
        async for msg in consumer:
            event = msg.value
            job_id = event['job_id']
            try:
                result = await process(event)
                await producer.send(TOPIC_OUT, {
                    "job_id": job_id,
                    "result_url": result['url'],
                })
                await consumer.commit()  # 성공 시에만 커밋
            except Exception as e:
                logger.error(f"Job {job_id} failed: {e}")
                retry_count = event.get('retry_count', 0)
                if retry_count < 3:
                    # 재시도
                    await producer.send(TOPIC_IN, {
                        **event,
                        "retry_count": retry_count + 1
                    })
                else:
                    # DLQ로 이동
                    await producer.send(TOPIC_ERR, {
                        "job_id": job_id,
                        "error": str(e),
                        "retry_count": retry_count,
                    })
                await consumer.commit()
    finally:
        await consumer.stop()
        await producer.stop()

if __name__ == "__main__":
    asyncio.run(run())
```

---

## 재시도 정책

| 상황 | 처리 |
|------|------|
| 일시적 에러 (네트워크, 타임아웃) | 동일 토픽에 재발행, retry_count 증가 |
| retry_count >= 3 | `.failed` DLQ 토픽으로 이동 |
| 치명적 에러 (스키마 오류 등) | 즉시 DLQ, 재시도 없음 |

---

## infra/kafka/topics.yml

> 파티션 수 결정 기준과 Worker 스케일링 규칙 → `infra/PERFORMANCE_SKILL.md` 섹션 2 참조.
> 핵심: Worker 인스턴스 수 ≤ 파티션 수. 초과 Worker는 유휴 상태가 된다.

```yaml
topics:
  - name: reels.generation.requested
    partitions: 3                    # Worker 3개까지 병렬 처리 가능
    replication_factor: 1            # 단일 브로커이므로 1 고정
    config:
      retention.ms: 604800000        # 7일

  - name: reels.generation.completed
    partitions: 1                    # 완료 이벤트는 순서 불필요, 처리 빠름
    replication_factor: 1

  - name: reels.generation.failed
    partitions: 1                    # DLQ — 발생 빈도 낮음
    replication_factor: 1
    config:
      retention.ms: 2592000000       # 30일 (DLQ는 오래 보관)
```

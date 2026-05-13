"""Kafka consumer/producer 팩토리."""
import json
import os

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

KAFKA_BOOTSTRAP_SERVERS = os.environ["KAFKA_BOOTSTRAP_SERVERS"]


def _safe_deserialize(m: bytes) -> object:
    """디코드/JSON 실패 시 None 반환 — 컨슈머 루프가 poison-message로 skip+commit 처리.
    utf-8-sig로 디코드해 BOM 포함 메시지도 안전 처리.
    """
    try:
        return json.loads(m.decode("utf-8-sig"))
    except Exception:  # noqa: BLE001
        return None


def make_consumer(topic: str, group_id: str) -> AIOKafkaConsumer:
    return AIOKafkaConsumer(
        topic,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=group_id,
        value_deserializer=_safe_deserialize,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )


def make_producer() -> AIOKafkaProducer:
    return AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
    )

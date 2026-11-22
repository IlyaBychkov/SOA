import logging
import os
import random
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from confluent_kafka import KafkaError, KafkaException, Producer
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import contextmanager_in_threadpool
from google.protobuf.json_format import ParseDict
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.background import worker
from app.kafka_init import TOPIC, initialize_kafka
from proto.movie.v1 import movie_event_pb2 as pb

EventType = Literal[
    "VIEW_STARTED", "VIEW_FINISHED", "VIEW_PAUSED", "VIEW_RESUMED", "LIKED", "SEARCHED"
]
DeviceType = Literal["MOBILE", "DESKTOP", "TV", "TABLET"]
Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\x00]+$")]
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_TIMESTAMP = datetime(2299, 12, 31, 23, 59, 59, 999000, tzinfo=UTC)
logger = logging.getLogger("uvicorn.error")


class EventCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    user_id: Identifier
    movie_id: Identifier
    event_type: EventType
    timestamp: AwareDatetime = Field(ge=EPOCH, le=MAX_TIMESTAMP)
    device_type: DeviceType
    session_id: Identifier
    progress_seconds: int | None = Field(default=None, ge=0, le=2**32 - 1, strict=True)

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        is_view = self.event_type.startswith("VIEW_")
        if is_view and self.progress_seconds is None:
            raise ValueError("progress_seconds is required for viewing events")
        if not is_view and self.progress_seconds not in (None, 0):
            raise ValueError("progress_seconds must be zero for search and like events")
        return self

    def to_protobuf(self) -> bytes:
        payload = self.model_dump(mode="json", exclude_none=True)
        payload["event_type"] = f"EVENT_TYPE_{self.event_type}"
        payload["device_type"] = f"DEVICE_TYPE_{self.device_type}"
        payload["timestamp"] = (self.timestamp - EPOCH) // timedelta(milliseconds=1)
        return ParseDict(payload, pb.MovieEvent()).SerializeToString()


class EventAccepted(BaseModel):
    event_id: UUID


def publish(producer: Producer, event: EventCreate) -> None:
    delivery: deque[KafkaError | None] = deque()
    producer.produce(
        TOPIC,
        key=event.user_id.encode(),
        value=event.to_protobuf(),
        on_delivery=lambda error, _: delivery.append(error),
    )
    while not delivery:
        producer.poll(0.1)
    error = delivery.popleft()
    if error is not None:
        raise KafkaException(error)
    logger.info(
        "Published event_id=%s event_type=%s timestamp=%s",
        event.event_id,
        event.event_type,
        event.timestamp.astimezone(UTC).isoformat(),
    )


def generate_events(producer: Producer, stop: Event) -> None:
    while not stop.is_set():
        user_id = f"demo-user-{random.randint(1, 12)}"
        movie_id = f"movie-{random.randint(1, 5)}"
        session_id = str(uuid4())
        device_type: DeviceType = random.choice(("MOBILE", "DESKTOP", "TV", "TABLET"))
        paused_at = random.randint(120, 1800)
        duration = random.randint(4800, 7800)
        pause_seconds = random.randint(10, 120)
        finish_offset = duration + pause_seconds
        started_at = datetime.now(UTC) - timedelta(seconds=finish_offset + 1)
        # (тип_события, прогресс_фильма_в_секундах, смещение_от_начала_сессии_в_секундах)
        sequence: list[tuple[EventType, int, int]] = [
            ("SEARCHED", 0, -10),
            ("VIEW_STARTED", 0, 0),
            ("VIEW_PAUSED", paused_at, paused_at),
            ("VIEW_RESUMED", paused_at, paused_at + pause_seconds),
        ]
        if random.random() < 0.75:
            sequence.append(("VIEW_FINISHED", duration, finish_offset))
            if random.random() < 0.5:
                sequence.append(("LIKED", 0, finish_offset + 1))

        for event_type, progress, offset in sequence:
            if stop.wait(3):
                return
            event = EventCreate(
                user_id=user_id,
                movie_id=movie_id,
                event_type=event_type,
                timestamp=started_at + timedelta(seconds=offset),
                device_type=device_type,
                session_id=session_id,
                progress_seconds=progress,
            )
            try:
                publish(producer, event)
            except (KafkaException, BufferError):
                logger.exception("Generator could not publish event_id=%s", event.event_id)
                break


@contextmanager
def lifespan(app: FastAPI) -> Generator[None, None, None]:
    initialize_kafka()
    producer = Producer(
        {
            "bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"],
            "acks": "all",
            "enable.idempotence": True,
            "retry.backoff.ms": 100,
            "retry.backoff.max.ms": 1000,
            "message.timeout.ms": 10000,
        }
    )
    app.state.producer = producer
    generator = (
        worker(generate_events, producer)
        if os.environ.get("GENERATE_EVENTS") == "1"
        else nullcontext()
    )
    try:
        with generator:
            yield
    finally:
        producer.flush(2)


app = FastAPI(
    title="Movie Service — HW5",
    lifespan=lambda app: contextmanager_in_threadpool(lifespan(app)),
)


@app.post("/events", status_code=201)
def create_event(event: EventCreate, request: Request) -> EventAccepted:
    try:
        publish(request.app.state.producer, event)
    except (KafkaException, BufferError) as exc:
        logger.exception("Could not publish event_id=%s", event.event_id)
        raise HTTPException(status_code=503, detail="Kafka did not acknowledge the event") from exc
    return EventAccepted(event_id=event.event_id)

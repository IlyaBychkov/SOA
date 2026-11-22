import logging
import os
from pathlib import Path

import httpx
from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

TOPIC = "movie-events"
SUBJECT = f"{TOPIC}-value"
SCHEMA = Path("proto/movie/v1/movie_event.proto")


def initialize_kafka() -> None:
    admin = AdminClient({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"]})
    topic = NewTopic(TOPIC, num_partitions=3, replication_factor=1)
    result = admin.create_topics([topic], request_timeout=15)[TOPIC]
    try:
        result.result()
    except KafkaException as exc:
        if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise

    body = {"schemaType": "PROTOBUF", "schema": SCHEMA.read_text()}
    with httpx.Client(base_url=os.environ["SCHEMA_REGISTRY_URL"], timeout=15) as registry:
        # Registration is idempotent and rejects incompatible changes.
        registry.post(f"/subjects/{SUBJECT}/versions", json=body).raise_for_status()
        response = registry.post(f"/subjects/{SUBJECT}", json=body).raise_for_status()
        registered = response.json()
    logging.getLogger("uvicorn.error").info(
        "Registered %s version=%s id=%s", SUBJECT, registered["version"], registered["id"]
    )

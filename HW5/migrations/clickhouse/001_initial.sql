CREATE TABLE IF NOT EXISTS movie_events
(
    event_id UUID,
    user_id String,
    movie_id String,
    event_type Enum8(
        'EVENT_TYPE_UNSPECIFIED' = 0, 'VIEW_STARTED' = 1, 'VIEW_FINISHED' = 2,
        'VIEW_PAUSED' = 3, 'VIEW_RESUMED' = 4, 'LIKED' = 5, 'SEARCHED' = 6
    ),
    timestamp DateTime64(3, 'UTC'),
    device_type Enum8(
        'DEVICE_TYPE_UNSPECIFIED' = 0, 'MOBILE' = 1, 'DESKTOP' = 2, 'TV' = 3, 'TABLET' = 4
    ),
    session_id String,
    progress_seconds UInt32
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (toDate(timestamp), event_id);

CREATE TABLE IF NOT EXISTS movie_events_kafka
(
    event_id String,
    user_id String,
    movie_id String,
    event_type Int8,
    timestamp Int64,
    device_type Int8,
    session_id String,
    progress_seconds UInt32
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'movie-events',
    kafka_group_name = 'clickhouse-movie-events',
    kafka_format = 'ProtobufSingle',
    kafka_schema = 'movie_event.proto:MovieEvent';

CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_movie_events TO movie_events AS
SELECT
    toUUID(event_id) AS event_id,
    user_id,
    movie_id,
    event_type,
    fromUnixTimestamp64Milli(timestamp, 'UTC') AS timestamp,
    device_type,
    session_id,
    progress_seconds
FROM movie_events_kafka;

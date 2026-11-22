import unittest
from time import monotonic, sleep
from uuid import uuid4

import httpx

from app import clickhouse

EVENT = {
    "user_id": "test-user",
    "movie_id": "test-movie",
    "event_type": "VIEW_FINISHED",
    "timestamp": "2000-01-01T15:00:00.123+03:00",
    "device_type": "TV",
    "session_id": "test-session",
    "progress_seconds": 5100,
}


class PipelineTest(unittest.TestCase):
    def test_http_event_reaches_clickhouse(self) -> None:
        event_id = str(uuid4())
        event = {**EVENT, "event_id": event_id}
        with httpx.Client(base_url="http://movie:8000", timeout=15) as movie:
            response = movie.post("/events", json=event)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json(), {"event_id": event_id})

        deadline = monotonic() + 30
        with clickhouse.client() as client:
            while (remaining := deadline - monotonic()) > 0:
                response = client.post(
                    "/",
                    params={"param_event_id": event_id, "date_time_output_format": "iso"},
                    timeout=remaining,
                    content="SELECT * FROM movie_events FINAL WHERE event_id = {event_id:UUID} FORMAT JSONEachRow",
                )
                response.raise_for_status()
                if response.text.strip():
                    self.assertEqual(
                        response.json(), {**event, "timestamp": "2000-01-01T12:00:00.123Z"}
                    )
                    return
                sleep(min(0.25, max(0, deadline - monotonic())))
        self.fail(f"Event {event_id} did not reach ClickHouse")

    def test_rejects_movie_id_that_cannot_be_stored(self) -> None:
        with httpx.Client(base_url="http://movie:8000", timeout=15) as movie:
            for movie_id in ("bad\x00id", "é" * 201):
                with self.subTest(movie_id=movie_id):
                    response = movie.post("/events", json={**EVENT, "movie_id": movie_id})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["detail"][0]["loc"], ["body", "movie_id"])

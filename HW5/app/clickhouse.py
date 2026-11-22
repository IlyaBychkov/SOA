import os

import httpx


def client() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ["CLICKHOUSE_URL"],
        auth=(os.environ["CLICKHOUSE_USER"], os.environ["CLICKHOUSE_PASSWORD"]),
        params={"wait_end_of_query": "1"},
        timeout=30,
    )

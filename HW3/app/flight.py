import hmac
import logging
import os
import re
import signal
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import UUID, uuid4

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from redis import Redis

from app.db import connect
from proto.flight.v1 import flight_pb2 as pb
from proto.flight.v1 import flight_pb2_grpc as rpc
from proto.flight.v1.flight_pb2_grpc import add_FlightServiceServicer_to_server


class ApiKeyInterceptor(grpc.ServerInterceptor):
    def __init__(self) -> None:
        self.key = os.environ["GRPC_API_KEY"]
        if not self.key:
            raise ValueError("GRPC_API_KEY must not be empty")

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler | None],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        keys = [
            value for name, value in handler_call_details.invocation_metadata if name == "x-api-key"
        ]
        if len(keys) == 1 and isinstance(keys[0], str) and hmac.compare_digest(keys[0], self.key):
            return continuation(handler_call_details)

        def deny(request: object, context: grpc.ServicerContext) -> None:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid or missing API key")

        return grpc.unary_unary_rpc_method_handler(deny)


def flight_message(row: dict) -> pb.Flight:
    (departure, arrival) = (Timestamp(), Timestamp())
    departure.FromDatetime(row["departure_at"])
    arrival.FromDatetime(row["arrival_at"])
    return pb.Flight(
        id=str(row["id"]),
        flight_number=row["flight_number"],
        airline=row["airline"],
        origin=row["origin"],
        destination=row["destination"],
        departure_at=departure,
        arrival_at=arrival,
        total_seats=row["total_seats"],
        available_seats=row["available_seats"],
        price=row["price"],
        status="FLIGHT_STATUS_" + row["status"],
    )


def parse_id(value: str, context: grpc.ServicerContext) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Expected UUID")


class FlightService(rpc.FlightServiceServicer):
    def __init__(self, cache: Redis) -> None:
        self.cache = cache
        self.cache_lock = threading.Lock()

    def SearchFlights(
        self, request: pb.SearchFlightsRequest, context: grpc.ServicerContext
    ) -> pb.SearchFlightsResponse:
        if not all(re.fullmatch("[A-Z]{3}", x) for x in (request.origin, request.destination)):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Expected uppercase IATA codes")
        params: list[str | date] = [request.origin, request.destination]
        query = "SELECT * FROM flights WHERE origin=%s AND destination=%s"
        query += " AND status='SCHEDULED'"
        if request.HasField("date"):
            try:
                day = date.fromisoformat(request.date)
                if day.isoformat() != request.date:
                    raise ValueError
            except ValueError:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Expected date YYYY-MM-DD")
            query += " AND departure_date=%s"
            params.append(day)
        query += " ORDER BY departure_at, id"
        key = f"search:{request.origin}:{request.destination}:{request.date if request.HasField('date') else 'all'}"
        with self.cache_lock:
            cached = self.cache.get(key)
            logging.info("cache %s: %s", "hit" if cached is not None else "miss", key)
            if isinstance(cached, bytes):
                return pb.SearchFlightsResponse.FromString(cached)
            with connect() as conn:
                rows = conn.execute(query, params).fetchall()
            response = pb.SearchFlightsResponse(flights=[flight_message(row) for row in rows])
            self.cache.set(key, response.SerializeToString(), ex=300)
            return response

    def GetFlight(
        self, request: pb.GetFlightRequest, context: grpc.ServicerContext
    ) -> pb.GetFlightResponse:
        flight_id = parse_id(request.id, context)
        key = f"flight:{flight_id}"
        with self.cache_lock:
            cached = self.cache.get(key)
            logging.info("cache %s: %s", "hit" if cached is not None else "miss", key)
            if isinstance(cached, bytes):
                return pb.GetFlightResponse.FromString(cached)
            with connect() as conn:
                row = conn.execute("SELECT * FROM flights WHERE id=%s", (flight_id,)).fetchone()
            if row is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Flight not found")
            response = pb.GetFlightResponse(flight=flight_message(row))
            self.cache.set(key, response.SerializeToString(), ex=300)
            return response

    def ReserveSeats(
        self, request: pb.ReserveSeatsRequest, context: grpc.ServicerContext
    ) -> pb.ReserveSeatsResponse:
        flight_id = parse_id(request.flight_id, context)
        booking_id = parse_id(request.booking_id, context)
        if request.seat_count <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "seat_count must be positive")
        with self.cache_lock:
            with connect() as conn, conn.transaction():
                row = conn.execute(
                    "SELECT * FROM flights WHERE id=%s FOR UPDATE", (flight_id,)
                ).fetchone()
                if row is None:
                    context.abort(grpc.StatusCode.NOT_FOUND, "Flight not found")
                if row["status"] != "SCHEDULED":
                    context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Flight is not scheduled")
                if row["available_seats"] < request.seat_count:
                    context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Not enough seats")
                conn.execute(
                    "UPDATE flights SET available_seats=available_seats-%s WHERE id=%s",
                    (request.seat_count, flight_id),
                )
                conn.execute(
                    "INSERT INTO seat_reservations (id, booking_id, flight_id, seat_count, status) VALUES (%s,%s,%s,%s,'ACTIVE')",
                    (uuid4(), booking_id, flight_id, request.seat_count),
                )
            self.cache.flushdb()
        return pb.ReserveSeatsResponse()

    def ReleaseReservation(
        self, request: pb.ReleaseReservationRequest, context: grpc.ServicerContext
    ) -> pb.ReleaseReservationResponse:
        booking_id = parse_id(request.booking_id, context)
        with self.cache_lock:
            with connect() as conn, conn.transaction():
                row = conn.execute(
                    "SELECT * FROM seat_reservations WHERE booking_id=%s FOR UPDATE",
                    (booking_id,),
                ).fetchone()
                if row is None:
                    context.abort(grpc.StatusCode.NOT_FOUND, "Reservation not found")
                if row["status"] == "ACTIVE":
                    conn.execute(
                        "UPDATE flights SET available_seats=available_seats+%s WHERE id=%s",
                        (row["seat_count"], row["flight_id"]),
                    )
                    conn.execute(
                        "UPDATE seat_reservations SET status='RELEASED' WHERE booking_id=%s",
                        (booking_id,),
                    )
            self.cache.flushdb()
        return pb.ReleaseReservationResponse()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    with Redis.from_url(
        os.environ["REDIS_URL"], socket_connect_timeout=1, socket_timeout=1
    ) as cache:
        server = grpc.server(ThreadPoolExecutor(max_workers=10), interceptors=[ApiKeyInterceptor()])
        add_FlightServiceServicer_to_server(FlightService(cache), server)
        server.add_insecure_port("[::]:50051")
        server.start()
        logging.info("Flight Service listening on :50051")
        stopped = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda signum, frame: stopped.set())
        stopped.wait()
        server.stop(grace=10).wait()


def healthcheck() -> None:
    with grpc.insecure_channel("localhost:50051") as channel:
        rpc.FlightServiceStub(channel).SearchFlights(
            pb.SearchFlightsRequest(origin="SVO", destination="LED"),
            metadata=(("x-api-key", os.environ["GRPC_API_KEY"]),),
            timeout=2,
        )


if __name__ == "__main__":
    import sys

    healthcheck() if "--healthcheck" in sys.argv else main()

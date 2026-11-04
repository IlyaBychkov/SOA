import logging
import re
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import UUID, uuid4

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from app.db import connect
from proto.flight.v1 import flight_pb2 as pb
from proto.flight.v1 import flight_pb2_grpc as rpc
from proto.flight.v1.flight_pb2_grpc import add_FlightServiceServicer_to_server


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
        with connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return pb.SearchFlightsResponse(flights=[flight_message(row) for row in rows])

    def GetFlight(
        self, request: pb.GetFlightRequest, context: grpc.ServicerContext
    ) -> pb.GetFlightResponse:
        flight_id = parse_id(request.id, context)
        with connect() as conn:
            row = conn.execute("SELECT * FROM flights WHERE id=%s", (flight_id,)).fetchone()
        if row is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "Flight not found")
        return pb.GetFlightResponse(flight=flight_message(row))

    def ReserveSeats(
        self, request: pb.ReserveSeatsRequest, context: grpc.ServicerContext
    ) -> pb.ReserveSeatsResponse:
        flight_id = parse_id(request.flight_id, context)
        booking_id = parse_id(request.booking_id, context)
        if request.seat_count <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "seat_count must be positive")
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
        return pb.ReserveSeatsResponse()

    def ReleaseReservation(
        self, request: pb.ReleaseReservationRequest, context: grpc.ServicerContext
    ) -> pb.ReleaseReservationResponse:
        booking_id = parse_id(request.booking_id, context)
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
        return pb.ReleaseReservationResponse()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    add_FlightServiceServicer_to_server(FlightService(), server)
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
            timeout=2,
        )


if __name__ == "__main__":
    import sys

    healthcheck() if "--healthcheck" in sys.argv else main()

import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

import grpc
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg import Error as DatabaseError
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from proto.flight.v1 import flight_pb2 as pb
from proto.flight.v1.flight_pb2_grpc import FlightServiceStub

Iata = Annotated[str, Field(pattern="^[A-Z]{3}$")]


class BookingCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    user_id: str = Field(min_length=1, max_length=200)
    flight_id: UUID
    passenger_name: str = Field(min_length=1, max_length=200)
    passenger_email: str = Field(max_length=320, pattern="^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$")
    seat_count: int = Field(gt=0, le=2147483647, strict=True)


class BookingRead(BookingCreate):
    id: UUID
    total_price: int
    status: Literal["CONFIRMED", "CANCELLED"]
    created_at: datetime


class FlightRead(BaseModel):
    id: UUID
    flight_number: str
    airline: str
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    total_seats: int
    available_seats: int
    price: int
    status: Literal["SCHEDULED", "DEPARTED", "CANCELLED", "COMPLETED"]


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with grpc.insecure_channel(os.environ["FLIGHT_ADDRESS"]) as channel:
        app.state.flight = FlightServiceStub(channel)
        yield


app = FastAPI(title="Flight Booking — HW3", lifespan=lifespan)


@app.exception_handler(grpc.RpcError)
def grpc_error(request: Request, exc: grpc.RpcError) -> JSONResponse:
    statuses = {
        grpc.StatusCode.INVALID_ARGUMENT: 400,
        grpc.StatusCode.NOT_FOUND: 404,
        grpc.StatusCode.RESOURCE_EXHAUSTED: 409,
        grpc.StatusCode.FAILED_PRECONDITION: 409,
        grpc.StatusCode.UNAVAILABLE: 503,
        grpc.StatusCode.DEADLINE_EXCEEDED: 504,
    }
    return JSONResponse(
        status_code=statuses.get(exc.code(), 502), content={"detail": exc.details()}
    )


@app.exception_handler(DatabaseError)
def database_error(request: Request, exc: DatabaseError) -> JSONResponse:
    logger.error("Booking database operation failed: %s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Booking database unavailable"})


def flight_json(flight: pb.Flight) -> dict:
    return {
        "id": flight.id,
        "flight_number": flight.flight_number,
        "airline": flight.airline,
        "origin": flight.origin,
        "destination": flight.destination,
        "departure_at": flight.departure_at.ToDatetime(tzinfo=UTC),
        "arrival_at": flight.arrival_at.ToDatetime(tzinfo=UTC),
        "total_seats": flight.total_seats,
        "available_seats": flight.available_seats,
        "price": flight.price,
        "status": pb.FlightStatus.Name(flight.status).removeprefix("FLIGHT_STATUS_"),
    }


@app.get("/health")
def health() -> dict:
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/flights", response_model=list[FlightRead])
def search_flights(
    request: Request, origin: Iata, destination: Iata, date: date | None = None
) -> list[dict]:
    query = pb.SearchFlightsRequest(origin=origin, destination=destination)
    if date is not None:
        query.date = date.isoformat()
    client = request.app.state.flight
    result = client.SearchFlights(query, timeout=5)
    return [flight_json(flight) for flight in result.flights]


@app.get("/flights/{flight_id}", response_model=FlightRead)
def get_flight(request: Request, flight_id: UUID) -> dict:
    client = request.app.state.flight
    result = client.GetFlight(pb.GetFlightRequest(id=str(flight_id)), timeout=5)
    return flight_json(result.flight)


@app.post("/bookings", response_model=BookingRead, status_code=201)
def create_booking(request: Request, body: BookingCreate) -> dict:
    client = request.app.state.flight
    flight = client.GetFlight(
        pb.GetFlightRequest(id=str(body.flight_id)),
        timeout=5,
    ).flight
    booking_id = uuid4()
    total_price = flight.price * body.seat_count
    client.ReserveSeats(
        pb.ReserveSeatsRequest(
            flight_id=str(body.flight_id),
            booking_id=str(booking_id),
            seat_count=body.seat_count,
        ),
        timeout=5,
    )
    try:
        with connect() as conn:
            row = conn.execute(
                "INSERT INTO bookings (id,user_id,flight_id,passenger_name,passenger_email,seat_count,total_price,status) VALUES (%s,%s,%s,%s,%s,%s,%s,'CONFIRMED') RETURNING *",
                (
                    booking_id,
                    body.user_id,
                    body.flight_id,
                    body.passenger_name,
                    body.passenger_email,
                    body.seat_count,
                    total_price,
                ),
            ).fetchone()
            assert row is not None
    except Exception:
        try:
            client.ReleaseReservation(
                pb.ReleaseReservationRequest(booking_id=str(booking_id)), timeout=5
            )
        except grpc.RpcError:
            logger.exception("Cannot release reservation for booking %s", booking_id)
        raise

    return row


@app.get("/bookings/{booking_id}", response_model=BookingRead)
def get_booking(booking_id: UUID) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id=%s", (booking_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Booking not found")
    return row


@app.post("/bookings/{booking_id}/cancel", response_model=BookingRead)
def cancel_booking(request: Request, booking_id: UUID) -> dict:
    with connect() as conn, conn.transaction():
        row = conn.execute(
            "SELECT * FROM bookings WHERE id=%s FOR UPDATE", (booking_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Booking not found")
        if row["status"] != "CONFIRMED":
            raise HTTPException(409, "Booking is already cancelled")
        client = request.app.state.flight
        client.ReleaseReservation(
            pb.ReleaseReservationRequest(booking_id=str(booking_id)), timeout=5
        )
        row = conn.execute(
            "UPDATE bookings SET status='CANCELLED' WHERE id=%s RETURNING *",
            (booking_id,),
        ).fetchone()
        assert row is not None
    return row


@app.get("/bookings", response_model=list[BookingRead])
def list_bookings(user_id: str = Query(min_length=1)) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM bookings WHERE user_id=%s ORDER BY created_at, id",
            (user_id,),
        ).fetchall()

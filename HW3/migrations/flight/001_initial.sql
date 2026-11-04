CREATE TABLE flights (
    id UUID PRIMARY KEY,
    flight_number TEXT NOT NULL CHECK (length(flight_number) > 0),
    airline TEXT NOT NULL CHECK (length(airline) > 0),
    origin CHAR(3) NOT NULL CHECK (origin ~ '^[A-Z]{3}$'),
    destination CHAR(3) NOT NULL CHECK (destination ~ '^[A-Z]{3}$'),
    departure_at TIMESTAMPTZ NOT NULL,
    departure_date DATE GENERATED ALWAYS AS ((departure_at AT TIME ZONE 'UTC')::date) STORED,
    arrival_at TIMESTAMPTZ NOT NULL CHECK (arrival_at > departure_at),
    total_seats INTEGER NOT NULL CHECK (total_seats > 0),
    available_seats INTEGER NOT NULL CHECK (available_seats >= 0 AND available_seats <= total_seats),
    price BIGINT NOT NULL CHECK (price > 0),
    status TEXT NOT NULL CHECK (status IN ('SCHEDULED','DEPARTED','CANCELLED','COMPLETED')),
    UNIQUE (flight_number, departure_date),
    CHECK (origin <> destination)
);
CREATE TABLE seat_reservations (
    id UUID PRIMARY KEY,
    flight_id UUID NOT NULL REFERENCES flights(id),
    booking_id UUID NOT NULL UNIQUE,
    seat_count INTEGER NOT NULL CHECK (seat_count > 0),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','RELEASED','EXPIRED'))
);
INSERT INTO flights VALUES (
    '11111111-1111-4111-8111-111111111111',
    'SU1234',
    'Aeroflot',
    'SVO',
    'LED',
    '2026-12-01 10:00:00+00',
    DEFAULT,
    '2026-12-01 11:30:00+00',
    100,
    100,
    5000,
    'SCHEDULED'
);

CREATE TABLE bookings (
    id UUID PRIMARY KEY,
    user_id TEXT NOT NULL CHECK (length(user_id) > 0),
    flight_id UUID NOT NULL,
    passenger_name TEXT NOT NULL CHECK (length(passenger_name) > 0),
    passenger_email TEXT NOT NULL CHECK (length(passenger_email) > 0),
    seat_count INTEGER NOT NULL CHECK (seat_count > 0),
    total_price BIGINT NOT NULL CHECK (total_price > 0),
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED','CANCELLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

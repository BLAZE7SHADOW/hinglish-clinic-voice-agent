-- Week 2 writes: who made each booking, cancellations, and what a reschedule replaced.

-- The call that created the row. Holds are released by call id when the call ends, and the
-- staff view can show which call made a booking.
alter table bookings add column call_id text;
alter table bookings add column cancelled_at timestamptz;
-- A rescheduled booking points at the one it replaced (the old row is kept, status cancelled).
alter table bookings add column replaces_booking_id bigint references bookings(id);

create index bookings_call on bookings (call_id) where call_id is not null;
create index bookings_patient on bookings (patient_id, starts_at);

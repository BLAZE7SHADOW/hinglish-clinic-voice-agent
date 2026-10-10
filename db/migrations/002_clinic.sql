-- Clinic data, patients, bookings, calls and the event log (design §9).
-- Field names stay close to FHIR Appointment/Patient, so the model maps onto health systems later.
-- Synthetic data only. Money is integer paise, never floats (design §16.3).

create table clinics (
    id          bigint generated always as identity primary key,
    name        text not null,
    timezone    text not null default 'Asia/Kolkata',
    created_at  timestamptz not null default now()
);

-- Answers for clinic_info, one row per topic. Free text written by clinic staff; the agent treats
-- it as data, never as instructions (design §16.4: data-borne injection).
create table clinic_facts (
    clinic_id   bigint not null references clinics(id) on delete cascade,
    topic       text   not null check (topic in ('hours', 'address', 'phone', 'services', 'payment', 'parking')),
    answer      text   not null,
    primary key (clinic_id, topic)
);

create table doctors (
    id                      bigint generated always as identity primary key,
    clinic_id               bigint not null references clinics(id) on delete cascade,
    full_name               text   not null,                -- "Dr. Anil Sharma"
    specialty               text   not null,                -- "General physician"
    consultation_fee_paise  int    not null check (consultation_fee_paise >= 0),
    slot_minutes            int    not null default 15 check (slot_minutes between 5 and 120),
    active                  boolean not null default true
);

-- Weekly working hours: a template, not stored slots. Free slots are computed in code as
-- template minus exceptions minus active bookings. 0 = Monday ... 6 = Sunday (Python weekday()).
create table doctor_hours (
    doctor_id   bigint   not null references doctors(id) on delete cascade,
    weekday     smallint not null check (weekday between 0 and 6),
    starts      time     not null,
    ends        time     not null check (ends > starts),
    primary key (doctor_id, weekday, starts)
);

-- Days a doctor does not work (leave, holidays).
create table doctor_days_off (
    doctor_id   bigint not null references doctors(id) on delete cascade,
    day         date   not null,
    reason      text,
    primary key (doctor_id, day)
);

create table patients (
    id          bigint generated always as identity primary key,
    clinic_id   bigint not null references clinics(id) on delete cascade,
    full_name   text   not null,
    phone       text   not null,                             -- E.164, synthetic
    birth_date  date   not null,
    unique (clinic_id, phone, full_name)
);

create table bookings (
    id               bigint generated always as identity primary key,
    clinic_id        bigint not null references clinics(id) on delete cascade,
    doctor_id        bigint not null references doctors(id),
    patient_id       bigint references patients(id),         -- null while only held
    starts_at        timestamptz not null,
    ends_at          timestamptz not null check (ends_at > starts_at),
    status           text not null check (status in ('held', 'confirmed', 'cancelled')),
    hold_expires_at  timestamptz,                             -- only for 'held'
    idempotency_key  text unique,                             -- full length, never truncated
    created_by       text not null check (created_by in ('agent', 'staff', 'workflow', 'seed')),
    created_at       timestamptz not null default now()
);

-- The final guard against double booking (design §9): at most one active booking per doctor
-- per start time, enforced by the database, whatever the application does.
create unique index bookings_one_active_per_slot
    on bookings (doctor_id, starts_at)
    where status in ('held', 'confirmed');

create index bookings_doctor_day on bookings (doctor_id, starts_at);

create table calls (
    id          text primary key,                             -- LiveKit job id, same as turns.call_id
    clinic_id   bigint references clinics(id),
    channel     text not null check (channel in ('browser', 'phone')),
    started_at  timestamptz not null default now(),
    ended_at    timestamptz,
    outcome     text check (outcome in ('booked', 'rescheduled', 'cancelled', 'answered', 'handed_off', 'dropped'))
);

-- Append-only log of everything that changed state; source for the staff view and debugging.
create table events (
    id          bigint generated always as identity primary key,
    call_id     text,
    type        text not null,                                -- "booking_created", "handoff_requested", ...
    payload     jsonb not null default '{}',
    created_at  timestamptz not null default now()
);

create index events_call on events (call_id, created_at);

"""Seed a synthetic clinic: 3 doctors, weekly hours, 20 patients, about 2 weeks of bookings.

    uv run python -m db.seed

Synthetic data only (design §11): fictional names, an invented address, and phone numbers in
an unallocated-looking +91 90000 range. Re-running replaces the demo clinic. Bookings are
placed relative to today, so "kal" and "parson" always have realistic data, and seeding is
deterministic (fixed random seed) so test calls are repeatable on the same day.
"""

import asyncio
import os
import random
from datetime import date, datetime, time, timedelta

import asyncpg
from dotenv import load_dotenv

from agent.dates import CLINIC_TZ, now
from agent.db import assert_safe_target, connect_kwargs
from agent.schedule import WorkingHours, free_slots

CLINIC = "Sharma Family Clinic"
DAYS = 14
BOOKED_SHARE = 0.35  # fraction of slots already taken, so availability looks realistic

FACTS = {
    "hours": "Monday to Saturday, 9 AM to 8 PM. Sunday 10 AM to 12 noon, physiotherapy only.",
    "address": "12 Lotus Lane, Shanti Nagar, Pune 411001, opposite Shanti Nagar Post Office.",
    "phone": "Front desk: plus nine one, two zero, zero zero zero zero, one two three four.",
    "services": "General consultation, dental care, and physiotherapy.",
    "payment": "Cash, UPI and all major cards are accepted. Pay at the front desk after the visit.",
    "parking": "Free two-wheeler parking in front; car parking on the lane behind the clinic.",
}

# (name, specialty, fee in paise, slot minutes, [(weekdays, start, end)], days off from today)
DOCTORS = [
    (
        "Dr. Anil Sharma",
        "General physician",
        500_00,
        15,
        [((0, 1, 2, 3, 4, 5), time(10), time(13)), ((0, 1, 2, 3, 4, 5), time(17), time(20))],
        [],
    ),
    (
        "Dr. Priya Mehta",
        "Dentist",
        700_00,
        30,
        [((0, 2, 4, 5), time(11), time(14)), ((0, 2, 4, 5), time(16), time(19))],
        [3],
    ),
    (
        "Dr. Kavita Iyer",
        "Physiotherapist",
        600_00,
        45,
        [((1, 3, 5), time(9), time(13)), ((6,), time(10), time(12))],
        [],
    ),
]

PATIENTS = [
    "Aarav Kulkarni",
    "Ananya Deshpande",
    "Rohan Joshi",
    "Sneha Patil",
    "Vikram Rao",
    "Meera Nair",
    "Arjun Malhotra",
    "Pooja Bhatt",
    "Karan Saxena",
    "Isha Kapoor",
    "Nikhil Gupta",
    "Riya Chatterjee",
    "Siddharth Menon",
    "Tanvi Reddy",
    "Aditya Pandey",
    "Neha Srinivasan",
    "Manish Tiwari",
    "Divya Pillai",
    "Harsh Agarwal",
    "Kavya Shetty",
]


async def seed() -> None:
    load_dotenv(".env")
    assert_safe_target()
    rng = random.Random(42)
    today = now().date()  # the eval clock when EVAL_MODE=1, else today
    conn = await asyncpg.connect(**connect_kwargs(os.environ["DATABASE_URL"]))
    try:
        async with conn.transaction():
            await conn.execute("delete from clinics where name = $1", CLINIC)
            clinic_id = await conn.fetchval(
                "insert into clinics (name) values ($1) returning id", CLINIC
            )
            await conn.executemany(
                "insert into clinic_facts (clinic_id, topic, answer) values ($1, $2, $3)",
                [(clinic_id, t, a) for t, a in FACTS.items()],
            )

            patient_ids = []
            for i, name in enumerate(PATIENTS):
                birth = date(1950, 1, 1) + timedelta(days=rng.randrange(365 * 55))
                patient_ids.append(
                    await conn.fetchval(
                        "insert into patients (clinic_id, full_name, phone, birth_date)"
                        " values ($1, $2, $3, $4) returning id",
                        clinic_id,
                        name,
                        f"+9190000{i:05d}",
                        birth,
                    )
                )

            n_bookings = 0
            for name, specialty, fee, slot_min, blocks, off in DOCTORS:
                doctor_id = await conn.fetchval(
                    "insert into doctors (clinic_id, full_name, specialty,"
                    " consultation_fee_paise, slot_minutes) values ($1,$2,$3,$4,$5) returning id",
                    clinic_id,
                    name,
                    specialty,
                    fee,
                    slot_min,
                )
                hours = [WorkingHours(wd, s, e) for wds, s, e in blocks for wd in wds]
                await conn.executemany(
                    "insert into doctor_hours (doctor_id, weekday, starts, ends)"
                    " values ($1,$2,$3,$4)",
                    [(doctor_id, h.weekday, h.starts, h.ends) for h in hours],
                )
                days_off = {today + timedelta(days=d) for d in off}
                await conn.executemany(
                    "insert into doctor_days_off (doctor_id, day, reason) values ($1,$2,$3)",
                    [(doctor_id, d, "Conference") for d in days_off],
                )

                start = datetime.combine(today, time(0), CLINIC_TZ)
                slots = free_slots(
                    hours, days_off, set(), slot_min, start, start + timedelta(days=DAYS), now=start
                )
                taken = [s for s in slots if rng.random() < BOOKED_SHARE]
                await conn.executemany(
                    "insert into bookings (clinic_id, doctor_id, patient_id, starts_at, ends_at,"
                    " status, created_by) values ($1,$2,$3,$4,$5,'confirmed','seed')",
                    [
                        (
                            clinic_id,
                            doctor_id,
                            rng.choice(patient_ids),
                            s,
                            s + timedelta(minutes=slot_min),
                        )
                        for s in taken
                    ],
                )
                n_bookings += len(taken)
                print(f"{name:18s} {len(slots):3d} slots in {DAYS} days, {len(taken):3d} booked")
        print(f"seeded clinic {clinic_id}: {len(PATIENTS)} patients, {n_bookings} bookings")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())

# REVIEW.md — Starter Code Defect Report

## Summary

Twenty defects were identified in the supplied starter. All have been corrected
in the submitted `app/main.py`. Items examined and found correct are noted at
the end.

---

## Defects

### Defect 1 — `compute_late_minutes`: Wrong timezone comparison

**Location:** Helper function `compute_late_minutes`

**What is wrong:** `punch_in.replace(hour=h, minute=m)` replaces the hour and
minute on the punch-in datetime without converting to IST first. If `punch_in`
is UTC, the shift start is built in UTC instead of IST, making the comparison
5 hours 30 minutes wrong.

**How to reproduce:** Punch in at 04:10 UTC (09:40 IST). The function builds
shift start as 09:30 UTC instead of 09:30 IST (04:00 UTC). The elapsed time
becomes 40 minutes instead of 10 minutes, producing wrong late_minutes.

**Fix:** Convert `punch_in` to IST with `.astimezone(IST)` before building the
shift start datetime.

---

### Defect 2 — `compute_late_minutes`: Seconds truncated before grace check

**Location:** Helper function `compute_late_minutes`

**What is wrong:** `int((punch_in - start).total_seconds() / 60)` converts to
whole minutes before checking the grace period. At 09:40:01 IST (601 seconds
after shift start), the result is `int(601/60) = 10`, then `10 > 10` is False,
returning 0. The contract says 09:40:01 should return `late_minutes = 10`.

**How to reproduce:** Punch in at exactly 09:40:01 IST with a 09:30 shift.
Expected `late_minutes = 10`. Starter returns 0.

**Fix:** Compare raw seconds against 600. If `elapsed_seconds > 600`, return
`elapsed_seconds // 60`.

---

### Defect 3 — `compute_overtime`: Overnight shift end on wrong date

**Location:** Helper function `compute_overtime`

**What is wrong:** `datetime.fromisoformat(date_str).replace(hour=h, minute=m)`
always builds shift end on the attendance date. For a 22:00–06:00 shift, the
real shift end is 06:00 the next day. This function builds it on the same day,
giving a shift end 16 hours before the real one. `max(0, ...)` silently returns
zero overtime.

**How to reproduce:** Punch out at 06:40 IST on July 7 for a 22:00–06:00 shift
started on July 6. Expected `overtime_minutes = 40`. Starter returns 0.

**Fix:** Build shift end on both the same day and the next day. Choose whichever
is at or before `punch_out`.

---

### Defect 4 — `compute_overtime`: 30-minute threshold missing

**Location:** Helper function `compute_overtime`

**What is wrong:** Returns any positive overtime minutes with no minimum check.
The contract (R3) says overtime is only counted when the employee works at least
30 whole minutes past shift end. Values below 30 should return 0.

**How to reproduce:** Punch out 29 minutes after shift end. Expected
`overtime_minutes = 0`. Starter returns 29.

**Fix:** Return `overtime_minutes if overtime_minutes >= 30 else 0`.

---

### Defect 5 — `compute_work_hours`: Banker's rounding

**Location:** Helper function `compute_work_hours`

**What is wrong:** Python's built-in `round()` uses banker's rounding
(round-half-to-even). The contract (R4, R8) requires half-up rounding.
`round(2.125, 2)` gives `2.12` instead of the required `2.13`.

**How to reproduce:** Work exactly 2.125 hours. Expected `work_hours = 2.13`.
Starter returns 2.12.

**Fix:** Use `Decimal` with `ROUND_HALF_UP`.

---

### Defect 6 — `PunchInIn.status`: No enum validation

**Location:** Pydantic model `PunchInIn`

**What is wrong:** `status: str = "PRESENT"` accepts any string. Values like
`"BANANA"` or `"ABSENT"` are stored without error. The contract only allows
`PRESENT`, `WFH` and `ON_DUTY` for punch-in.

**How to reproduce:** POST `/attendance/punch-in` with `status: "INVALID"`.
Starter stores it. Fixed version returns 422.

**Fix:** Change type to `Literal["PRESENT", "WFH", "ON_DUTY"]`.

---

### Defect 7 — `EmployeeIn`: No field format validation

**Location:** Pydantic model `EmployeeIn`

**What is wrong:** All fields are plain `str` with no constraints. Invalid
values like `emp_code: "ABC"`, `email: "notanemail"`, `shift_start: "99:99"`,
`joined_on: "not-a-date"` are accepted and stored.

**How to reproduce:** POST `/employees` with `emp_code: "INVALID"`. Starter
creates the employee. Fixed version returns 422.

**Fix:** Add `Field(pattern=...)`, `Field(min_length=...)`, `Field(max_length=...)`
and a `@model_validator` for `shift_start != shift_end`.

---

### Defect 8 — `GET /health`: Never contacts MongoDB

**Location:** Route handler `health()`

**What is wrong:** Always returns `{"status": "ok"}` with status 200 regardless
of MongoDB availability. If MongoDB is down the endpoint still reports healthy.

**How to reproduce:** Stop MongoDB and call `GET /health`. Starter returns 200.
Fixed version returns 503.

**Fix:** Call `db.command("ping")` inside a try/except. Return 503 on exception.

---

### Defect 9 — `POST /employees`: Race condition on duplicate check

**Location:** Route handler `create_employee()`

**What is wrong:** Uses check-then-insert pattern. Two simultaneous requests for
the same `emp_code` can both pass `find_one` before either inserts, resulting in
two duplicate employee documents.

**How to reproduce:** Send two concurrent POST requests for `EMP0001`. Both may
return 201 with two documents in the database.

**Fix:** Remove the `find_one` check. Rely on the unique index on `emp_code` and
catch `DuplicateKeyError` → 409.

---

### Defect 10 — `POST /employees`: `created_at` uses naive local time

**Location:** Route handler `create_employee()`

**What is wrong:** `datetime.now()` returns a naive local datetime. On a non-UTC
server this stores the wrong time. MongoDB expects UTC.

**How to reproduce:** Run on a server in IST. `created_at` is stored 5h30m ahead
of UTC.

**Fix:** Use `datetime.now(timezone.utc)`.

---

### Defect 11 — `GET /employees`: Pagination off by one

**Location:** Route handler `list_employees()`

**What is wrong:** `skip = page * page_size` skips `page_size` items on page 1.
Page 1 returns items 21–40 instead of 1–20. The first page is unreachable.

**How to reproduce:** Create 5 employees. GET `/employees?page=1&page_size=5`.
Starter returns empty. Fixed version returns all 5.

**Fix:** `skip = (page - 1) * page_size`.

---

### Defect 12 — `GET /employees`: `total` ignores department filter

**Location:** Route handler `list_employees()`

**What is wrong:** `count_documents({})` counts all employees regardless of the
department filter. With 500 employees and 5 in Engineering, filtering by
Engineering returns `total: 500` instead of `total: 5`.

**Fix:** Pass the same `query` dict to `count_documents`.

---

### Defect 13 — `POST /attendance/punch-in`: No null check for unknown employee

**Location:** Route handler `punch_in()`

**What is wrong:** `find_one` returns `None` for unknown employees. The code
continues and crashes on `emp["shift_start"]` with `TypeError`, returning 500
instead of 404.

**How to reproduce:** Punch in with `emp_code: "EMP9999"`. Starter returns 500.
Fixed version returns 404.

**Fix:** Check `if emp is None: raise HTTPException(404, ...)` immediately after
`find_one`.

---

### Defect 14 — `POST /attendance/punch-in`: Timestamp converted to naive local time

**Location:** Route handler `punch_in()`

**What is wrong:** `datetime.fromtimestamp(body.punched_at / 1000)` without
`tz=timezone.utc` returns a naive local datetime. On a UTC server a punch-in at
23:45 UTC would produce the wrong IST date.

**Fix:** Use `ms_to_utc_dt(body.punched_at)` which passes `tz=timezone.utc`.

---

### Defect 15 — `POST /attendance/punch-in`: Race condition on duplicate punch-in

**Location:** Route handler `punch_in()`

**What is wrong:** Same check-then-insert race condition as Defect 9. Two
simultaneous punch-ins for the same employee and date can both succeed.

**Fix:** Remove `find_one` check. Use unique compound index on `(emp_code, date)`
and catch `DuplicateKeyError` → 409.

---

### Defect 16 — `POST /attendance/punch-in`: Response includes forbidden `id` field

**Location:** Route handler `punch_in()`

**What is wrong:** `doc["id"] = str(res.inserted_id)` adds an `id` field to the
response. The contract explicitly forbids an `id` field on attendance records.

**Fix:** Remove those lines. Use `record_to_response(doc)` which removes `_id`
without adding `id`.

---

### Defect 17 — `GET /attendance`: Entire collection loaded into Python memory

**Location:** Route handler `list_attendance()`

**What is wrong:** `list(db.attendance_logs.find(q))` fetches all matching
documents into Python. Sorting and pagination happen in Python memory. On 100,000
records this is extremely slow and memory-intensive.

**Fix:** Use `.sort().skip().limit()` on the cursor and `count_documents(query)`
for the total. All work stays in MongoDB.

---

### Defect 18 — `GET /attendance`: Response exposes forbidden `id` field

**Location:** Route handler `list_attendance()`

**What is wrong:** `d["id"] = str(d.pop("_id"))` renames `_id` to `id` and
includes it in the response. The contract forbids this field.

**Fix:** Use `{"_id": 0}` in the projection and `record_to_response(doc)`.

---

### Defect 19 — `GET /attendance`: No maximum on `page_size`

**Location:** Route handler `list_attendance()`

**What is wrong:** No upper bound on `page_size`. A caller can request
`page_size=1000000`.

**Fix:** `page_size: int = Query(default=20, ge=1, le=100)`.

---

### Defect 20 — `GET /attendance`: No minimum on `page`

**Location:** Route handler `list_attendance()`

**What is wrong:** No lower bound on `page`. A caller can send `page=0` or
negative values.

**Fix:** `page: int = Query(default=1, ge=1)`.

---

## Items Examined and Found Correct

- The general structure of the MongoDB connection setup using environment
  variables is correct. `load_dotenv()` correctly gives priority to real
  environment variables over `.env` file values.
- The `IST` timezone constant definition `timezone(timedelta(hours=5, minutes=30))`
  is correct.
- The `model_dump()` call in `create_employee` correctly converts the Pydantic
  model to a dictionary for MongoDB insertion.
- The date range filter logic using `$gte` and `$lte` on the `date` string field
  in `list_attendance` is correct — ISO date strings compare lexicographically
  in the same order as chronologically.
- The `FastAPI` app title and version match the OpenAPI contract.

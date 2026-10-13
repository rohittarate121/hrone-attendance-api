"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""

import os
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from calendar import monthrange

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from bson import ObjectId, Timestamp

#New configuration
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.getenv("MONGO_DB",  "attendance_db")

client = MongoClient(MONGO_URI)
db     = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index("department", name="ix_emp_department") 

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_emp_date",
    )

    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="ix_date_emp",
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
"""def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))"""
    
def half_up(value: float, places: int) -> float:
    quantize_str = "0." + "0" * places
    return float(
        Decimal(str(value)).quantize(
            Decimal(quantize_str),
            rounding=ROUND_HALF_UP
        )
    )
    
def ms_to_utc_dt(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)

def utc_dt_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def truncate_to_seconds(dt: datetime) -> datetime:
    return dt.replace(microsecond=0)

def compute_attendance_date(
    punch_in_utc: datetime,
    shift_start: str,
    shift_end: str
) -> str:
    punch_in_ist = punch_in_utc.astimezone(IST)

    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))

    is_overnight = (eh * 60 + em) <= (sh * 60 + sm)

    if is_overnight:
        punch_time_mins  = punch_in_ist.hour * 60 + punch_in_ist.minute
        shift_end_mins   = eh * 60 + em

        if punch_time_mins < shift_end_mins:
            return (punch_in_ist - timedelta(days=1)).date().isoformat()

    return punch_in_ist.date().isoformat()

def compute_late_minutes(punch_in_utc: datetime, shift_start: str) -> int:
    punch_in_ist = punch_in_utc.astimezone(IST)

    h, m = map(int, shift_start.split(":"))
    shift_start_dt = punch_in_ist.replace(hour=h, minute=m, second=0, microsecond=0)

    elapsed_seconds = int((punch_in_ist - shift_start_dt).total_seconds())

    if elapsed_seconds > 600:
        return elapsed_seconds // 60
    return 0

def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    seconds = (punch_out_utc - punch_in_utc).total_seconds()
    return half_up(seconds / 3600, 2)

def compute_overtime_minutes(
    punch_out_utc: datetime,
    shift_end: str,
    attendance_date: str
) -> int:
    h, m = map(int, shift_end.split(":"))

    base = datetime.fromisoformat(attendance_date)

    shift_end_same = datetime(
        base.year, base.month, base.day, h, m, 0, tzinfo=IST
    )
    shift_end_next = shift_end_same + timedelta(days=1)

    punch_out_aware = punch_out_utc.astimezone(IST)

    if shift_end_next <= punch_out_aware:
        shift_end_dt = shift_end_next
    elif shift_end_same <= punch_out_aware:
        shift_end_dt = shift_end_same
    else:
        return 0

    overtime_seconds = int((punch_out_aware - shift_end_dt).total_seconds())
    overtime_minutes = overtime_seconds // 60

    return overtime_minutes if overtime_minutes >= 30 else 0

def compute_half_day(work_hours: float) -> bool:
    return work_hours < 4.50

def employee_to_response(doc: dict) -> dict:
    doc.pop("_id", None)

    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = utc_dt_to_ms(doc["created_at"])

    return doc

def record_to_response(doc: dict) -> dict:
    doc.pop("_id", None)

    # Convert punch_in and punch_out — either a datetime or None
    def to_ms_or_none(value):
        if value is None:
            return None
        return utc_dt_to_ms(value)

    doc["punch_in"]  = to_ms_or_none(doc.get("punch_in"))
    doc["punch_out"] = to_ms_or_none(doc.get("punch_out"))

    # Fill in missing fields — legacy records may omit these
    doc.setdefault("work_hours",       None)
    doc.setdefault("late_minutes",     0)
    doc.setdefault("overtime_minutes", 0)
    doc.setdefault("half_day",         False)
    doc.setdefault("history",          [])

    # Convert datetimes inside the history array
    for entry in doc["history"]:
        entry["at"] = utc_dt_to_ms(entry["at"])

        changes = entry.get("changes", {})

        for field in ("punch_in", "punch_out"):
            if field in changes:
                changes[field]["from"] = to_ms_or_none(changes[field].get("from"))
                changes[field]["to"]   = to_ms_or_none(changes[field].get("to"))

    return doc

def get_month_bounds(month: str):
    """
    Given "2026-07", return:
    - first_day: "2026-07-01"
    - last_day:  "2026-07-31"
    - year: 2026
    - month_num: 7
    """
    year, month_num = map(int, month.split("-"))
    _, last = monthrange(year, month_num)
    first_day = f"{year:04d}-{month_num:02d}-01"
    last_day  = f"{year:04d}-{month_num:02d}-{last:02d}"
    return first_day, last_day, year, month_num

def count_working_days(first_day: str, last_day: str, joined_on: str) -> int:
    """
    Count Monday-Friday days between the later of first_day/joined_on
    and last_day, inclusive. R7.
    """
    start = max(
        date.fromisoformat(first_day),
        date.fromisoformat(joined_on)
    )
    end = date.fromisoformat(last_day)

    if start > end:
        return 0

    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:   # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday
            count += 1
        current += timedelta(days=1)
    return count

def make_json_safe(obj):
    """
    Recursively convert a MongoDB document to a JSON-serialisable structure.
    Handles ObjectId, datetime, Timestamp, bytes and other BSON types.
    """
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_safe(v) for v in obj]
    elif isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, Timestamp):
        return {"t": obj.time, "i": obj.inc}
    elif isinstance(obj, bytes):
        return obj.hex()
    elif isinstance(obj, float) and (obj != obj):
        # Handle NaN values which are not valid JSON
        return None
    else:
        try:
            # Try to return as-is for basic types
            # int, float, str, bool, None are all fine
            return obj
        except Exception:
            return str(obj)

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
"""class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str"""
class EmployeeIn(BaseModel):
    emp_code:    str = Field(pattern=r"^EMP\d{4,6}$")
    name:        str = Field(min_length=1, max_length=100)
    email:       str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department:  str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end:   str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on:   str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

    @model_validator(mode="after")
    def shift_start_must_differ_from_shift_end(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


"""class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"""
class PunchInIn(BaseModel):
    emp_code:   str
    punched_at: Optional[int] = None
    status:     Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at", mode="before")
    @classmethod
    def validate_epoch_ms(cls, v):
        if v is None:
            return v
        if not isinstance(v, int):
            raise ValueError("punched_at must be an integer")
        if v < 100_000_000_000 or v > 4_102_444_800_000:
            raise ValueError(
                "punched_at must be epoch milliseconds "
                "(min 100000000000, max 4102444800000)"
            )
        return v
    
class PunchOutIn(BaseModel):
    emp_code:   str
    punched_at: Optional[int] = None

    @field_validator("punched_at", mode="before")
    @classmethod
    def validate_epoch_ms(cls, v):
        if v is None:
            return v
        if not isinstance(v, int):
            raise ValueError("punched_at must be an integer")
        if v < 100_000_000_000 or v > 4_102_444_800_000:
            raise ValueError(
                "punched_at must be epoch milliseconds "
                "(min 100000000000, max 4102444800000)"
            )
        return v
    
class RegularizeIn(BaseModel):
    status:          Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in:        Optional[int] = None
    punch_out:       Optional[int] = None
    reason:          str = Field(min_length=5, max_length=200)
    regularized_by:  str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out", mode="before")
    @classmethod
    def validate_epoch_ms(cls, v):
        if v is None:
            return v
        if not isinstance(v, int):
            raise ValueError("must be an integer epoch milliseconds value")
        if v < 100_000_000_000 or v > 4_102_444_800_000:
            raise ValueError(
                "must be epoch milliseconds "
                "(min 100000000000, max 4102444800000)"
            )
        return v


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
# --- Employees ---
# Bug 8 — always returns 200, never contacts MongoDB.
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")

#Bugs here: Bug 9 (race condition), Bug 10 (naive local time), and the response does not convert created_at to epoch milliseconds.
    
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")

    return employee_to_response(doc)


# Bug 11 (wrong skip formula), Bug 12 (total ignores filter), no sorting, no page/page_size validation, no datetime conversion on created_at.

@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page:       int = Query(default=1,  ge=1),
    page_size:  int = Query(default=20, ge=1, le=100),
):
    query = {}
    if department:
        query["department"] = department

    skip  = (page - 1) * page_size
    total = db.employees.count_documents(query)

    items = list(
        db.employees
        .find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )

    items = [employee_to_response(doc) for doc in items]

    return {"items": items, "total": total, "page": page, "page_size": page_size}

# --- Attendance ---
#Bug 13 (no null check for unknown employee), Bug 14 (naive local time), Bug 15 (race condition), Bug 16 (forbidden id field in response).

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    # Step 1 — verify employee exists
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail=f"Employee {body.emp_code!r} not found")

    # Step 2 — convert timestamp to aware UTC datetime
    if body.punched_at is not None:
        punch_in_utc = ms_to_utc_dt(body.punched_at)
    else:
        punch_in_utc = datetime.now(timezone.utc)

    # Step 3 — truncate to whole seconds (R1)
    punch_in_utc = truncate_to_seconds(punch_in_utc)

    # Step 4 — derive IST attendance date (handles overnight shifts)
    attendance_date = compute_attendance_date(
        punch_in_utc, emp["shift_start"], emp["shift_end"]
    )

    # Step 5 — compute late minutes (R2)
    late_minutes = compute_late_minutes(punch_in_utc, emp["shift_start"])

    # Step 6 — build the document
    doc = {
        "emp_code":         body.emp_code,
        "date":             attendance_date,
        "status":           body.status,
        "punch_in":         punch_in_utc,
        "punch_out":        None,
        "work_hours":       None,
        "late_minutes":     late_minutes,
        "overtime_minutes": 0,
        "half_day":         False,
        "history":          [],
    }

    # Step 7 — insert with concurrency safety
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail=f"Employee {body.emp_code!r} already punched in for {attendance_date}"
        )

    # Step 8 — return in API format
    return record_to_response(doc)

#POST  /attendance/punch-out
@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    # Step 1 — verify employee exists
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail=f"Employee {body.emp_code!r} not found")

    # Step 2 — convert and truncate punch-out timestamp
    if body.punched_at is not None:
        punch_out_utc = ms_to_utc_dt(body.punched_at)
    else:
        punch_out_utc = datetime.now(timezone.utc)

    punch_out_utc = truncate_to_seconds(punch_out_utc)

    # Step 3 — find the most recent open record before this punch-out time
    
    """open_record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_out": None,
            "punch_in":  {"$lte": punch_out_utc},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if open_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No open punch-in found for {body.emp_code!r} at or before the given time"
        )"""    
    # Step 3 — find the most recent open record at or before punch_out time
    open_record = db.attendance_logs.find_one(
        {
            "emp_code":  body.emp_code,
            "punch_out": None,
            "punch_in":  {"$lte": punch_out_utc},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if open_record is None:
        # No open record found — find out why for a better error message
        latest = db.attendance_logs.find_one(
            {"emp_code": body.emp_code},
            sort=[("punch_in", DESCENDING)],
        )

        if latest is None:
            # Employee has no attendance records at all
            raise HTTPException(
                status_code=404,
                detail=f"No punch-in found for {body.emp_code!r}"
            )

        if latest.get("punch_out") is not None:
            # Latest record is already closed
            raise HTTPException(
                status_code=409,
                detail="Already punched out for this record"
            )

        # Latest record is open but punch_out_utc is before its punch_in
        # Let Step 5 handle this — it will return 422
        open_record = latest
            
    # Step 4 — get the stored punch_in from the record
    punch_in_utc = open_record["punch_in"]
    if punch_in_utc.tzinfo is None:
        punch_in_utc = punch_in_utc.replace(tzinfo=timezone.utc)

    # Step 5 — validate punch-out time
    if punch_out_utc <= punch_in_utc:
        raise HTTPException(
            status_code=422,
            detail="punch_out must be after punch_in"
        )

    diff_seconds = (punch_out_utc - punch_in_utc).total_seconds()
    if diff_seconds > 86400:
        raise HTTPException(
            status_code=422,
            detail="punch_out must be within 24 hours of punch_in"
        )

    # Step 6 — compute derived values
    work_hours        = compute_work_hours(punch_in_utc, punch_out_utc)
    overtime_minutes  = compute_overtime_minutes(
        punch_out_utc, emp["shift_end"], open_record["date"]
    )
    half_day          = compute_half_day(work_hours)

    # Step 7 — atomic conditional update
    # Filter includes punch_out=None so only an open record matches.
    # If two punch-outs arrive simultaneously, only one finds punch_out=None.
    updated = db.attendance_logs.find_one_and_update(
        {
            "emp_code": body.emp_code,
            "date":     open_record["date"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out":        punch_out_utc,
                "work_hours":       work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day":         half_day,
            }
        },
        return_document=True,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Already punched out — concurrent request beat you to it"
        )

    return record_to_response(updated)

#PATCH /attendance/{emp_code}/{date}
@app.patch("/attendance/{emp_code}/{date}", status_code=200)
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    # Step 1 — verify employee exists
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail=f"Employee {emp_code!r} not found")

    # Step 2 — fetch the existing record
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No attendance record for {emp_code!r} on {date}"
        )

    # Step 3 — validate the request
    new_status    = body.status
    new_punch_in  = ms_to_utc_dt(body.punch_in)  if body.punch_in  is not None else None
    new_punch_out = ms_to_utc_dt(body.punch_out) if body.punch_out is not None else None

    # ABSENT/LEAVE clears punch times — cannot supply punch times with them
    if new_status in ("ABSENT", "LEAVE"):
        if new_punch_in is not None or new_punch_out is not None:
            raise HTTPException(
                status_code=422,
                detail="Cannot supply punch_in or punch_out with ABSENT or LEAVE status"
            )

    # Presence status requires a punch_in (supplied or already stored)
    presence = ("PRESENT", "WFH", "ON_DUTY")
    final_status = new_status if new_status is not None else record["status"]

    if final_status in presence:
        existing_punch_in = record.get("punch_in")
        if new_punch_in is None and existing_punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires a punch_in"
            )

    # Step 4 — build the final field values after applying changes
    # Start from existing values and overlay supplied ones
    existing_punch_in  = record.get("punch_in")
    existing_punch_out = record.get("punch_out")

    if existing_punch_in and existing_punch_in.tzinfo is None:
        existing_punch_in = existing_punch_in.replace(tzinfo=timezone.utc)
    if existing_punch_out and existing_punch_out.tzinfo is None:
        existing_punch_out = existing_punch_out.replace(tzinfo=timezone.utc)

    final_status    = new_status    if new_status    is not None else record["status"]
    final_punch_in  = new_punch_in  if new_punch_in  is not None else existing_punch_in
    final_punch_out = new_punch_out if new_punch_out is not None else existing_punch_out

    # ABSENT/LEAVE clears both punch times regardless
    if final_status in ("ABSENT", "LEAVE"):
        final_punch_in  = None
        final_punch_out = None

    # Validate punch_in stays on the attendance date (R1)
    if final_punch_in is not None:
        final_punch_in = truncate_to_seconds(final_punch_in)
        punch_in_date  = compute_attendance_date(
            final_punch_in, emp["shift_start"], emp["shift_end"]
        )
        if punch_in_date != date:
            raise HTTPException(
                status_code=422,
                detail=f"punch_in must remain on the attendance date {date}"
            )

    # Validate punch_out is after punch_in and within 24 hours
    if final_punch_out is not None:
        final_punch_out = truncate_to_seconds(final_punch_out)
        if final_punch_in is None:
            raise HTTPException(status_code=422, detail="Cannot have punch_out without punch_in")
        if final_punch_out <= final_punch_in:
            raise HTTPException(status_code=422, detail="punch_out must be after punch_in")
        diff = (final_punch_out - final_punch_in).total_seconds()
        if diff > 86400:
            raise HTTPException(status_code=422, detail="punch_out must be within 24 hours of punch_in")

    # Step 5 — recompute derived values from final times
    if final_punch_in is not None:
        final_late_minutes = compute_late_minutes(final_punch_in, emp["shift_start"])
    else:
        final_late_minutes = 0

    if final_punch_in is not None and final_punch_out is not None:
        final_work_hours       = compute_work_hours(final_punch_in, final_punch_out)
        final_overtime_minutes = compute_overtime_minutes(
            final_punch_out, emp["shift_end"], date
        )
        final_half_day         = compute_half_day(final_work_hours)
    else:
        final_work_hours       = None
        final_overtime_minutes = 0
        final_half_day         = False

    # Step 6 — detect which fields actually changed
    # Compare final values against what is currently stored
    old_punch_in  = existing_punch_in
    old_punch_out = existing_punch_out
    old_work_hours       = record.get("work_hours")
    old_late_minutes     = record.get("late_minutes", 0)
    old_overtime_minutes = record.get("overtime_minutes", 0)
    old_half_day         = record.get("half_day", False)
    old_status           = record["status"]

    changes = {}

    if final_status != old_status:
        changes["status"] = {"from": old_status, "to": final_status}

    if final_punch_in != old_punch_in:
        changes["punch_in"] = {"from": old_punch_in, "to": final_punch_in}

    if final_punch_out != old_punch_out:
        changes["punch_out"] = {"from": old_punch_out, "to": final_punch_out}

    if final_work_hours != old_work_hours:
        changes["work_hours"] = {"from": old_work_hours, "to": final_work_hours}

    if final_late_minutes != old_late_minutes:
        changes["late_minutes"] = {"from": old_late_minutes, "to": final_late_minutes}

    if final_overtime_minutes != old_overtime_minutes:
        changes["overtime_minutes"] = {"from": old_overtime_minutes, "to": final_overtime_minutes}

    if final_half_day != old_half_day:
        changes["half_day"] = {"from": old_half_day, "to": final_half_day}

    # A request that changes nothing is 422
    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Request makes no changes to the existing record"
        )

    # Step 7 — build the history entry
    history_entry = {
        "at":      datetime.now(timezone.utc),
        "by":      body.regularized_by,
        "reason":  body.reason,
        "changes": changes,
    }

    # Step 8 — atomic conditional update
    # Use history length as optimistic concurrency version
    original_history_length = len(record.get("history", []))

    updated = db.attendance_logs.find_one_and_update(
        {
            "emp_code": emp_code,
            "date":     date,
            "history":  {"$size": original_history_length},
        },
        {
            "$set": {
                "status":           final_status,
                "punch_in":         final_punch_in,
                "punch_out":        final_punch_out,
                "work_hours":       final_work_hours,
                "late_minutes":     final_late_minutes,
                "overtime_minutes": final_overtime_minutes,
                "half_day":         final_half_day,
            },
            "$push": {"history": history_entry},
        },
        return_document=True,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Concurrent correction detected — please retry"
        )

    return record_to_response(updated)

#Bug 17 (entire collection loaded into Python memory), Bug 18 (forbidden id field), Bug 19 (no maximum on page_size), Bug 20 (no minimum on page), no date_from > date_to validation, no sort by emp_code, status accepts any string.

@app.get("/attendance")
def list_attendance(
    emp_code:  Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    status:    Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page:      int = Query(default=1,  ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    # Validate date range
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must not be after date_to"
        )

    # Build filter query
    query = {}
    if emp_code:
        query["emp_code"] = emp_code
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    if status:
        query["status"] = status

    # Count and paginate in the database
    total = db.attendance_logs.count_documents(query)
    skip  = (page - 1) * page_size

    items = list(
        db.attendance_logs
        .find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )

    items = [record_to_response(doc) for doc in items]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --- Analytics ---
#GET   /analytics/employees/{emp_code}/monthly
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    # Verify employee exists
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail=f"Employee {emp_code!r} not found")

    # Get month boundaries
    first_day, last_day, year, month_num = get_month_bounds(month)

    # Count working days in Python (R7)
    working_days = count_working_days(first_day, last_day, emp["joined_on"])

    # Aggregation pipeline
    pipeline = [
        # Stage 1 — filter to this employee and this month
        {
            "$match": {
                "emp_code": emp_code,
                "date":     {"$gte": first_day, "$lte": last_day},
            }
        },

        # Stage 2 — group all matching records into one summary document
        {
            "$group": {
                "_id": None,

                # present_days: presence status + weekday only
                # half_day=True counts 0.5, half_day=False counts 1.0
                # Weekend records are excluded using $dayOfWeek
                # $dayOfWeek: 1=Sunday, 2=Monday, ..., 7=Saturday
                # Weekdays are 2-6 (Mon-Fri)
                "present_days": {
                    "$sum": {
                        "$cond": {
                            "if": {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$gte": [
                                        {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                                        2
                                    ]},
                                    {"$lte": [
                                        {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                                        6
                                    ]},
                                ]
                            },
                            "then": {
                                "$cond": {
                                    "if":   {"$eq": ["$half_day", True]},
                                    "then": 0.5,
                                    "else": 1.0,
                                }
                            },
                            "else": 0,
                        }
                    }
                },

                # leave_days: count of LEAVE records
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },

                # late_count: records where late_minutes > 0
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },

                # total_late_minutes: sum of all late_minutes
                "total_late_minutes": {"$sum": "$late_minutes"},

                # total_overtime_minutes: sum of all overtime_minutes
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    # If no records exist for this month, all counts are zero
    if results:
        row = results[0]
    else:
        row = {
            "present_days":          0,
            "leave_days":            0,
            "late_count":            0,
            "total_late_minutes":    0,
            "total_overtime_minutes": 0,
        }

    # present_days = row["present_days"]
    present_days = half_up(row["present_days"], 2)

    # Compute attendance_pct
    if working_days > 0:
        attendance_pct = half_up(present_days / working_days * 100, 2)
    else:
        attendance_pct = None

    return {
        "emp_code":               emp_code,
        "month":                  month,
        "working_days":           working_days,
        "present_days":           present_days,
        "leave_days":             row["leave_days"],
        "late_count":             row["late_count"],
        "total_late_minutes":     row["total_late_minutes"],
        "total_overtime_minutes": row["total_overtime_minutes"],
        "attendance_pct":         attendance_pct,
    }
    
#GET   /analytics/departments/summary
@app.get("/analytics/departments/summary")
def department_summary(
    month:      str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    first_day, last_day, year, month_num = get_month_bounds(month)

    # Base filter for employees
    emp_filter = {"joined_on": {"$lte": last_day}}
    if department:
        emp_filter["department"] = department

    pipeline = [
        # Stage 1 — filter employees eligible for this month (R9)
        # joined_on <= last day of month
        {"$match": emp_filter},

        # Stage 2 — join attendance logs for this month
        {
            "$lookup": {
                "from": "attendance_logs",
                "let":  {"emp": "$emp_code"},
                "pipeline": [
                    {"$match": {
                        "$expr": {
                            "$and": [
                                {"$eq":  ["$emp_code", "$$emp"]},
                                {"$gte": ["$date", first_day]},
                                {"$lte": ["$date", last_day]},
                            ]
                        }
                    }}
                ],
                "as": "logs",
            }
        },

        # Stage 3 — unwind logs so each log becomes its own document
        # preserveNullAndEmpty keeps employees with no logs
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True,
            }
        },

        # Stage 4 — group by department
        {
            "$group": {
                "_id": "$department",

                # headcount — count distinct employees
                "headcount": {"$addToSet": "$emp_code"},

                # present_days — presence status + weekday + half day rule
                "present_days": {
                    "$sum": {
                        "$cond": {
                            "if": {
                                "$and": [
                                    {"$in": [
                                        {"$ifNull": ["$logs.status", ""]},
                                        ["PRESENT", "WFH", "ON_DUTY"]
                                    ]},
                                    {"$gte": [
                                        {"$dayOfWeek": {
                                            "$dateFromString": {
                                                "dateString": {"$ifNull": ["$logs.date", "2000-01-01"]}
                                            }
                                        }},
                                        2
                                    ]},
                                    {"$lte": [
                                        {"$dayOfWeek": {
                                            "$dateFromString": {
                                                "dateString": {"$ifNull": ["$logs.date", "2000-01-01"]}
                                            }
                                        }},
                                        6
                                    ]},
                                ]
                            },
                            "then": {
                                "$cond": {
                                    "if":   {"$eq": ["$logs.half_day", True]},
                                    "then": 0.5,
                                    "else": 1.0,
                                }
                            },
                            "else": 0,
                        }
                    }
                },

                # avg_work_hours — collect work_hours values for present records
                "work_hours_list": {
                    "$push": {
                        "$cond": {
                            "if": {
                                "$and": [
                                    {"$in": [
                                        {"$ifNull": ["$logs.status", ""]},
                                        ["PRESENT", "WFH", "ON_DUTY"]
                                    ]},
                                    {"$gt": [{"$ifNull": ["$logs.work_hours", None]}, None]},
                                ]
                            },
                            "then": "$logs.work_hours",
                            "else": "$$REMOVE",
                        }
                    }
                },

                # late_count
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},
                            1, 0
                        ]
                    }
                },

                # total_late_minutes
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },

                # leave_count
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": [{"$ifNull": ["$logs.status", ""]}, "LEAVE"]},
                            1, 0
                        ]
                    }
                },

                # on_duty_count
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": [{"$ifNull": ["$logs.status", ""]}, "ON_DUTY"]},
                            1, 0
                        ]
                    }
                },
            }
        },

        # Stage 5 — compute headcount size and avg_work_hours
        {
            "$addFields": {
                "headcount": {"$size": "$headcount"},
                "avg_work_hours": {
                    "$cond": {
                        "if":   {"$gt": [{"$size": "$work_hours_list"}, 0]},
                        "then": {"$avg": "$work_hours_list"},
                        "else": None,
                    }
                },
            }
        },

        # Stage 6 — shape the output
        {
            "$project": {
                "_id":                0,
                "department":         "$_id",
                "headcount":          1,
                "present_days":       1,
                "avg_work_hours":     1,
                "late_count":         1,
                "total_late_minutes": 1,
                "leave_count":        1,
                "on_duty_count":      1,
            }
        },

        # Stage 7 — sort by department ascending
        {"$sort": {"department": ASCENDING}},
    ]

    results = list(db.employees.aggregate(pipeline))

    # Apply half-up rounding to avg_work_hours
    for item in results:
        if item["avg_work_hours"] is not None:
            item["avg_work_hours"] = half_up(item["avg_work_hours"], 2)
        item["present_days"] = half_up(item["present_days"], 2)

    return {"month": month, "items": results}

#GET   /analytics/leaderboard/late
@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month:      str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit:      int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    first_day, last_day, _, _ = get_month_bounds(month)

    # Step 1 — build employee filter
    emp_filter = {}
    if department:
        emp_filter["department"] = department

    pipeline = [
        # Stage 1 — start from employees to get name and department
        {"$match": emp_filter},

        # Stage 2 — join attendance logs for this month
        {
            "$lookup": {
                "from": "attendance_logs",
                "let":  {"emp": "$emp_code"},
                "pipeline": [
                    {"$match": {
                        "$expr": {
                            "$and": [
                                {"$eq":  ["$emp_code", "$$emp"]},
                                {"$gte": ["$date",     first_day]},
                                {"$lte": ["$date",     last_day]},
                                {"$gt":  ["$late_minutes", 0]},
                            ]
                        }
                    }}
                ],
                "as": "logs",
            }
        },

        # Stage 3 — group by employee to get totals
        {
            "$group": {
                "_id":                "$emp_code",
                "name":               {"$first": "$name"},
                "department":         {"$first": "$department"},
                "total_late_minutes": {"$sum":   {"$ifNull": [{"$sum": "$logs.late_minutes"}, 0]}},
                "late_count":         {"$sum":   {"$size":   "$logs"}},
            }
        },

        # Stage 4 — keep only employees who were actually late
        {"$match": {"total_late_minutes": {"$gt": 0}}},

        # Stage 5 — assign competition ranks using $setWindowFields
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },

        # Stage 6 — apply limit after ranking
        # Keep only employees whose rank <= limit
        {"$match": {"rank": {"$lte": limit}}},

        # Stage 7 — sort by total_late_minutes desc, emp_code asc
        # emp_code is still in _id at this point — renamed to emp_code in Stage 8
        {
            "$sort": {
                "total_late_minutes": DESCENDING,
                "_id":                ASCENDING,
                #"emp_code":           ASCENDING,
            }
        },

        # Stage 8 — shape the output
        {
            "$project": {
                "_id":                0,
                "rank":               1,
                "emp_code":           "$_id",
                "name":               1,
                "department":         1,
                "total_late_minutes": 1,
                "late_count":         1,
            }
        },
    ]

    results = list(db.employees.aggregate(pipeline))
    return {"month": month, "items": results}


#GET   /analytics/departments/{department}/trend
@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date:  str = Query(alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    to_date:    str = Query(alias="to",   pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
):
    # Validate date range
    if to_date < from_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must not be before 'from'"
        )

    from_dt = date.fromisoformat(from_date)
    to_dt   = date.fromisoformat(to_date)

    if (to_dt - from_dt).days > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days"
        )

    # Verify department exists
    emp_exists = db.employees.find_one({"department": department})
    if emp_exists is None:
        raise HTTPException(
            status_code=404,
            detail=f"Department {department!r} not found"
        )

    # Convert date strings to UTC datetimes for $densify
    from_datetime = datetime(
        from_dt.year, from_dt.month, from_dt.day, tzinfo=timezone.utc
    )
    to_datetime = datetime(
        to_dt.year, to_dt.month, to_dt.day, tzinfo=timezone.utc
    ) + timedelta(days=1)

    pipeline = [

        # ----------------------------------------------------------------
        # Stage 1 — filter attendance logs to the requested date range
        # ----------------------------------------------------------------
        {
            "$match": {
                "date": {"$gte": from_date, "$lte": to_date}
            }
        },

        # ----------------------------------------------------------------
        # Stage 2 — join employee to verify department membership
        # ----------------------------------------------------------------
        {
            "$lookup": {
                "from":         "employees",
                "localField":   "emp_code",
                "foreignField": "emp_code",
                "as":           "emp",
            }
        },
        {"$unwind": "$emp"},

        # ----------------------------------------------------------------
        # Stage 3 — keep only logs belonging to this department
        # ----------------------------------------------------------------
        {"$match": {"emp.department": department}},

        # ----------------------------------------------------------------
        # Stage 4 — convert date string to datetime for $densify
        # ----------------------------------------------------------------
        {
            "$addFields": {
                "date_dt": {
                    "$dateFromString": {"dateString": "$date"}
                }
            }
        },

        # ----------------------------------------------------------------
        # Stage 5 — group by date to get daily attendance stats
        # ----------------------------------------------------------------
        {
            "$group": {
                "_id": "$date_dt",
                "date_str": {"$first": "$date"},
                "present_count": {
                    "$sum": {
                        "$cond": {
                            "if": {
                                "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
                            },
                            "then": {
                                "$cond": {
                                    "if":   {"$eq": ["$half_day", True]},
                                    "then": 0.5,
                                    "else": 1.0,
                                }
                            },
                            "else": 0,
                        }
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
            }
        },

        # ----------------------------------------------------------------
        # Stage 6 — fill in missing dates using $densify
        # Creates empty documents for dates with no attendance records
        # ----------------------------------------------------------------
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step":   1,
                    "unit":   "day",
                    "bounds": [from_datetime, to_datetime],
                }
            }
        },

        # ----------------------------------------------------------------
        # Stage 7 — replace nulls from $densify with zero defaults
        # ----------------------------------------------------------------
        {
            "$addFields": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count":    {"$ifNull": ["$late_count",    0]},
                "date_str": {
                    "$ifNull": [
                        "$date_str",
                        {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date":   "$_id"
                            }
                        }
                    ]
                },
            }
        },

        # ----------------------------------------------------------------
        # Stage 8 — sort by date ascending (required for window functions)
        # ----------------------------------------------------------------
        {"$sort": {"_id": 1}},

        # ----------------------------------------------------------------
        # Stage 9 — compute headcount per day inside MongoDB
        # Joins employees collection and counts those who joined
        # on or before this specific day (R9)
        # ----------------------------------------------------------------
        {
            "$lookup": {
                "from": "employees",
                "let":  {"day": "$_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {
                                        "$lte": [
                                            {
                                                "$dateFromString": {
                                                    "dateString": "$joined_on"
                                                }
                                            },
                                            "$$day"
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    {"$count": "n"}
                ],
                "as": "headcount_arr"
            }
        },

        # ----------------------------------------------------------------
        # Stage 10 — extract headcount from the array
        # ----------------------------------------------------------------
        {
            "$addFields": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_arr.n", 0]},
                        0
                    ]
                }
            }
        },

        # ----------------------------------------------------------------
        # Stage 11 — compute is_working_day and attendance_rate
        # $dayOfWeek: 1=Sunday, 2=Monday ... 6=Friday, 7=Saturday
        # Working days are 2 through 6 (Monday to Friday)
        # attendance_rate is null on weekends or when headcount is 0
        # A working day with no records has rate 0.0 (present_count=0)
        # ----------------------------------------------------------------
        {
            "$addFields": {
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$_id"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$_id"}, 6]},
                    ]
                },
                "attendance_rate": {
                    "$cond": {
                        "if": {
                            "$and": [
                                {"$gte": [{"$dayOfWeek": "$_id"}, 2]},
                                {"$lte": [{"$dayOfWeek": "$_id"}, 6]},
                                {"$gt":  ["$headcount", 0]},
                            ]
                        },
                        "then": {
                            "$divide": ["$present_count", "$headcount"]
                        },
                        "else": None,
                    }
                },
            }
        },

        # ----------------------------------------------------------------
        # Stage 12 — compute 7-day moving average using $setWindowFields
        # Window: current document and up to 6 preceding documents
        # $avg skips null attendance_rate values automatically
        # Only documents within the requested range are in the pipeline
        # so the window never reaches before from_date
        # ----------------------------------------------------------------
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0]
                        }
                    }
                }
            }
        },

        # ----------------------------------------------------------------
        # Stage 13 — shape the final output
        # ----------------------------------------------------------------
        {
            "$project": {
                "_id":             0,
                "date":            "$date_str",
                "is_working_day":  1,
                "headcount":       1,
                "present_count":   1,
                "late_count":      1,
                "attendance_rate": 1,
                "moving_avg_7d":   1,
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    # Apply half-up rounding to rate fields (4 decimal places per R8)
    for row in results:
        if row["attendance_rate"] is not None:
            row["attendance_rate"] = half_up(row["attendance_rate"], 4)
        if row["moving_avg_7d"] is not None:
            row["moving_avg_7d"] = half_up(row["moving_avg_7d"], 4)

    return {"department": department, "items": results}

#GET   /admin/explain/{endpoint}
@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint:   str,
    emp_code:   Optional[str] = None,
    month:      Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit:      int = Query(default=10, ge=1, le=50),
    date_from:  Optional[str] = None,
    date_to:    Optional[str] = None,
    status:     Optional[Literal["PRESENT","ABSENT","LEAVE","WFH","ON_DUTY"]] = None,
    from_date:  Optional[str] = Query(default=None, alias="from"),
    to_date:    Optional[str] = Query(default=None, alias="to"),
    page:       int = Query(default=1,  ge=1),
    page_size:  int = Query(default=20, ge=1, le=100),
):
    valid_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }
    if endpoint not in valid_endpoints:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown endpoint {endpoint!r}"
        )

    # ----------------------------------------------------------------
    # attendance_list
    # ----------------------------------------------------------------
    if endpoint == "attendance_list":
        att_query = {}
        if emp_code:
            att_query["emp_code"] = emp_code
        if date_from or date_to:
            att_query["date"] = {}
            if date_from:
                att_query["date"]["$gte"] = date_from
            if date_to:
                att_query["date"]["$lte"] = date_to
        if status:
            att_query["status"] = status

        plan = db.command(
            "explain",
            {
                "find":       "attendance_logs",
                "filter":     att_query,
                "projection": {"_id": 0},
                "sort":       {"date": -1, "emp_code": 1},
                "skip":       (page - 1) * page_size,
                "limit":      page_size,
            },
            verbosity="executionStats",
        )

        return {
            "endpoint":   endpoint,
            "collection": "attendance_logs",
            "explain":    make_json_safe(plan),
        }

    # ----------------------------------------------------------------
    # employee_monthly
    # ----------------------------------------------------------------
    if endpoint == "employee_monthly":
        if not emp_code:
            raise HTTPException(
                status_code=422,
                detail="emp_code is required for employee_monthly"
            )
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required for employee_monthly"
            )

        first_day, last_day, _, _ = get_month_bounds(month)

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date":     {"$gte": first_day, "$lte": last_day},
                }
            },
        ]

        plan = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline":  pipeline,
                "cursor":    {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint":   endpoint,
            "collection": "attendance_logs",
            "explain":    make_json_safe(plan),
        }

    # ----------------------------------------------------------------
    # department_summary
    # ----------------------------------------------------------------
    if endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required for department_summary"
            )

        first_day, last_day, _, _ = get_month_bounds(month)

        emp_filter = {"joined_on": {"$lte": last_day}}
        if department:
            emp_filter["department"] = department

        pipeline = [
            {"$match": emp_filter},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let":  {"emp": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq":  ["$emp_code", "$$emp"]},
                                        {"$gte": ["$date", first_day]},
                                        {"$lte": ["$date", last_day]},
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "logs",
                }
            },
        ]

        plan = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline":  pipeline,
                "cursor":    {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint":   endpoint,
            "collection": "employees",
            "explain":    make_json_safe(plan),
        }

    # ----------------------------------------------------------------
    # late_leaderboard
    # ----------------------------------------------------------------
    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required for late_leaderboard"
            )

        first_day, last_day, _, _ = get_month_bounds(month)

        emp_filter = {}
        if department:
            emp_filter["department"] = department

        pipeline = [
            {"$match": emp_filter},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let":  {"emp": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq":  ["$emp_code", "$$emp"]},
                                        {"$gte": ["$date",     first_day]},
                                        {"$lte": ["$date",     last_day]},
                                        {"$gt":  ["$late_minutes", 0]},
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "logs",
                }
            },
        ]

        plan = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline":  pipeline,
                "cursor":    {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint":   endpoint,
            "collection": "employees",
            "explain":    make_json_safe(plan),
        }

    # ----------------------------------------------------------------
    # department_trend
    # ----------------------------------------------------------------
    if endpoint == "department_trend":
        if not department:
            raise HTTPException(
                status_code=422,
                detail="department is required for department_trend"
            )
        if not from_date:
            raise HTTPException(
                status_code=422,
                detail="'from' is required for department_trend"
            )
        if not to_date:
            raise HTTPException(
                status_code=422,
                detail="'to' is required for department_trend"
            )

        pipeline = [
            {
                "$match": {
                    "date": {"$gte": from_date, "$lte": to_date}
                }
            },
            {
                "$lookup": {
                    "from":         "employees",
                    "localField":   "emp_code",
                    "foreignField": "emp_code",
                    "as":           "emp",
                }
            },
            {"$unwind": "$emp"},
            {"$match":  {"emp.department": department}},
        ]

        plan = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline":  pipeline,
                "cursor":    {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint":   endpoint,
            "collection": "attendance_logs",
            "explain":    make_json_safe(plan),
        }

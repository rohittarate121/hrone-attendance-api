# Employee Attendance & Analytics API

A REST API for recording employee attendance and serving analytics reports.
Built with Python 3.11, FastAPI and MongoDB 6.0 as part of the HROne
Software Engineer Trainee assignment.

## Requirements

- Python 3.11 or newer
- MongoDB 6.0 or newer
- pip

MongoDB can be installed locally or run as a free cluster on MongoDB Atlas.

## Installation

Clone the repository and install dependencies:

```bash
git clone <your-repository-url>
cd <repository-folder>
pip install -r requirements.txt
```

## Configuration

Create a file named `.env` in the project root folder:
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db

For MongoDB Atlas, replace `MONGO_URI` with your Atlas connection string:
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net
MONGO_DB=attendance_db

Real environment variables always override the `.env` file. Do not commit
your `.env` file or any credentials to your repository.

## Starting the API

```bash
uvicorn app.main:app --port 8000
```

On Windows you can run the same command in Command Prompt or PowerShell.
The `--reload` flag can be added during development to restart automatically
on file changes:

```bash
uvicorn app.main:app --port 8000 --reload
```

## API Documentation

Once the app is running, open your browser at:
http://localhost:8000/docs

This page lists every endpoint. You can send real requests directly from
the browser without needing any extra tool.

## Loading Sample Data

To load the nine sample documents into your local database:

```bash
python sample_seed.py
```

This inserts sample employees and attendance records. Run it only once. If
you run it again on a non-empty database it may produce duplicate key errors,
which can be safely ignored.

## Endpoints

| Method | Path                                      | Description                                                      |
| ------ | ----------------------------------------- | ---------------------------------------------------------------- |
| GET    | /health                                   | MongoDB readiness probe                                          |
| POST   | /employees                                | Create an employee                                               |
| GET    | /employees                                | List employees with filtering and pagination                     |
| POST   | /attendance/punch-in                      | Record a punch-in                                                |
| POST   | /attendance/punch-out                     | Record a punch-out                                               |
| GET    | /attendance                               | List attendance records with filtering and pagination            |
| PATCH  | /attendance/{emp_code}/{date}             | Manually correct an attendance record                            |
| GET    | /analytics/employees/{emp_code}/monthly   | Monthly summary for one employee                                 |
| GET    | /analytics/departments/summary            | Attendance summary per department                                |
| GET    | /analytics/leaderboard/late               | Late-comers leaderboard with competition ranking                 |
| GET    | /analytics/departments/{department}/trend | Daily attendance trend with gap-filling and 7-day moving average |
| GET    | /admin/explain/{endpoint}                 | MongoDB execution plan for a named endpoint                      |

## Implementation Notes

All 12 endpoints are implemented. All business rules R1 through R10 are
followed as specified in the contract.

The department trend endpoint performs gap-filling using MongoDB's `$densify`,
computes per-day headcount using a `$lookup` sub-pipeline, calculates
attendance rates using `$divide`, and produces the 7-day moving average using
`$setWindowFields` — all inside the MongoDB aggregation pipeline.

Indexes are created automatically at startup. The explain endpoint uses
MongoDB's `executionStats` verbosity to verify that queries use index scans
and not collection scans.

## What is Not Included

- No Dockerfile
- No frontend
- No authentication system
- No `.env` file committed to the repository

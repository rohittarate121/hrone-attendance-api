# DECISIONS.md

**1. Which indexes were created and why, including indexes considered but rejected.**

Three indexes are created at startup. A unique index on `employees.emp_code`
enforces employee code uniqueness and makes employee lookups fast. An index on
`employees.department` speeds up department filtering in the summary and
leaderboard endpoints. A unique compound index on
`attendance_logs(emp_code, date)` enforces one record per employee per day and
prevents concurrent duplicate punch-ins at the database level. A compound index
on `attendance_logs(date DESC, emp_code ASC)` matches the sort order of the
attendance list endpoint so MongoDB can serve paginated results without scanning
the full collection. A separate single-field index on `date` alone was
considered but rejected because the list endpoint always sorts by both fields
together, making the compound index more efficient and sufficient.

**2. What happens when two identical punch-ins arrive simultaneously.**

Both requests pass employee validation and build the same document. Both call
`insert_one`. MongoDB processes them against the unique compound index on
`(emp_code, date)`. The first insert succeeds and returns 201. The second
insert violates the unique index, raises `DuplicateKeyError`, which the
application catches and returns as 409. No application-level check is needed.
The database enforces the constraint atomically with no race condition possible.

**3. What the leaderboard returns when tied employees meet the limit boundary.**

The leaderboard uses standard competition ranking via MongoDB's `$rank`
operator. Employees with equal total late minutes receive the same rank and
the next rank is skipped. The limit is applied as a filter on the rank value
(`rank <= limit`), not as a row count. If three employees tie at rank 2 and
the limit is 2, all three are returned. The response may contain more rows
than the limit value when ties occur at the boundary.

**4. How the department summary includes eligible employees with no attendance logs.**

The aggregation pipeline starts on the `employees` collection and uses a
pipeline-style `$lookup` to join only the relevant month's attendance logs.
After the lookup, `$unwind` with `preserveNullAndEmptyArrays: true` keeps
employees whose logs array is empty. These employees flow into the `$group`
stage and their `emp_code` is collected via `$addToSet` for headcount, even
though they contribute zero to any attendance count.

**5. What should change if the dataset becomes 100 times larger.**

The current indexes would remain correct but additional optimisation would
help. A partial index on `attendance_logs` covering only open records
(`punch_out: null`) would speed up punch-out lookups. The department trend
pipeline performs a per-day `$lookup` against employees which could become
slow at scale — materialising a daily headcount snapshot or caching employee
joining dates would reduce that cost. MongoDB Atlas with sharding on `emp_code`
would distribute the collection across nodes for horizontal scaling.

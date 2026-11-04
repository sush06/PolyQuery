# Constraint labels v2: review packet

Draft labels and split proposals. Automatic checks passed only when audit.json says so. No independent human approval is implied.

Review meaning before model outputs. Check all variants, predicate scope, boundary/null/date policy, grouping stage, ordering, ties, identity, and unsupported fields. Record any disagreement and adjudicate before freezing a test split.

## fare-exclude-negative

Source: taxi; label: canonical; group: fare-sign-exclusion; proposed split: validation

- `fare-exclude-negative-p1`: Average fare excluding negative fares
- `fare-exclude-negative-p2`: Calculate the mean fare after removing negative fares.
- `fare-exclude-negative-p3`: Ignore negative fare amounts when calculating the average fare.
- `fare-exclude-negative-p4`: What is the average fare among fares that are not negative?

Rationale: Keep fares >= 0: zero and positive amounts qualify; negative and null amounts do not.

- scope: `trips rows`
- aggregation: `trips.fare_amount mean`
- predicate: `trips.fare_amount ge number:"0"`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## fare-exclude-zero

Source: taxi; label: canonical; group: fare-sign-exclusion; proposed split: validation

- `fare-exclude-zero-p1`: Average fare excluding zero fares
- `fare-exclude-zero-p2`: Calculate the mean fare after removing zero fares.
- `fare-exclude-zero-p3`: Ignore zero fare amounts when calculating the average fare.
- `fare-exclude-zero-p4`: What is the average fare among fares that are not zero?

Rationale: Keep fares != 0: negative and positive amounts qualify; zero and null amounts do not.

- scope: `trips rows`
- aggregation: `trips.fare_amount mean`
- predicate: `trips.fare_amount ne number:"0"`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## fare-exclude-positive

Source: taxi; label: canonical; group: fare-sign-exclusion; proposed split: validation

- `fare-exclude-positive-p1`: Average fare excluding positive fares
- `fare-exclude-positive-p2`: Calculate the mean fare after removing positive fares.
- `fare-exclude-positive-p3`: Ignore positive fare amounts when calculating the average fare.
- `fare-exclude-positive-p4`: What is the average fare among fares that are not positive?

Rationale: Keep fares <= 0: negative amounts and zero qualify; positive and null amounts do not.

- scope: `trips rows`
- aggregation: `trips.fare_amount mean`
- predicate: `trips.fare_amount le number:"0"`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## department-not-sales

Source: synthetic; label: canonical; group: department-membership; proposed split: train

- `department-not-sales-p1`: Average salary excluding Sales
- `department-not-sales-p2`: Mean salary for employees not in Sales.
- `department-not-sales-p3`: What is the mean employee salary outside the Sales department?
- `department-not-sales-p4`: Compute average salary after omitting Sales employees.

Rationale: Keep rows where department != Sales is TRUE. Null departments yield UNKNOWN and are dropped under the explicit SQL null policy.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `employees.department ne string:"Sales"`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## not-both

Source: synthetic; label: canonical; group: boolean-negation-scope; proposed split: train

- `not-both-p1`: Average salary excluding employees who satisfy both salary > 100000 and experience < 4.
- `not-both-p2`: Mean salary where NOT (salary > 100000 AND experience < 4).
- `not-both-p3`: For the salary average, omit people only when salary exceeds 100000 and experience is below 4 together.
- `not-both-p4`: Average salary for employees for whom the conjunction of salary > 100000 and experience < 4 is false.

Rationale: Negate the entire conjunction. A row qualifies only when NOT(A AND B) is TRUE; UNKNOWN is dropped, so exclusion is not a keep-null anti-filter.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `NOT (employees.experience lt number:"4" AND employees.salary gt number:"100000")`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## not-either

Source: synthetic; label: canonical; group: boolean-negation-scope; proposed split: train

- `not-either-p1`: Average salary excluding employees who have salary > 100000 or experience < 4.
- `not-either-p2`: Mean salary where NOT (salary > 100000 OR experience < 4).
- `not-either-p3`: For average salary, retain employees satisfying neither salary > 100000 nor experience < 4.
- `not-either-p4`: Omit either high-salary employees above 100000 or low-experience employees below 4 before averaging salary.

Rationale: Negate the entire disjunction. A row qualifies only when NOT(A OR B) is TRUE; UNKNOWN is dropped, rather than treating an unknown condition as false.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `NOT (employees.experience lt number:"4" OR employees.salary gt number:"100000")`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## salary-strict

Source: synthetic; label: canonical; group: salary-boundary; proposed split: validation

- `salary-strict-p1`: Average salary for employees with salary above 100000.
- `salary-strict-p2`: Average salary for employees with salary greater than 100000.
- `salary-strict-p3`: Average salary for employees with salary > 100000.
- `salary-strict-p4`: Average salary for employees with salary strictly exceeding 100000.

Rationale: The boundary is strict for above and inclusive for at least; 100000 is the contrast witness.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `employees.salary gt number:"100000"`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## salary-inclusive

Source: synthetic; label: canonical; group: salary-boundary; proposed split: validation

- `salary-inclusive-p1`: Average salary for employees with salary at least 100000.
- `salary-inclusive-p2`: Average salary for employees with salary >= 100000.
- `salary-inclusive-p3`: Average salary for employees with salary no less than 100000.
- `salary-inclusive-p4`: Average salary for employees with salary 100000 or higher.

Rationale: The boundary is strict for above and inclusive for at least; 100000 is the contrast witness.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `employees.salary ge number:"100000"`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## fare-bounded-interval

Source: taxi; label: canonical; group: fare-interval; proposed split: train

- `fare-bounded-interval-p1`: Average fare for 0 <= fare_amount < 20.
- `fare-bounded-interval-p2`: Mean fare for amounts at least zero and below 20.
- `fare-bounded-interval-p3`: Average fare in the half-open interval [0, 20).
- `fare-bounded-interval-p4`: Calculate mean fare including zero but excluding 20 and all fares outside those bounds.

Rationale: Half-open interval: zero included, 20 excluded. No unit conversion.

- scope: `trips rows`
- aggregation: `trips.fare_amount mean`
- predicate: `(trips.fare_amount ge number:"0" AND trips.fare_amount lt number:"20")`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## path-hop-bound

Source: ldbc; label: canonical; group: ldbc-path-bounds; proposed split: train

- `path-hop-bound-p1`: Shortest undirected friendship path from 933 to 1129 using at most 3 edges.
- `path-hop-bound-p2`: Find a shortest friendship chain between 933 and 1129 with no more than three hops.
- `path-hop-bound-p3`: Connect person 933 to person 1129 by a shortest undirected path of length <= 3.
- `path-hop-bound-p4`: Return a minimum-hop undirected path from 933 to 1129, but only if its edge count is at most 3.

Rationale: At most three edges, not three vertices. Accept any tied shortest path. Return null when the global shortest path exceeds three edges or the endpoints are disconnected.

- scope: `person rows`
- traversal: `person source "933"`
- traversal: `person target "1129"`
- traversal: `person strategy "shortest_undirected"`
- traversal: `person max_hops 3`

Context: `{"answer_policy": {"direction": "undirected", "hop_unit": "edges", "max_hops": 3, "no_path_within_bound": "successful result value null; abstention/error is not a correct null result", "scorer": "bounded_shortest_path", "source": "933", "target": "1129", "ties": "any valid globally shortest path"}}`

Independent review: **pending**.

## and-around-or

Source: synthetic; label: canonical; group: boolean-precedence; proposed split: train

- `and-around-or-p1`: Average salary where department is Research AND (salary > 100000 OR experience < 4).
- `and-around-or-p2`: Mean salary for Research employees who either earn more than 100000 or have less than four years of experience.
- `and-around-or-p3`: Within Research, average salaries of employees meeting at least one of salary > 100000 and experience < 4.
- `and-around-or-p4`: Average salary after filtering to Research and then requiring salary > 100000 or experience < 4.

Rationale: Department must be Research, and at least one numeric condition must be TRUE. A qualifying numeric condition alone cannot admit a non-Research employee.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `(employees.department eq string:"Research" AND (employees.experience lt number:"4" OR employees.salary gt number:"100000"))`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## or-around-and

Source: synthetic; label: canonical; group: boolean-precedence; proposed split: train

- `or-around-and-p1`: Average salary where (department is Research AND salary > 100000) OR experience < 4.
- `or-around-and-p2`: Mean salary for either Research employees earning above 100000 or any employees with experience below 4.
- `or-around-and-p3`: Average salaries for the union of high-paid Research employees above 100000 and all employees with less than four years experience.
- `or-around-and-p4`: Average salary after applying (Research AND salary > 100000) OR (experience < 4).

Rationale: The Research restriction applies only to the salary > 100000 branch. Any employee satisfying experience < 4 qualifies through the other branch.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `(employees.experience lt number:"4" OR (employees.department eq string:"Research" AND employees.salary gt number:"100000"))`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## numeric-and

Source: synthetic; label: canonical; group: boolean-numeric-combinations; proposed split: train

- `numeric-and-p1`: Average salary where salary > 100000 AND experience < 4.
- `numeric-and-p2`: Mean salary for employees earning above 100000 with experience below four.
- `numeric-and-p3`: Average salaries only when both salary exceeds 100000 and experience is less than 4.
- `numeric-and-p4`: Require salary > 100000 together with experience < 4 before calculating mean salary.

Rationale: Both salary > 100000 and experience < 4 must be TRUE for a row to qualify.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `(employees.experience lt number:"4" AND employees.salary gt number:"100000")`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## numeric-or

Source: synthetic; label: canonical; group: boolean-numeric-combinations; proposed split: train

- `numeric-or-p1`: Average salary where salary > 100000 OR experience < 4.
- `numeric-or-p2`: Mean salary for employees earning above 100000 or having experience below four.
- `numeric-or-p3`: Average salaries when either salary exceeds 100000 or experience is less than 4, including employees satisfying both.
- `numeric-or-p4`: Use the union of salary > 100000 and experience < 4 when calculating average salary.

Rationale: Either salary > 100000 or experience < 4 can admit a row; satisfying both does not duplicate the row.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `(employees.experience lt number:"4" OR employees.salary gt number:"100000")`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## not-inside-or

Source: synthetic; label: canonical; group: boolean-numeric-combinations; proposed split: train

- `not-inside-or-p1`: Average salary where NOT (salary > 100000) OR experience < 4.
- `not-inside-or-p2`: Mean salary when salary is not above 100000 or experience is below four.
- `not-inside-or-p3`: Average salary for the union of salary <= 100000 and experience < 4.
- `not-inside-or-p4`: Keep employees who fail salary > 100000 or satisfy experience < 4, then average salary.

Rationale: NOT applies only to the salary > 100000 comparison. Its result is OR-ed with experience < 4; NOT does not cover the OR expression.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `(employees.experience lt number:"4" OR NOT employees.salary gt number:"100000")`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## categorical-or

Source: synthetic; label: canonical; group: department-membership; proposed split: train

- `categorical-or-p1`: Average salary in Sales or Engineering.
- `categorical-or-p2`: Mean salary for employees belonging to either Sales or Engineering.
- `categorical-or-p3`: Combine Engineering and Sales employees and calculate their mean salary.
- `categorical-or-p4`: Average salaries across the union of the Sales and Engineering departments.

Rationale: Keep the union of Sales and Engineering employees. Research employees are excluded; no employee is counted twice.

- scope: `employees rows`
- aggregation: `employees.salary mean`
- predicate: `(employees.department eq string:"Engineering" OR employees.department eq string:"Sales")`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## pickup-before

Source: taxi; label: canonical; group: pickup-date-boundary; proposed split: train

- `pickup-before-p1`: Count trips with pickup timestamp before 2026-02-01 at 00:00 as stored.
- `pickup-before-p2`: How many pickups occurred earlier than midnight at the start of 2026-02-01, without timezone conversion?
- `pickup-before-p3`: Count trips satisfying tpep_pickup_datetime < 2026-02-01 00:00 in stored wall-clock time.
- `pickup-before-p4`: Return the number of trips picked up strictly before the start of February 1, 2026, using the timestamps as stored.

Rationale: The timestamp comparison uses the start of the stated date, not the whole end date. Pickup, not dropoff.

- scope: `trips rows`
- aggregation: `trips.* count`
- predicate: `trips.tpep_pickup_datetime lt date:"2026-02-01"`

Context: `{"calendar": "ISO Gregorian", "date_literal_boundary": "00:00:00 as stored", "interval": "explicit; do not infer from file month", "null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}, "timezone_conversion": "none"}`

Independent review: **pending**.

## pickup-on-or-after

Source: taxi; label: canonical; group: pickup-date-boundary; proposed split: train

- `pickup-on-or-after-p1`: Count trips with pickup timestamp at or after 2026-02-01 00:00 as stored.
- `pickup-on-or-after-p2`: How many pickups are no earlier than midnight starting February 1, 2026, with no timezone conversion?
- `pickup-on-or-after-p3`: Count trips satisfying tpep_pickup_datetime >= 2026-02-01 00:00 in stored wall-clock time.
- `pickup-on-or-after-p4`: Include trips picked up at midnight at the start of 2026-02-01 and all later pickups; report their count.

Rationale: The timestamp comparison uses the start of the stated date, not the whole end date. Pickup, not dropoff.

- scope: `trips rows`
- aggregation: `trips.* count`
- predicate: `trips.tpep_pickup_datetime ge date:"2026-02-01"`

Context: `{"calendar": "ISO Gregorian", "date_literal_boundary": "00:00:00 as stored", "interval": "explicit; do not infer from file month", "null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}, "timezone_conversion": "none"}`

Independent review: **pending**.

## pickup-month-interval

Source: taxi; label: canonical; group: pickup-date-interval; proposed split: train

- `pickup-month-interval-p1`: Count trips picked up from 2026-01-01 00:00 inclusive to 2026-02-01 00:00 exclusive, as stored.
- `pickup-month-interval-p2`: Count pickups in [2026-01-01 00:00, 2026-02-01 00:00) with no timezone conversion.
- `pickup-month-interval-p3`: How many trips have pickup timestamps at least January 1, 2026 at midnight and before February 1 at midnight?
- `pickup-month-interval-p4`: Use stored pickup time to count trips starting on or after 2026-01-01 00:00 and strictly before 2026-02-01 00:00.

Rationale: A half-open calendar interval avoids double-counting an adjacent month boundary.

- scope: `trips rows`
- aggregation: `trips.* count`
- predicate: `(trips.tpep_pickup_datetime ge date:"2026-01-01" AND trips.tpep_pickup_datetime lt date:"2026-02-01")`

Context: `{"calendar": "ISO Gregorian", "date_literal_boundary": "00:00:00 as stored", "interval": "explicit; do not infer from file month", "null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}, "timezone_conversion": "none"}`

Independent review: **pending**.

## invoice-before

Source: chinook; label: canonical; group: invoice-date-boundary; proposed split: test

- `invoice-before-p1`: Sum invoice totals for InvoiceDate before 2010-01-01 00:00, using stored time.
- `invoice-before-p2`: Total invoice revenue strictly before midnight at the start of January 1, 2010, without timezone conversion.
- `invoice-before-p3`: Calculate SUM(Total) for invoices satisfying InvoiceDate < 2010-01-01 00:00 as stored.
- `invoice-before-p4`: Add invoice totals dated earlier than the start of 2010 in the database wall-clock timestamps.

Rationale: Filter on Invoice.InvoiceDate strictly before the stated start-of-day boundary, then sum Invoice.Total over qualifying rows.

- scope: `Invoice rows`
- aggregation: `Invoice.Total sum`
- predicate: `Invoice.InvoiceDate lt date:"2010-01-01"`

Context: `{"calendar": "ISO Gregorian", "date_literal_boundary": "00:00:00 as stored", "interval": "explicit; do not infer from file month", "null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}, "timezone_conversion": "none"}`

Independent review: **pending**.

## relative-date-unanchored

Source: taxi; label: clarify; group: relative-date-ambiguity; proposed split: train

- `relative-date-unanchored-p1`: Count trips picked up last month.
- `relative-date-unanchored-p2`: How many pickups happened yesterday?
- `relative-date-unanchored-p3`: Count recent pickups.
- `relative-date-unanchored-p4`: How many trips were picked up during the previous week?

Rationale: No reference clock, timezone, or definition of recent is supplied; ask for clarification.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Independent review: **pending**.

## country-group

Source: chinook; label: canonical; group: country-group-filter; proposed split: test

- `country-group-p1`: Total invoice revenue by billing country.
- `country-group-p2`: Sum invoice totals separately for each billing country.
- `country-group-p3`: Group invoices on BillingCountry and return the sum of Total in each group.
- `country-group-p4`: Break invoice revenue down by billing country.

Rationale: One aggregate per country; this is not a filter to a single country.

- scope: `Invoice rows`
- aggregation: `Invoice.Total sum`
- grouping: `Invoice.BillingCountry key`

Context: `{"group_policy": {"all_null_values_in_existing_group": "SUM is null, not 0", "empty_groups": "omit; never manufacture zero rows for groups removed by a row filter", "null_group_key": "one SQL NULL group if qualifying rows have a null key", "stage_order": ["row_filter", "group", "aggregate", "having_if_requested"]}, "null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## country-filter

Source: chinook; label: canonical; group: country-group-filter; proposed split: test

- `country-filter-p1`: Total invoice revenue where billing country is USA.
- `country-filter-p2`: Sum Total only for invoices billed in USA.
- `country-filter-p3`: For BillingCountry = USA, report one overall invoice revenue total.
- `country-filter-p4`: Restrict invoices to billing country USA, then sum their totals without grouping.

Rationale: One country is selected before aggregation; no GROUP BY is requested.

- scope: `Invoice rows`
- aggregation: `Invoice.Total sum`
- predicate: `Invoice.BillingCountry eq string:"USA"`

Context: `{"group_policy": {"all_null_values_in_existing_group": "SUM is null, not 0", "empty_groups": "omit; never manufacture zero rows for groups removed by a row filter", "null_group_key": "one SQL NULL group if qualifying rows have a null key", "stage_order": ["row_filter", "group", "aggregate", "having_if_requested"]}, "null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## country-group-and-filter

Source: chinook; label: canonical; group: country-group-filter; proposed split: test

- `country-group-and-filter-p1`: Sum invoice totals by billing country, excluding invoices billed in USA.
- `country-group-and-filter-p2`: Remove USA-billed invoices and then total revenue for each remaining billing country.
- `country-group-and-filter-p3`: Return SUM(Total) grouped by BillingCountry with the row filter BillingCountry != USA.
- `country-group-and-filter-p4`: Group non-USA invoices by billing country and report revenue for every resulting group.

Rationale: The exclusion is a row predicate; country is also a grouping key.

- scope: `Invoice rows`
- aggregation: `Invoice.Total sum`
- predicate: `Invoice.BillingCountry ne string:"USA"`
- grouping: `Invoice.BillingCountry key`

Context: `{"group_policy": {"all_null_values_in_existing_group": "SUM is null, not 0", "empty_groups": "omit; never manufacture zero rows for groups removed by a row filter", "null_group_key": "one SQL NULL group if qualifying rows have a null key", "stage_order": ["row_filter", "group", "aggregate", "having_if_requested"]}, "null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## invoice-row-filter-before-group

Source: chinook; label: canonical; group: group-filter-stage; proposed split: test

- `invoice-row-filter-before-group-p1`: Sum invoice totals by billing country using only invoices whose Total exceeds 5.
- `invoice-row-filter-before-group-p2`: Discard invoice rows with Total <= 5, then group by billing country and sum the remaining totals.
- `invoice-row-filter-before-group-p3`: Report country revenue with WHERE Total > 5 before grouping.
- `invoice-row-filter-before-group-p4`: For each billing country, add only individual invoice totals above 5.

Rationale: Apply Total > 5 to individual rows, not to the country sum.

- scope: `Invoice rows`
- aggregation: `Invoice.Total sum`
- predicate: `Invoice.Total gt number:"5"`
- grouping: `Invoice.BillingCountry key`

Context: `{"group_policy": {"all_null_values_in_existing_group": "SUM is null, not 0", "empty_groups": "omit; never manufacture zero rows for groups removed by a row filter", "null_group_key": "one SQL NULL group if qualifying rows have a null key", "stage_order": ["row_filter", "group", "aggregate", "having_if_requested"]}, "null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## country-having

Source: chinook; label: representation_gap; group: group-filter-stage; proposed split: test

- `country-having-p1`: Sum invoice totals by billing country and keep groups whose revenue exceeds 100.
- `country-having-p2`: Return billing-country sums with HAVING SUM(Total) > 100.
- `country-having-p3`: Calculate total revenue per billing country, then discard country totals at or below 100.
- `country-having-p4`: Group all invoices by billing country; show only groups with summed Total above 100.

Rationale: Unambiguous HAVING, not WHERE. Canonical v1 lacks post-aggregation predicate scope; do not encode it as a row predicate.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Context: `{"group_policy": {"all_null_values_in_existing_group": "SUM is null, not 0", "empty_groups": "omit; never manufacture zero rows for groups removed by a row filter", "null_group_key": "one SQL NULL group if qualifying rows have a null key", "stage_order": ["row_filter", "group", "aggregate", "having_if_requested"]}, "intended_semantics": {"aggregate": "SUM(Invoice.Total)", "group_by": "Invoice.BillingCountry", "post_aggregation_predicate": {"operator": "gt", "value": 100}}, "null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## fare-descending

Source: taxi; label: canonical; group: taxi-ordering; proposed split: train

- `fare-descending-p1`: List fare_amount values from non-null fares ordered descending.
- `fare-descending-p2`: Return non-null fare amounts from largest to smallest.
- `fare-descending-p3`: Sort non-null fare_amount values in descending order and list them.
- `fare-descending-p4`: Show non-null fares ordered by fare_amount DESC.

Rationale: The selected field and direction are explicit; null ordering does not need an implicit default.

- scope: `trips rows`
- predicate: `trips.fare_amount is_not_null`
- projection: `trips fields "fare_amount"`
- ordering: `trips.fare_amount desc`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## distance-ascending

Source: taxi; label: canonical; group: taxi-ordering; proposed split: train

- `distance-ascending-p1`: List non-null trip distances ordered ascending.
- `distance-ascending-p2`: Return non-null trip_distance values from shortest to longest.
- `distance-ascending-p3`: Sort non-null trip distances from smallest to largest.
- `distance-ascending-p4`: Show non-null trip_distance values ordered ASC.

Rationale: The selected field and direction are explicit; null ordering does not need an implicit default.

- scope: `trips rows`
- predicate: `trips.trip_distance is_not_null`
- projection: `trips fields "trip_distance"`
- ordering: `trips.trip_distance asc`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## fare-top-five

Source: taxi; label: canonical; group: taxi-ordering; proposed split: train

- `fare-top-five-p1`: Return the five largest non-null fare_amount values, in descending order.
- `fare-top-five-p2`: List non-null fare_amount values ORDER BY fare_amount DESC LIMIT 5.
- `fare-top-five-p3`: Sort non-null fares largest first and keep the first five values.
- `fare-top-five-p4`: Show at most 5 non-null fare amounts, ordered from largest to smallest.

Rationale: Five is a row limit, not a predicate fare_amount >= 5. Repeated amounts are retained.

- scope: `trips rows`
- predicate: `trips.fare_amount is_not_null`
- projection: `trips fields "fare_amount"`
- ordering: `trips.fare_amount desc`
- limit: `trips max_rows 5`

Context: `{"null_policy": {"aggregate_inputs": "Ignore null numeric inputs after filtering; COUNT rows includes every retained row", "empty_numeric_aggregate": "null, except COUNT returns 0", "logic": "sql_three_valued", "negation": "NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows", "null_comparison": "UNKNOWN except explicit IS NULL/IS NOT NULL", "row_retention": "only_TRUE"}}`

Independent review: **pending**.

## employee-two-key-order

Source: synthetic; label: representation_gap; group: employee-ordering; proposed split: train

- `employee-two-key-order-p1`: List employees by salary descending, breaking ties by name ascending; return exactly name, department, salary, and experience.
- `employee-two-key-order-p2`: Return employee rows ORDER BY salary DESC, name ASC; return exactly name, department, salary, and experience.
- `employee-two-key-order-p3`: Sort employees from highest salary to lowest, then by case-sensitive Unicode code-point name order within salary ties; return exactly name, department, salary, and experience.
- `employee-two-key-order-p4`: Order employee rows on descending salary followed by ascending name; return exactly name, department, salary, and experience.

Rationale: Salary descending is the primary key, name ascending is the tie-breaker under the stated case-sensitive code-point rule. Canonical v1 cannot encode collation, so full encoding is deferred consistently with numeric person-ID ordering.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Context: `{"collation": {"case_sensitive": true, "field": "employees.name", "locale": "none", "normalization": "none", "order": "Unicode code-point"}, "intended_semantics": {"constraints": [{"field": "employees", "kind": "scope", "operator": "rows", "predicate": null, "value": null}, {"field": "employees", "kind": "projection", "operator": "fields", "predicate": null, "value": "name,department,salary,experience"}, {"field": "employees.salary", "kind": "ordering", "operator": "desc", "predicate": null, "value": null}, {"field": "employees.name", "kind": "ordering", "operator": "asc", "predicate": null, "value": null}], "source": "synthetic", "version": 1}, "projection": {"extra_fields": "reject", "fields": ["name", "department", "salary", "experience"], "fixture_columns": ["name", "department", "salary", "experience"]}}`

Independent review: **pending**.

## employee-limit-only

Source: synthetic; label: canonical; group: employee-limit; proposed split: validation

- `employee-limit-only-p1`: List three employees if available, otherwise all employees; any subset and order is acceptable; return exactly name, department, salary, and experience.
- `employee-limit-only-p2`: Return 3 employee rows when at least 3 exist, otherwise every employee, with no ordering requirement; project exactly name, department, salary, and experience.
- `employee-limit-only-p3`: Give any three distinct employees, or all if fewer than three exist, without ranking; include exactly name, department, salary, and experience.
- `employee-limit-only-p4`: List employee records with LIMIT 3, filling the limit when possible; any order is acceptable; return exactly name, department, salary, and experience.

Rationale: Return any three distinct eligible employee rows, or all rows if fewer than three exist. Score membership and cardinality, not equality with one arbitrary reference subset or row order.

- scope: `employees rows`
- projection: `employees fields "name,department,salary,experience"`
- limit: `employees max_rows 3`

Context: `{"answer_policy": {"cardinality": "min(limit, eligible_row_count)", "duplicates": "reject", "identity": "name", "limit": 3, "membership": "full projected row must match an eligible row", "ordering": "ignored", "scorer": "unordered_valid_subset"}, "projection": {"extra_fields": "reject", "fields": ["name", "department", "salary", "experience"], "fixture_columns": ["name", "department", "salary", "experience"]}}`

Independent review: **pending**.

## ldbc-friends-ordered

Source: ldbc; label: representation_gap; group: ldbc-neighbor-order; proposed split: test

- `ldbc-friends-ordered-p1`: List friend IDs of person 933 in ascending numeric ID order, at most five IDs.
- `ldbc-friends-ordered-p2`: Return up to 5 direct undirected friends of 933, sorted by numeric person ID ascending.
- `ldbc-friends-ordered-p3`: For person 933, list the five smallest numeric IDs among direct friends.
- `ldbc-friends-ordered-p4`: List direct friend IDs of 933 ORDER BY numeric ID ASC LIMIT 5.

Rationale: IDs are stored as strings but ordered numerically. No friendship-path traversal is requested. Canonical v1 has no ordering collation/coercion field, so a full numeric-ID order label is deferred.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Context: `{"intended_semantics": {"constraints": [{"field": "person", "kind": "scope", "operator": "rows", "predicate": null, "value": null}, {"field": "person", "kind": "traversal", "operator": "source", "predicate": null, "value": "933"}, {"field": "person", "kind": "traversal", "operator": "strategy", "predicate": null, "value": "direct_undirected"}, {"field": "person", "kind": "projection", "operator": "fields", "predicate": null, "value": "id"}, {"field": "person.id", "kind": "ordering", "operator": "asc", "predicate": null, "value": null}, {"field": "person", "kind": "limit", "operator": "max_rows", "predicate": null, "value": 5}], "source": "ldbc", "version": 1}, "person_id_order": "numeric, arbitrary precision"}`

Independent review: **pending**.

## ambiguous-money-field

Source: taxi; label: clarify; group: ambiguity-money-field; proposed split: test

- `ambiguous-money-field-p1`: What is the average amount?
- `ambiguous-money-field-p2`: Calculate the mean charge.
- `ambiguous-money-field-p3`: What is the typical payment amount?
- `ambiguous-money-field-p4`: Average the money values.

Rationale: Specify fare_amount versus total_amount or another monetary column.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Independent review: **pending**.

## ambiguous-timestamp-field

Source: taxi; label: clarify; group: ambiguity-timestamp-field; proposed split: train

- `ambiguous-timestamp-field-p1`: Count trips before 2026-02-01.
- `ambiguous-timestamp-field-p2`: How many trips happened on January 15, 2026?
- `ambiguous-timestamp-field-p3`: Filter trips after midnight on February 1, 2026.
- `ambiguous-timestamp-field-p4`: Count trips dated before 2026-01-15.

Rationale: Specify pickup versus dropoff timestamp, boundary semantics, and time basis.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Independent review: **pending**.

## ambiguous-mean-or-median

Source: synthetic; label: clarify; group: ambiguity-mean-or-median; proposed split: validation

- `ambiguous-mean-or-median-p1`: What is a typical salary?
- `ambiguous-mean-or-median-p2`: Give the normal employee pay.
- `ambiguous-mean-or-median-p3`: What salary is representative?
- `ambiguous-mean-or-median-p4`: Find the usual salary.

Rationale: Typical/normal/usual does not uniquely choose mean or median.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Independent review: **pending**.

## ambiguous-employee-attribute

Source: synthetic; label: clarify; group: ambiguity-employee-attribute; proposed split: validation

- `ambiguous-employee-attribute-p1`: Show the highest employees.
- `ambiguous-employee-attribute-p2`: Rank employees by performance.
- `ambiguous-employee-attribute-p3`: Who are the best employees?
- `ambiguous-employee-attribute-p4`: List the top employees.

Rationale: Ranking field, direction, and often limit are unspecified; performance is not a fixture field.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Independent review: **pending**.

## ambiguous-invoice-country

Source: chinook; label: clarify; group: ambiguity-invoice-country; proposed split: train

- `ambiguous-invoice-country-p1`: Revenue by country.
- `ambiguous-invoice-country-p2`: Show country totals.
- `ambiguous-invoice-country-p3`: Group sales geographically by country.
- `ambiguous-invoice-country-p4`: Give total revenue for each country.

Rationale: Under this annotation protocol, country must specify Invoice.BillingCountry versus Customer.Country; do not assume equality.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Independent review: **pending**.

## ambiguous-ldbc-endpoints

Source: ldbc; label: clarify; group: ambiguity-ldbc-endpoints; proposed split: train

- `ambiguous-ldbc-endpoints-p1`: Find a shortest path between them.
- `ambiguous-ldbc-endpoints-p2`: How are those two people connected?
- `ambiguous-ldbc-endpoints-p3`: Show the friendship chain for the pair.
- `ambiguous-ldbc-endpoints-p4`: Find their shortest connection.

Rationale: No antecedents/entity IDs are supplied in this single-turn task.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Independent review: **pending**.

## ambiguous-ldbc-name-identity

Source: ldbc; label: clarify; group: ambiguity-ldbc-name-identity; proposed split: train

- `ambiguous-ldbc-name-identity-p1`: List friends of Aaron.
- `ambiguous-ldbc-name-identity-p2`: Who knows Aaron?
- `ambiguous-ldbc-name-identity-p3`: Show the neighbors of Aaron.
- `ambiguous-ldbc-name-identity-p4`: Find friends of Aaron.

Rationale: The supplied people file contains two first-name Aaron matches. A first name does not select one person ID; request disambiguation.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Independent review: **pending**.

## ambiguous-boolean-no-parentheses

Source: synthetic; label: clarify; group: ambiguity-boolean-no-parentheses; proposed split: train

- `ambiguous-boolean-no-parentheses-p1`: Average salary for Research and Sales employees with experience below 4.
- `ambiguous-boolean-no-parentheses-p2`: Mean salary for Sales or Engineering employees earning above 100000.
- `ambiguous-boolean-no-parentheses-p3`: Average salaries of Research employees or Sales employees with salary above 100000.
- `ambiguous-boolean-no-parentheses-p4`: Mean salary for Engineering or Research employees with experience over 5.

Rationale: Modifier attachment can have more than one reasonable reading. Request parentheses or explicit scope under the conservative annotation protocol.

No full canonical label: clarify or extend representation; do not invent a forced interpretation.

Independent review: **pending**.

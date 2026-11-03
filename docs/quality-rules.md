# Data quality configuration

Create `.flowlens/quality.yml` or change `flowlens.qualityConfig`. The file is read only when the user runs **FlowLens: Validate Dataset**. CSV and Parquet inputs are queried read-only through DuckDB. Checks never rewrite source data. Failed samples are returned to the user but are not written to the SQLite history; history keeps aggregate pass/fail counts.

```yaml
dataset: customers.csv
checks:
  - column: customer_id
    rule: not_null
  - column: customer_id
    rule: unique
  - column: country
    rule: accepted_values
    values: [US, CA]
  - column: age
    rule: range
    min: 0
    max: 120
  - column: email
    rule: regex
    pattern: '^[^@]+@[^@]+$'
  - column: customer_id
    rule: type_check
    type: integer
  - rule: row_count
    min: 1
    max: 1000000
  - column: country
    rule: referential_integrity
    reference_dataset: countries.csv
    reference_column: country_code
  - rule: custom_sql
    sql: SELECT * FROM dataset WHERE age < 0
```

`custom_sql` must be exactly one read-only `SELECT` and may only read the selected `dataset` view and its CTEs; rows returned by it represent failed records. It cannot use external table functions or other database tables. Supported built-in rules are `not_null`, `unique`, `accepted_values`, `range`, `regex`, `type_check`, `row_count`, and `referential_integrity`. `type_check` uses DuckDB `TRY_CAST` for `INTEGER`/`INT`/`BIGINT`/`SMALLINT`, `STRING`/`TEXT`/`VARCHAR`, `FLOAT`/`DOUBLE`/`REAL`, `DECIMAL`, `BOOLEAN`/`BOOL`, `DATE`, and `TIMESTAMP`; nulls are left to `not_null`. The `dataset` key may match the selected filename, stem, or workspace-relative path. Use an explicit path if multiple matching dataset names exist.

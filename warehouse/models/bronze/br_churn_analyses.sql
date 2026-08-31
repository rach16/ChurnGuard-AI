-- Raw landing: source data as it arrived, typed by DuckDB, nothing reshaped.
-- Kept as a view so bronze costs nothing to materialise and always reflects the file.
select * from {{ source('raw', 'churn_analyses') }}

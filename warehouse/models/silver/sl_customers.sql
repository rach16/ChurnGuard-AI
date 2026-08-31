-- Conformed customer dimension source: types cast, empty strings normalised to
-- null, and the numeric id the API exposes derived from the business key.
select
    customer_id,
    cast(regexp_extract(customer_id, 'CUST-(\d+)', 1) as integer) as customer_num,
    company_name,
    segment,
    industry,
    region,
    cast(arr as double)              as arr,
    cast(seats as integer)           as seats,
    cast(contract_start_date as date) as contract_start_date,
    cast(contract_end_date as date)   as contract_end_date,
    cast(tenure_months as integer)   as tenure_months,
    cast(is_churned as boolean)      as is_churned,
    try_cast(churn_date as date)     as churn_date,
    nullif(churn_category, '')       as churn_category,
    nullif(specific_reason, '')      as specific_reason,
    nullif(competitor, '')           as competitor
from {{ ref('br_customers') }}

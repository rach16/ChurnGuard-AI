-- The customer dimension. One row per customer, churned and active alike.
select
    customer_id,
    customer_num,
    company_name,
    segment,
    industry,
    region,
    arr,
    seats,
    contract_start_date,
    contract_end_date,
    tenure_months,
    round(tenure_months / 12.0, 1) as tenure_years,
    is_churned,
    churn_date,
    churn_category,
    specific_reason,
    competitor
from {{ ref('sl_customers') }}

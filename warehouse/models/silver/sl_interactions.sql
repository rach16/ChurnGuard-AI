select
    customer_id,
    cast(interaction_date as date) as interaction_date,
    interaction_type,
    content
from {{ ref('br_customer_interactions') }}

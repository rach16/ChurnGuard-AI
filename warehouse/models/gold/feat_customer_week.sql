-- Point-in-time features: one row per customer per observed week.
--
-- Every value here is computed using ONLY observations up to and including
-- week_start. That constraint is the whole point of this model, and it is what
-- separates a trainable feature table from a dashboard query.
--
-- src/core/health_scoring.py computes the same signals from each customer's
-- LATEST snapshot, which is correct for a dashboard and fatal for training: for a
-- churned customer, the latest snapshot is the week before they left, so a model
-- trained on it learns "customers about to churn look terrible", scores near 1.0,
-- and predicts nothing. See docs/ARCHITECTURE.md and ADR-0008.
--
-- Leakage is enforced by construction (every window is `rows between N preceding
-- and current row`) and verified by tests/test_point_in_time_features.py, which
-- rebuilds the table on truncated data and asserts the rows are identical.

with weeks as (
    select
        customer_id,
        week_start,
        engagement_score,
        feature_adoption_rate,
        active_users,
        sessions,
        -- Ascending index. sl_engagement_weekly numbers weeks_ago descending from
        -- each customer's last observation, which is the wrong direction here.
        row_number() over (partition by customer_id order by week_start) as week_index
    from {{ ref('sl_engagement_weekly') }}
    -- Truncation hook used only by tests/test_point_in_time_features.py: rebuild
    -- the table as if today were an earlier date, then assert the surviving rows
    -- are byte-identical. If any feature peeked forward, they would not be.
    {% if var('feature_cutoff_date', none) %}
    where week_start <= '{{ var("feature_cutoff_date") }}'
    {% endif %}
),

engagement as (
    select
        customer_id,
        week_start,
        week_index,
        engagement_score,
        feature_adoption_rate,
        active_users,
        sessions,

        avg(engagement_score) over w4  as engagement_mean_4w,
        avg(engagement_score) over w12 as engagement_mean_12w,
        avg(feature_adoption_rate) over w12 as adoption_mean_12w,

        -- regr_slope returns NaN before it has two distinct x values.
        coalesce(nullif(regr_slope(engagement_score, week_index) over w4,  'NaN'), 0.0) as engagement_slope_4w,
        coalesce(nullif(regr_slope(engagement_score, week_index) over w12, 'NaN'), 0.0) as engagement_slope_12w,

        min(engagement_score) over w12 as engagement_min_12w,
        max(engagement_score) over w12 as engagement_max_12w,

        -- Current engagement against this customer's own early baseline. A
        -- healthy-looking absolute score can still be a collapse in relative terms.
        --
        -- Fixed 8-week opening window rather than an expanding mean-to-date. The
        -- expanding version accumulated over up to 100 rows, and DuckDB sums in a
        -- different order depending on partition size, so values on a rounding
        -- boundary flipped by 1e-4 between builds. A feature that changes with how
        -- much unrelated data is present is not reproducible. The fixed window is
        -- also the more meaningful comparison: engagement now versus how this
        -- customer started, not versus a drifting average of itself.
        avg(engagement_score) over (
            partition by customer_id order by week_index
            rows between unbounded preceding and 12 preceding
        ) as engagement_baseline

    from weeks
    window
        w4  as (partition by customer_id order by week_index rows between 3  preceding and current row),
        w12 as (partition by customer_id order by week_index rows between 11 preceding and current row)
),

-- Ticket counts in trailing windows. A range join rather than a window function,
-- because tickets are event-shaped and do not align to the weekly grain.
tickets as (
    select
        w.customer_id,
        w.week_start,
        count(t.ticket_id) filter (
            where t.created_date > w.week_start - interval 30 day
              and t.created_date <= w.week_start
        ) as tickets_30d,
        count(t.ticket_id) filter (
            where t.created_date > w.week_start - interval 90 day
              and t.created_date <= w.week_start
        ) as tickets_90d,
        avg(t.csat_score) filter (
            where t.created_date > w.week_start - interval 90 day
              and t.created_date <= w.week_start
        ) as csat_mean_90d,
        count(t.ticket_id) filter (
            where t.created_date <= w.week_start
              and t.severity in ('High', 'Critical')
        ) as severe_tickets_to_date
    from weeks w
    left join {{ ref('sl_support_tickets') }} t
           on t.customer_id = w.customer_id
          and t.created_date <= w.week_start
    group by w.customer_id, w.week_start
),

contact as (
    select
        w.customer_id,
        w.week_start,
        max(i.interaction_date) as last_interaction_date
    from weeks w
    left join {{ ref('sl_interactions') }} i
           on i.customer_id = w.customer_id
          and i.interaction_date <= w.week_start
    group by w.customer_id, w.week_start
)

select
    e.customer_id,
    e.week_start,
    e.week_index,

    -- Static attributes. Known at contract start, so safe at any observation week.
    c.segment,
    c.industry,
    c.arr,
    c.seats,

    -- Tenure as of this week, not total tenure -- that would encode the future.
    date_diff('week', c.contract_start_date, e.week_start) as tenure_weeks,

    -- Engagement, as of this week
    e.engagement_score,
    e.feature_adoption_rate,
    e.active_users,
    e.sessions,
    round(e.engagement_mean_4w, 4)   as engagement_mean_4w,
    round(e.engagement_mean_12w, 4)  as engagement_mean_12w,
    round(e.adoption_mean_12w, 4)    as adoption_mean_12w,
    round(e.engagement_slope_4w, 6)  as engagement_slope_4w,
    round(e.engagement_slope_12w, 6) as engagement_slope_12w,
    round(e.engagement_max_12w - e.engagement_min_12w, 4) as engagement_range_12w,
    round(e.engagement_score - coalesce(e.engagement_baseline, e.engagement_score), 3)
        as engagement_vs_own_baseline,

    -- Support
    t.tickets_30d,
    t.tickets_90d,
    round(t.csat_mean_90d, 3) as csat_mean_90d,
    t.severe_tickets_to_date,

    -- Relationship
    coalesce(date_diff('day', ct.last_interaction_date, e.week_start), 9999)
        as days_since_last_interaction

from engagement e
join {{ ref('sl_customers') }} c using (customer_id)
join tickets t  on t.customer_id  = e.customer_id and t.week_start  = e.week_start
join contact ct on ct.customer_id = e.customer_id and ct.week_start = e.week_start

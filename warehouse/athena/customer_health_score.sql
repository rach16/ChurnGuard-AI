CREATE EXTERNAL TABLE IF NOT EXISTS `churnguard`.`customer_health_score` (
  `customer_id` string,
  `customer_num` int,
  `company_name` string,
  `segment` string,
  `arr` double,
  `tenure_years` double,
  `is_churned` boolean,
  `risk_score` double,
  `risk_level` string,
  `risk_reason` string,
  `trend` string,
  `engagement_score` double,
  `feature_adoption_rate` double,
  `support_tickets_30d` bigint,
  `mean_csat` double,
  `last_engagement_days` bigint,
  `f_engagement` double,
  `f_adoption` double,
  `f_trend` double,
  `f_support` double,
  `f_satisfaction` double
)
STORED AS PARQUET
LOCATION 's3://churnguard-warehouse-586723123589/gold/customer_health_score/'
TBLPROPERTIES ('parquet.compression'='ZSTD');

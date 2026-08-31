CREATE EXTERNAL TABLE IF NOT EXISTS `churnguard`.`fct_engagement_weekly` (
  `customer_id` string,
  `week_start` date,
  `weeks_ago` bigint,
  `engagement_score` double,
  `feature_adoption_rate` double,
  `active_users` int,
  `sessions` int,
  `segment` string,
  `arr` double
)
STORED AS PARQUET
LOCATION 's3://churnguard-warehouse-586723123589/gold/fct_engagement_weekly/'
TBLPROPERTIES ('parquet.compression'='ZSTD');

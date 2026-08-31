CREATE EXTERNAL TABLE IF NOT EXISTS `churnguard`.`fct_support_tickets` (
  `ticket_id` string,
  `customer_id` string,
  `created_date` date,
  `category` string,
  `issue_type` string,
  `severity` string,
  `resolution_hours` int,
  `csat_score` int,
  `segment` string,
  `arr` double
)
STORED AS PARQUET
LOCATION 's3://churnguard-warehouse-586723123589/gold/fct_support_tickets/'
TBLPROPERTIES ('parquet.compression'='ZSTD');

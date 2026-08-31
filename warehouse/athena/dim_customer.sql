CREATE EXTERNAL TABLE IF NOT EXISTS `churnguard`.`dim_customer` (
  `customer_id` string,
  `customer_num` int,
  `company_name` string,
  `segment` string,
  `industry` string,
  `region` string,
  `arr` double,
  `seats` int,
  `contract_start_date` date,
  `contract_end_date` date,
  `tenure_months` int,
  `tenure_years` double,
  `is_churned` boolean,
  `churn_date` date,
  `churn_category` string,
  `specific_reason` string,
  `competitor` string
)
STORED AS PARQUET
LOCATION 's3://churnguard-warehouse-586723123589/gold/dim_customer/'
TBLPROPERTIES ('parquet.compression'='ZSTD');

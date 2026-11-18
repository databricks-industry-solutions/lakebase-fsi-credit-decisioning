-- Databricks notebook source
-- Triggered batch ingestion. The Silver output feeds training and Lakebase enrichment.
-- With expectations, these materialized views use full refresh semantics.
CREATE OR REFRESH MATERIALIZED VIEW credit_ingest_bronze
COMMENT 'Synthetic credit JSON from a governed UC Volume; includes file provenance'
AS SELECT *, _metadata.file_path AS source_file
FROM read_files(
  '${credit.raw_path}',
  format => 'json',
  mode => 'FAILFAST',
  schema => 'customer_id BIGINT, age INT, income_monthly DOUBLE, credit_score INT, open_accounts INT, total_debt DOUBLE, payment_history FLOAT, utilization FLOAT, months_employed INT, derogatory_marks INT, last_updated STRING'
);

-- COMMAND ----------
CREATE OR REFRESH MATERIALIZED VIEW credit_ingest_silver (
  CONSTRAINT valid_customer EXPECT (customer_id IS NOT NULL AND customer_id >= 0) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_age EXPECT (age IS NOT NULL AND age BETWEEN 18 AND 100) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_income EXPECT (income_monthly IS NOT NULL AND income_monthly > 0) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_score EXPECT (credit_score IS NOT NULL AND credit_score BETWEEN 300 AND 850) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_accounts EXPECT (open_accounts IS NOT NULL AND open_accounts >= 0) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_debt EXPECT (total_debt IS NOT NULL AND total_debt >= 0) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_history EXPECT (payment_history IS NOT NULL AND payment_history BETWEEN 0 AND 1) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_utilization EXPECT (utilization IS NOT NULL AND utilization BETWEEN 0 AND 1) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_employment EXPECT (months_employed IS NOT NULL AND months_employed >= 0) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_derogatory EXPECT (derogatory_marks IS NOT NULL AND derogatory_marks >= 0) ON VIOLATION FAIL UPDATE,
  CONSTRAINT valid_date EXPECT (try_cast(last_updated AS DATE) IS NOT NULL) ON VIOLATION FAIL UPDATE
)
COMMENT 'Validated credit features consumed by model training and operational enrichment'
AS SELECT customer_id, age, income_monthly, credit_score, open_accounts,
          total_debt, payment_history, utilization, months_employed,
          derogatory_marks, last_updated, source_file
FROM credit_ingest_bronze;

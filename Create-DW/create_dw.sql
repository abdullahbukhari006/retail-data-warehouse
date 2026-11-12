CREATE DATABASE IF NOT EXISTS retail_data_warehouse
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_bin;

USE retail_data_warehouse;

-- Retail warehouse and required ETL working tables.
-- Target: MySQL 8.0.16+ (enforced CHECK constraints), InnoDB, utf8mb4.
-- Select/create the project database before running this file, for example
-- mysql -h HOST -P PORT -u USER -p DATABASE < Create-DW/create_dw.sql
-- This script deletes existing project data in the selected database.
-- The bootstrap above selects retail_data_warehouse. Adjust it for another name.
-- Database credentials are supplied by the Python application at execution time.
-- DDL commits implicitly in MySQL; rerun the complete script after a failure.

SET NAMES utf8mb4;

-- Remove dependants before referenced tables, keeping FK enforcement enabled.
DROP VIEW IF EXISTS STORE_QUARTERLY_SALES;
DROP TABLE IF EXISTS online_sales_cache;
DROP TABLE IF EXISTS periodic_join_results;
DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS shed_table;
DROP TABLE IF EXISTS periodic_sales_data;
DROP TABLE IF EXISTS customer_master_data;
DROP TABLE IF EXISTS product_master_data;
DROP TABLE IF EXISTS dim_customer;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_supplier;
DROP TABLE IF EXISTS dim_store;
DROP TABLE IF EXISTS dim_time;

-- Masters remain separate from dimensions so MESHJOIN reads disk pages.
-- Page scans use the ordered primary keys and LIMIT 500, with independent
-- cursors for the two tables. Never preload either complete master in memory.
CREATE TABLE customer_master_data (
    customer_id VARCHAR(32) NOT NULL,
    gender ENUM('Male', 'Female') NOT NULL,
    age_group ENUM('0–17', '18–25', '26–35', '36–45', '46–50', '51–55', '55+') NOT NULL,
    occupation ENUM('Student', 'Engineer', 'Teacher', 'Executive', 'Clerk',
                    'Healthcare', 'Retail', 'Homemaker', 'Artist', 'Other') NOT NULL,
    city_category ENUM('A', 'B', 'C') NOT NULL,
    stay_in_current_city_years ENUM('0', '1', '2', '3', '4+') NOT NULL,
    marital_status ENUM('Single', 'Married') NOT NULL,
    PRIMARY KEY (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE product_master_data (
    product_id VARCHAR(32) NOT NULL,
    product_name TEXT NOT NULL,
    product_category ENUM('Home Decor', 'Kitchen', 'Stationery', 'Toys',
                          'Seasonal Gifts', 'Bags', 'Jewelry', 'Garden', 'Party', 'Other') NOT NULL,
    supplier_id CHAR(5) NOT NULL,
    supplier_name VARCHAR(100) NOT NULL,
    PRIMARY KEY (product_id),
    CONSTRAINT chk_master_supplier CHECK (
        supplier_id IN ('SUP01', 'SUP02', 'SUP03', 'SUP04', 'SUP05')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE dim_customer (
    customer_key BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    customer_id VARCHAR(32) NOT NULL,
    gender ENUM('Male', 'Female') NOT NULL,
    age_group ENUM('0–17', '18–25', '26–35', '36–45', '46–50', '51–55', '55+') NOT NULL,
    occupation ENUM('Student', 'Engineer', 'Teacher', 'Executive', 'Clerk',
                    'Healthcare', 'Retail', 'Homemaker', 'Artist', 'Other') NOT NULL,
    city_category ENUM('A', 'B', 'C') NOT NULL,
    stay_in_current_city_years ENUM('0', '1', '2', '3', '4+') NOT NULL,
    marital_status ENUM('Single', 'Married') NOT NULL,
    PRIMARY KEY (customer_key),
    UNIQUE KEY uq_customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE dim_product (
    product_key BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id VARCHAR(32) NOT NULL,
    product_name TEXT NOT NULL,
    product_category ENUM('Home Decor', 'Kitchen', 'Stationery', 'Toys',
                          'Seasonal Gifts', 'Bags', 'Jewelry', 'Garden', 'Party', 'Other') NOT NULL,
    PRIMARY KEY (product_key),
    UNIQUE KEY uq_product_id (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE dim_supplier (
    supplier_key BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    supplier_id CHAR(5) NOT NULL,
    supplier_name VARCHAR(100) NOT NULL,
    PRIMARY KEY (supplier_key),
    UNIQUE KEY uq_supplier_id (supplier_id),
    CONSTRAINT chk_supplier_id CHECK (
        supplier_id IN ('SUP01', 'SUP02', 'SUP03', 'SUP04', 'SUP05')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE dim_store (
    store_key BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    store_id TINYINT UNSIGNED NOT NULL,
    store_name VARCHAR(100) NOT NULL,
    city VARCHAR(50) NOT NULL,
    PRIMARY KEY (store_key),
    UNIQUE KEY uq_store_id (store_id),
    CONSTRAINT chk_store_definition CHECK (
        (store_id = 1 AND store_name = 'Online' AND city = 'Web') OR
        (store_id = 2 AND store_name = 'London Bridge' AND city = 'London') OR
        (store_id = 3 AND store_name = 'Manchester Arndale' AND city = 'Manchester') OR
        (store_id = 4 AND store_name = 'Birmingham Bullring' AND city = 'Birmingham') OR
        (store_id = 5 AND store_name = 'Edinburgh Princes' AND city = 'Edinburgh')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- One row per source event timestamp. Parts are stored explicitly for OLAP.
-- day_of_week follows ISO numbering: Monday=1 through Sunday=7.
CREATE TABLE dim_time (
    time_key BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_time DATETIME NOT NULL,
    full_date DATE NOT NULL,
    hour TINYINT UNSIGNED NOT NULL,
    day_of_month TINYINT UNSIGNED NOT NULL,
    month TINYINT UNSIGNED NOT NULL,
    quarter TINYINT UNSIGNED NOT NULL,
    year SMALLINT UNSIGNED NOT NULL,
    day_of_week TINYINT UNSIGNED NOT NULL,
    day_type ENUM('Weekday', 'Weekend') NOT NULL,
    season ENUM('Spring', 'Summer', 'Fall', 'Winter') NOT NULL,
    half_year TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (time_key),
    UNIQUE KEY uq_event_time (event_time),
    KEY ix_time_date (full_date),
    KEY ix_time_year_month (year, month),
    CONSTRAINT chk_time_parts CHECK (
        full_date = DATE(event_time) AND hour = HOUR(event_time)
        AND day_of_month = DAYOFMONTH(event_time)
        AND month = MONTH(event_time) AND quarter = QUARTER(event_time)
        AND year = YEAR(event_time) AND day_of_week = WEEKDAY(event_time) + 1
        AND half_year = CASE WHEN MONTH(event_time) <= 6 THEN 1 ELSE 2 END
        AND day_type = CASE WHEN WEEKDAY(event_time) < 5 THEN 'Weekday' ELSE 'Weekend' END
        AND season = CASE
            WHEN MONTH(event_time) BETWEEN 3 AND 5 THEN 'Spring'
            WHEN MONTH(event_time) BETWEEN 6 AND 8 THEN 'Summer'
            WHEN MONTH(event_time) BETWEEN 9 AND 11 THEN 'Fall'
            ELSE 'Winter' END
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- source_line_id is assigned from the original workbook row during preparation.
-- Keep it through CSV ingestion/replay via deterministic preparation metadata.
-- Never identify a line by invoice/product alone, or deduplicate equal rows.
-- processing_mode preserves the original ingestion path (Live or Periodic).
CREATE TABLE fact_sales (
    sales_key BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_line_id BIGINT UNSIGNED NOT NULL,
    transaction_id VARCHAR(32) NOT NULL,
    customer_key BIGINT UNSIGNED NOT NULL,
    product_key BIGINT UNSIGNED NOT NULL,
    supplier_key BIGINT UNSIGNED NOT NULL,
    store_key BIGINT UNSIGNED NOT NULL,
    time_key BIGINT UNSIGNED NOT NULL,
    quantity INT NOT NULL,
    revenue DECIMAL(24, 6) NOT NULL,
    sales_mode ENUM('Online', 'Periodic') NOT NULL,
    processing_mode VARCHAR(32) NOT NULL,
    PRIMARY KEY (sales_key),
    UNIQUE KEY uq_fact_source_line (source_line_id),
    KEY ix_fact_invoice_product (transaction_id, product_key),
    KEY ix_fact_customer (customer_key),
    KEY ix_fact_product (product_key),
    KEY ix_fact_supplier (supplier_key),
    KEY ix_fact_store (store_key),
    KEY ix_fact_time (time_key),
    CONSTRAINT fk_fact_customer FOREIGN KEY (customer_key) REFERENCES dim_customer (customer_key),
    CONSTRAINT fk_fact_product FOREIGN KEY (product_key) REFERENCES dim_product (product_key),
    CONSTRAINT fk_fact_supplier FOREIGN KEY (supplier_key) REFERENCES dim_supplier (supplier_key),
    CONSTRAINT fk_fact_store FOREIGN KEY (store_key) REFERENCES dim_store (store_key),
    CONSTRAINT fk_fact_time FOREIGN KEY (time_key) REFERENCES dim_time (time_key),
    CONSTRAINT chk_fact_quantity CHECK (quantity > 0),
    CONSTRAINT chk_fact_revenue CHECK (revenue > 0),
    CONSTRAINT chk_fact_processing_mode CHECK (CHAR_LENGTH(TRIM(processing_mode)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE periodic_sales_data (
    source_line_id BIGINT UNSIGNED NOT NULL,
    transaction_id VARCHAR(32) NOT NULL,
    customer_id VARCHAR(32) NOT NULL,
    product_id VARCHAR(32) NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(18, 6) NOT NULL,
    event_time DATETIME NOT NULL,
    sales_mode ENUM('Online', 'Periodic') NOT NULL DEFAULT 'Periodic',
    processing_mode VARCHAR(32) NOT NULL,
    release_hour TINYINT UNSIGNED NOT NULL,
    released BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (source_line_id),
    KEY ix_periodic_release (released, release_hour, event_time, source_line_id),
    CONSTRAINT chk_periodic_quantity CHECK (quantity > 0),
    CONSTRAINT chk_periodic_price CHECK (unit_price > 0),
    CONSTRAINT chk_periodic_mode CHECK (sales_mode = 'Periodic'),
    CONSTRAINT chk_periodic_hour CHECK (release_hour BETWEEN 0 AND 47),
    CONSTRAINT chk_periodic_released CHECK (released IN (0, 1)),
    CONSTRAINT chk_periodic_processing_mode CHECK (CHAR_LENGTH(TRIM(processing_mode)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Contains only cleaned periodic transactions waiting for join capacity.
-- Persist a shed row before removing its in-memory owner. Delete it only
-- after successful downstream handoff/load under the runtime recovery protocol.
CREATE TABLE shed_table (
    source_line_id BIGINT UNSIGNED NOT NULL,
    transaction_id VARCHAR(32) NOT NULL,
    customer_id VARCHAR(32) NOT NULL,
    product_id VARCHAR(32) NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(18, 6) NOT NULL,
    event_time DATETIME NOT NULL,
    sales_mode ENUM('Online', 'Periodic') NOT NULL DEFAULT 'Periodic',
    processing_mode VARCHAR(32) NOT NULL,
    shed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (source_line_id),
    KEY ix_shed_arrival (shed_at, source_line_id),
    CONSTRAINT chk_shed_quantity CHECK (quantity > 0),
    CONSTRAINT chk_shed_price CHECK (unit_price > 0),
    CONSTRAINT chk_shed_mode CHECK (sales_mode = 'Periodic'),
    CONSTRAINT chk_shed_processing_mode CHECK (CHAR_LENGTH(TRIM(processing_mode)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Select the project database before running this additive migration.
CREATE TABLE IF NOT EXISTS periodic_join_results (
    source_line_id BIGINT UNSIGNED NOT NULL,
    transaction_id VARCHAR(32) NOT NULL,
    customer_key BIGINT UNSIGNED NOT NULL,
    product_key BIGINT UNSIGNED NOT NULL,
    supplier_key BIGINT UNSIGNED NOT NULL,
    store_key BIGINT UNSIGNED NOT NULL,
    time_key BIGINT UNSIGNED NOT NULL,
    quantity INT NOT NULL,
    revenue DECIMAL(24, 6) NOT NULL,
    sales_mode ENUM('Online', 'Periodic') NOT NULL,
    processing_mode VARCHAR(32) NOT NULL,
    release_hour TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (source_line_id),
    KEY ix_join_results_hour (release_hour, source_line_id),
    CONSTRAINT fk_join_results_customer FOREIGN KEY (customer_key) REFERENCES dim_customer (customer_key),
    CONSTRAINT fk_join_results_product FOREIGN KEY (product_key) REFERENCES dim_product (product_key),
    CONSTRAINT fk_join_results_supplier FOREIGN KEY (supplier_key) REFERENCES dim_supplier (supplier_key),
    CONSTRAINT fk_join_results_store FOREIGN KEY (store_key) REFERENCES dim_store (store_key),
    CONSTRAINT fk_join_results_time FOREIGN KEY (time_key) REFERENCES dim_time (time_key),
    CONSTRAINT chk_join_results_quantity CHECK (quantity > 0),
    CONSTRAINT chk_join_results_revenue CHECK (revenue > 0),
    CONSTRAINT chk_join_results_mode CHECK (sales_mode = 'Periodic'),
    CONSTRAINT chk_join_results_hour CHECK (release_hour BETWEEN 0 AND 47),
    CONSTRAINT chk_join_results_processing CHECK (CHAR_LENGTH(TRIM(processing_mode)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Populate dimensions from completed enrichment in the loader. Store_list is
-- the prescribed five-entry in-memory lookup, not an additional database table.
-- Q20 creates STORE_QUARTERLY_SALES in Queries-DW/analysis_queries.sql.
-- The bonus cache is added after the required pipeline and queries pass.

-- Retail warehouse analyses Q1-Q20 | MySQL 8.0+
-- Select the populated database in Workbench before executing this script.
-- Reconciled coverage: 2010-12-01 08:26:00 through 2011-12-09 12:50:00.
-- The selected year is 2011 (2011-01-01 through 2011-12-09, a partial year).
-- All money is GBP and all analyses use stored fact_sales.revenue.
-- Year/month/quarter/day type/season/half are stored dim_time attributes.
-- Rankings return exactly five rows where available, breaking ties by business ID
-- (occupation text for Q5). Q8 gives equal revenue equal DENSE_RANK positions.
-- Growth/volatility = 100 * (current - previous) / previous, rounded to 6 decimals.
-- Zero-sales periods are included. First-period or zero-baseline growth is NULL.
-- Q7 averages shopping-cart amounts within gender/stay groups, not line prices.
-- Q19 includes zero-sales calendar days in the common loaded date range.
-- Q20 is a regular view as explicitly required by section 10 Q20.
-- The brief's section 11 materialized-view wording is interpreted through Q20.
-- Queries do not alter fact/dimension data. Q20 creates/replaces one named view.

-- Q1
-- Monthly top five products for weekdays/weekends, selected year 2011.
WITH monthly_product_sales AS (
    SELECT t.year, t.month, t.day_type, p.product_id, p.product_name,
           SUM(f.revenue) AS revenue
    FROM fact_sales f
    JOIN dim_time t ON t.time_key = f.time_key
    JOIN dim_product p ON p.product_key = f.product_key
    WHERE t.year = 2011
    GROUP BY t.year, t.month, t.day_type, p.product_id, p.product_name
), ranked AS (
    SELECT monthly_product_sales.*,
           ROW_NUMBER() OVER (PARTITION BY year, month, day_type
                              ORDER BY revenue DESC, product_id) AS product_rank
    FROM monthly_product_sales
)
SELECT year, month, day_type, product_rank, product_id, product_name, revenue
FROM ranked WHERE product_rank <= 5
ORDER BY year, month, day_type, product_rank;

-- Q2
-- All loaded dates, 2010-12-01 through 2011-12-09.
SELECT c.gender, c.age_group, c.city_category, SUM(f.revenue) AS revenue
FROM fact_sales f JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.gender, c.age_group, c.city_category
ORDER BY c.gender, CAST(c.age_group AS CHAR), c.city_category;

-- Q3
-- All loaded dates. Category sales by occupation.
SELECT p.product_category, c.occupation, SUM(f.revenue) AS revenue
FROM fact_sales f
JOIN dim_product p ON p.product_key = f.product_key
JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY p.product_category, c.occupation
ORDER BY CAST(p.product_category AS CHAR), CAST(c.occupation AS CHAR);

-- Q4
-- Quarterly demographic trend, selected year 2011 (Q4 ends December 9).
SELECT t.year, t.quarter, c.gender, c.age_group, SUM(f.revenue) AS revenue
FROM fact_sales f
JOIN dim_time t ON t.time_key = f.time_key
JOIN dim_customer c ON c.customer_key = f.customer_key
WHERE t.year = 2011
GROUP BY t.year, t.quarter, c.gender, c.age_group
ORDER BY t.year, t.quarter, c.gender, CAST(c.age_group AS CHAR);

-- Q5
-- All loaded dates. Exactly five occupations per category, where available.
WITH occupation_sales AS (
    SELECT p.product_category, CAST(c.occupation AS CHAR) AS occupation,
           SUM(f.revenue) AS revenue
    FROM fact_sales f
    JOIN dim_product p ON p.product_key = f.product_key
    JOIN dim_customer c ON c.customer_key = f.customer_key
    GROUP BY p.product_category, c.occupation
), ranked AS (
    SELECT occupation_sales.*,
           ROW_NUMBER() OVER (PARTITION BY product_category
                              ORDER BY revenue DESC, occupation) AS occupation_rank
    FROM occupation_sales
)
SELECT product_category, occupation_rank, occupation, revenue
FROM ranked WHERE occupation_rank <= 5
ORDER BY CAST(product_category AS CHAR), occupation_rank;

-- Q6
-- Latest six loaded calendar months: 2011-07-01 through 2011-12-09.
-- July through December includes the partially loaded final month.
SELECT t.year, t.month, c.city_category, c.marital_status, SUM(f.revenue) AS revenue
FROM fact_sales f
JOIN dim_time t ON t.time_key = f.time_key
JOIN dim_customer c ON c.customer_key = f.customer_key
WHERE t.full_date >= '2011-07-01' AND t.full_date < '2011-12-10'
GROUP BY t.year, t.month, c.city_category, c.marital_status
ORDER BY t.year, t.month, c.city_category, c.marital_status;

-- Q7
-- All loaded dates. Purchase grain = one invoice within a gender/stay group.
-- Repeated valid product lines contribute to their invoice amount.
WITH purchases AS (
    SELECT f.transaction_id, c.stay_in_current_city_years, c.gender,
           SUM(f.revenue) AS purchase_amount
    FROM fact_sales f JOIN dim_customer c ON c.customer_key = f.customer_key
    GROUP BY f.transaction_id, c.stay_in_current_city_years, c.gender
)
SELECT stay_in_current_city_years, gender, COUNT(*) AS purchases,
       SUM(purchase_amount) AS revenue,
       ROUND(AVG(CAST(purchase_amount AS DECIMAL(40,12))), 6) AS average_purchase
FROM purchases
GROUP BY stay_in_current_city_years, gender
ORDER BY stay_in_current_city_years, gender;

-- Q8
-- All loaded dates. Show exactly A/B/C in every loaded product category.
WITH categories AS (SELECT DISTINCT product_category FROM dim_product),
cities AS (SELECT 'A' AS city_category UNION ALL SELECT 'B' UNION ALL SELECT 'C'),
city_sales AS (
    SELECT p.product_category, c.city_category, SUM(f.revenue) AS revenue
    FROM fact_sales f
    JOIN dim_product p ON p.product_key = f.product_key
    JOIN dim_customer c ON c.customer_key = f.customer_key
    GROUP BY p.product_category, c.city_category
), complete_sales AS (
    SELECT p.product_category, c.city_category, COALESCE(s.revenue, 0) AS revenue
    FROM categories p CROSS JOIN cities c
    LEFT JOIN city_sales s ON s.product_category = p.product_category
                         AND s.city_category = c.city_category
)
SELECT product_category, city_category, revenue,
       DENSE_RANK() OVER (PARTITION BY product_category ORDER BY revenue DESC) AS city_rank
FROM complete_sales
ORDER BY CAST(product_category AS CHAR), city_rank, city_category;

-- Q9
-- Monthly category growth in 2011. January has no in-year predecessor.
-- December is partial through 2011-12-09, so its change is not a full-month comparison.
WITH months AS (SELECT 1 AS month UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9 UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12),
categories AS (SELECT DISTINCT product_category FROM dim_product),
monthly_sales AS (
    SELECT p.product_category, t.month, SUM(f.revenue) AS revenue
    FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
    JOIN dim_product p ON p.product_key = f.product_key
    WHERE t.year = 2011 GROUP BY p.product_category, t.month
), complete_sales AS (
    SELECT c.product_category, m.month, COALESCE(s.revenue, 0) AS revenue
    FROM categories c CROSS JOIN months m
    LEFT JOIN monthly_sales s ON s.product_category = c.product_category AND s.month = m.month
), changes AS (
    SELECT complete_sales.*, LAG(revenue) OVER (PARTITION BY product_category ORDER BY month) AS previous_revenue
    FROM complete_sales
)
SELECT 2011 AS year, product_category, month, revenue, previous_revenue,
       ROUND(100 * CAST(revenue - previous_revenue AS DECIMAL(40,12)) / NULLIF(previous_revenue, 0), 6) AS growth_pct
FROM changes ORDER BY CAST(product_category AS CHAR), month;

-- Q10
-- Weekday/weekend sales by age, selected year 2011 through December 9.
SELECT 2011 AS year, c.age_group, t.day_type, SUM(f.revenue) AS revenue
FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
JOIN dim_customer c ON c.customer_key = f.customer_key
WHERE t.year = 2011 GROUP BY c.age_group, t.day_type
ORDER BY CAST(c.age_group AS CHAR), t.day_type;

-- Q11
-- Separately executable answer to the overlapping Q11, selected year 2011.
WITH monthly_product_sales AS (
    SELECT t.year, t.month, t.day_type, p.product_id, p.product_name,
           SUM(f.revenue) AS revenue
    FROM fact_sales f
    JOIN dim_time t ON t.time_key = f.time_key
    JOIN dim_product p ON p.product_key = f.product_key
    WHERE t.year = 2011
    GROUP BY t.year, t.month, t.day_type, p.product_id, p.product_name
), ranked AS (
    SELECT monthly_product_sales.*,
           ROW_NUMBER() OVER (PARTITION BY year, month, day_type
                              ORDER BY revenue DESC, product_id) AS product_rank
    FROM monthly_product_sales
)
SELECT year, month, day_type, product_rank, product_id, product_name, revenue
FROM ranked WHERE product_rank <= 5
ORDER BY year, month, day_type, product_rank;

-- Q12
-- Quarterly store growth, selected year 2011. Q4 is partial through December 9.
WITH quarters AS (SELECT 1 AS quarter UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4),
quarterly_sales AS (
    SELECT f.store_key, t.quarter, SUM(f.revenue) AS revenue
    FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
    WHERE t.year = 2011 GROUP BY f.store_key, t.quarter
), complete_sales AS (
    SELECT st.store_id, st.store_name, q.quarter, COALESCE(s.revenue, 0) AS revenue
    FROM dim_store st CROSS JOIN quarters q
    LEFT JOIN quarterly_sales s ON s.store_key = st.store_key AND s.quarter = q.quarter
), changes AS (
    SELECT complete_sales.*, LAG(revenue) OVER (PARTITION BY store_id ORDER BY quarter) AS previous_revenue
    FROM complete_sales
)
SELECT 2011 AS year, store_id, store_name, quarter, revenue, previous_revenue,
       ROUND(100 * CAST(revenue - previous_revenue AS DECIMAL(40,12)) / NULLIF(previous_revenue, 0), 6) AS growth_pct
FROM changes ORDER BY store_id, quarter;

-- Q13
-- All loaded dates. Group by store, supplier, then product name as requested.
-- Distinct product IDs sharing a name contribute to that same named group.
-- HEX groups exact UTF-8 bytes, preserving trailing spaces under PAD SPACE collations.
SELECT st.store_id, st.store_name, s.supplier_id, s.supplier_name,
       MIN(p.product_name) AS product_name, SUM(f.revenue) AS revenue
FROM fact_sales f
JOIN dim_store st ON st.store_key = f.store_key
JOIN dim_supplier s ON s.supplier_key = f.supplier_key
JOIN dim_product p ON p.product_key = f.product_key
GROUP BY st.store_id, st.store_name, s.supplier_id, s.supplier_name, HEX(p.product_name)
ORDER BY st.store_id, s.supplier_id, HEX(MIN(p.product_name));

-- Q14
-- All loaded dates (2010-12-01 through 2011-12-09), combined by stored season.
SELECT p.product_id, p.product_name, t.season, SUM(f.revenue) AS revenue
FROM fact_sales f JOIN dim_product p ON p.product_key = f.product_key
JOIN dim_time t ON t.time_key = f.time_key
GROUP BY p.product_id, p.product_name, t.season
ORDER BY p.product_id, FIELD(t.season, 'Spring', 'Summer', 'Fall', 'Winter');

-- Q15
-- Store/supplier monthly volatility in 2011 = percentage change, not standard deviation.
-- January baseline is NULL. December is partial through 2011-12-09.
WITH months AS (SELECT 1 AS month UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9 UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12),
monthly_sales AS (
    SELECT f.store_key, f.supplier_key, t.month, SUM(f.revenue) AS revenue
    FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
    WHERE t.year = 2011 GROUP BY f.store_key, f.supplier_key, t.month
), complete_sales AS (
    SELECT st.store_id, st.store_name, sp.supplier_id, sp.supplier_name, m.month,
           COALESCE(s.revenue, 0) AS revenue
    FROM dim_store st CROSS JOIN dim_supplier sp CROSS JOIN months m
    LEFT JOIN monthly_sales s ON s.store_key = st.store_key AND s.supplier_key = sp.supplier_key
                            AND s.month = m.month
), changes AS (
    SELECT complete_sales.*, LAG(revenue) OVER (PARTITION BY store_id, supplier_id ORDER BY month) AS previous_revenue
    FROM complete_sales
)
SELECT 2011 AS year, store_id, store_name, supplier_id, supplier_name, month,
       revenue, previous_revenue,
       ROUND(100 * CAST(revenue - previous_revenue AS DECIMAL(40,12)) / NULLIF(previous_revenue, 0), 6) AS volatility_pct
FROM changes ORDER BY store_id, supplier_id, month;

-- Q16
-- All loaded dates. Each unordered pair is counted once per distinct shopping cart.
-- DISTINCT removes repeated product lines inside a cart before pairing.
WITH cart_products AS (
    SELECT DISTINCT f.transaction_id, p.product_id
    FROM fact_sales f JOIN dim_product p ON p.product_key = f.product_key
), pair_counts AS (
    SELECT a.product_id AS product_id_1, b.product_id AS product_id_2,
           COUNT(*) AS shopping_carts
    FROM cart_products a JOIN cart_products b
      ON a.transaction_id = b.transaction_id AND a.product_id < b.product_id
    GROUP BY a.product_id, b.product_id
)
SELECT c.product_id_1, p1.product_name AS product_name_1,
       c.product_id_2, p2.product_name AS product_name_2, c.shopping_carts
FROM pair_counts c
JOIN dim_product p1 ON p1.product_id = c.product_id_1
JOIN dim_product p2 ON p2.product_id = c.product_id_2
ORDER BY c.shopping_carts DESC, c.product_id_1, c.product_id_2 LIMIT 5;

-- Q17
-- Yearly 2011 revenue, with product, supplier, store, and grand-total ROLLUP levels.
-- Roll up business IDs only, then resolve labels, avoiding redundant name subtotals.
WITH rolled AS (
    SELECT st.store_id, sp.supplier_id, p.product_id,
           GROUPING(st.store_id, sp.supplier_id, p.product_id) AS grouping_mask,
           SUM(f.revenue) AS revenue
    FROM fact_sales f
    JOIN dim_time t ON t.time_key = f.time_key
    JOIN dim_store st ON st.store_key = f.store_key
    JOIN dim_supplier sp ON sp.supplier_key = f.supplier_key
    JOIN dim_product p ON p.product_key = f.product_key
    WHERE t.year = 2011
    GROUP BY st.store_id, sp.supplier_id, p.product_id WITH ROLLUP
)
SELECT 2011 AS year, r.store_id,
       CASE WHEN r.grouping_mask = 7 THEN 'ALL STORES' ELSE st.store_name END AS store_name,
       r.supplier_id,
       CASE WHEN r.grouping_mask IN (7,3) THEN 'ALL SUPPLIERS' ELSE sp.supplier_name END AS supplier_name,
       r.product_id,
       CASE WHEN r.grouping_mask IN (7,3,1) THEN 'ALL PRODUCTS' ELSE p.product_name END AS product_name,
       CASE r.grouping_mask WHEN 7 THEN 'Grand total' WHEN 3 THEN 'Store subtotal'
                            WHEN 1 THEN 'Supplier subtotal' ELSE 'Product' END AS aggregation_level,
       r.revenue
FROM rolled r
LEFT JOIN dim_store st ON st.store_id = r.store_id
LEFT JOIN dim_supplier sp ON sp.supplier_id = r.supplier_id
LEFT JOIN dim_product p ON p.product_id = r.product_id
ORDER BY r.store_id IS NULL, r.store_id, r.supplier_id IS NULL, r.supplier_id,
         r.product_id IS NULL, r.product_id;

-- Q18
-- Selected year 2011. Half 1 January-June, half 2 July-December 9.
SELECT 2011 AS year, p.product_id, p.product_name,
       SUM(CASE WHEN t.half_year = 1 THEN f.quantity ELSE 0 END) AS first_half_quantity,
       SUM(CASE WHEN t.half_year = 1 THEN f.revenue ELSE 0 END) AS first_half_revenue,
       SUM(CASE WHEN t.half_year = 2 THEN f.quantity ELSE 0 END) AS second_half_quantity,
       SUM(CASE WHEN t.half_year = 2 THEN f.revenue ELSE 0 END) AS second_half_revenue,
       SUM(f.quantity) AS full_year_quantity, SUM(f.revenue) AS full_year_revenue
FROM fact_sales f JOIN dim_product p ON p.product_key = f.product_key
JOIN dim_time t ON t.time_key = f.time_key
WHERE t.year = 2011 GROUP BY p.product_id, p.product_name
ORDER BY p.product_id;

-- Q19
-- Common window: 2010-12-01 through 2011-12-09 inclusive (374 calendar days).
-- The average includes zero-sales dates for every product, across this common window.
-- Test the unrounded threshold by cross-multiplication to avoid rounding edge cases.
WITH bounds AS (
    SELECT MIN(t.full_date) AS first_date, MAX(t.full_date) AS last_date,
           DATEDIFF(MAX(t.full_date), MIN(t.full_date)) + 1 AS calendar_days
    FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
), daily_sales AS (
    SELECT f.product_key, t.full_date, SUM(f.revenue) AS daily_revenue
    FROM fact_sales f JOIN dim_time t ON t.time_key = f.time_key
    GROUP BY f.product_key, t.full_date
), product_totals AS (
    SELECT product_key, SUM(daily_revenue) AS revenue FROM daily_sales GROUP BY product_key
)
SELECT p.product_id, p.product_name, d.full_date, d.daily_revenue,
       ROUND(CAST(pt.revenue AS DECIMAL(40,12)) / b.calendar_days, 6) AS average_daily_revenue,
       ROUND(CAST(d.daily_revenue * b.calendar_days AS DECIMAL(40,12)) / pt.revenue, 6) AS spike_factor,
       b.calendar_days
FROM daily_sales d JOIN product_totals pt ON pt.product_key = d.product_key
JOIN dim_product p ON p.product_key = d.product_key CROSS JOIN bounds b
WHERE d.daily_revenue * b.calendar_days > 2 * pt.revenue
ORDER BY p.product_id, d.full_date;

-- Q20
-- Regular view across both loaded years (2010-12-01 through 2011-12-09).
-- Q20 explicitly requires a VIEW. It recomputes totals when read.
-- Ordering belongs on the reading SELECT, outside the view definition.
CREATE OR REPLACE VIEW STORE_QUARTERLY_SALES AS
SELECT st.store_id, st.store_name, t.year, t.quarter, SUM(f.revenue) AS total_sales
FROM fact_sales f JOIN dim_store st ON st.store_key = f.store_key
JOIN dim_time t ON t.time_key = f.time_key
GROUP BY st.store_id, st.store_name, t.year, t.quarter;

SELECT store_id, store_name, year, quarter, total_sales
FROM STORE_QUARTERLY_SALES ORDER BY store_name, year, quarter;

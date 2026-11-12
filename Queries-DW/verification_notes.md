# OLAP definitions and verification

The executable deliverable is `analysis_queries.sql`, with separately numbered answers Q1–Q20. It targets the reconciled warehouse on MySQL 8.0.45. Choose the populated schema in Workbench, set the result-row limit to **Don't Limit**, and execute the complete script. Q20 creates/replaces `STORE_QUARTERLY_SALES`; the other questions are SELECT statements. Repeating the script preserves fact/dimension data.

## Loaded periods

- Complete source coverage is 2010-12-01 08:26:00 through 2011-12-09 12:50:00.
- The selected analysis year for Q1, Q4, Q9–Q12, Q15, Q17, and Q18 is 2011, ending on December 9. Q17's ROLLUP grand total is therefore the selected-year revenue, rather than the two-year warehouse total.
- Q6 covers the latest six calendar months present in the data, 2011-07-01 through 2011-12-09. Its final month is partial.
- Q2, Q3, Q5, Q7, Q8, Q13, Q14, Q16, Q19, and Q20 use both loaded years. Q14 combines equivalent seasons across the full coverage.
- December 2011 and Q4 2011 are partial. Percentage changes against full preceding periods require that qualification when interpreting results.

## Calculation choices

Q1 and Q11 independently answer the overlapping prompts. Each ranks within month and weekday/weekend using ROW_NUMBER, revenue descending and product business ID ascending. Exactly five products are shown where available. Q5 uses the same exactly-five rule per category, with occupation text breaking revenue ties. Q8 shows A, B, and C for every category and uses DENSE_RANK so equal revenues have equal ranks.

Q7 first sums each shopping cart within its gender/stay group, then averages those purchase amounts. Valid repeated product lines remain part of the cart amount. This defines the requested purchase grain explicitly.

Q9, Q12, and Q15 fill the selected year's month/quarter grid with zero-sales periods. January/Q1 has no in-year predecessor. Percentage change is NULL for the first period or a zero previous amount, including zero-to-positive changes. Positive-to-zero changes are -100%. Q15 uses this percentage change as the brief's definition of volatility. Ratios and averages are rounded to six decimal places after using expanded DECIMAL scale for arithmetic. MySQL's division scale rule is documented in its [arithmetic operators reference](https://dev.mysql.com/doc/refman/8.0/en/arithmetic-functions.html).

Q13 groups by store, supplier, and product name. Distinct product IDs sharing a name contribute to that named sales group. Q16 deduplicates products within each cart before counting unordered product pairs, so repeated lines do not multiply pair counts. Equal pair counts are ordered by both product IDs for reproducible top-five selection.

Q17 uses actual WITH ROLLUP and GROUPING, rolling up store/supplier/product business IDs before resolving labels. This produces product rows, supplier subtotals, store subtotals, and one selected-year grand total without redundant label subtotal levels. The masks are 0, 1, 3, and 7 respectively, following MySQL's [GROUP BY modifiers reference](https://dev.mysql.com/doc/refman/8.0/en/group-by-modifiers.html).

Q18 reports first-half, second-half, and full-year quantity/revenue side by side for each product. The two halves must sum to the full-year values.

Q19 uses the entire common loaded calendar window, including dates with zero product sales. This is 374 days for this dataset. The average is total product revenue / 374, including pre-first-sale and post-last-sale dates within that common window. The unrounded predicate is daily_revenue * calendar_days > 2 * product_total. Exact boundary cases are excluded. Displayed averages and spike factors are rounded after testing. This convention can flag occasional/one-day products as strongly as high-volume products; report interpretation should distinguish concentration from absolute sales size.

Q20 follows the explicit section 10 instruction to create a regular SQL view. The section 11 overview's materialized-view wording is resolved in favor of the precise Q20 definition. The view covers both loaded years and stores no second business fact table. Its reading SELECT orders by store name, year, and quarter, distinguishing Q4 2010 from Q4 2011.

## Automated verification

Run `python .\Mesh-Join\main.py check-queries` with the same database credentials used for reconciliation. Finish running the warehouse and keep source files unchanged before starting. Use `reconcile` first to verify individual warehouse lines.

The checker reconstructs source expectations on a temporary disk database, derives independent Python aggregates/rankings/percentages/subtotals, and counts distinct cart product pairs from the source. It then executes the delivered MySQL script and compares every result row, including nulls, quantities, labels, ranks, six-decimal calculations, and missing/extra results. It checks Q16 and Q20 ordering explicitly. Q1–Q19 execute in a read-only repeatable-read snapshot. The snapshot is closed before Q20 creates/replaces the required view, since DDL commits implicitly. No fact or dimension data is changed. The command prints row counts, bounded mismatch examples, and five measured Q19 spike examples, then returns status 0 only if all twenty questions pass.

The full reference and product-pair calculations can take time. Temporary reference files are created on the data drive and removed on completion/failure. MySQL credentials remain private and are never written to source or verification notes. The command needs normal SELECT access plus permission to create/replace the Q20 view.

Local regression fixtures exercise every delivered query using SQLite CTE/window execution with test-only adapters for MySQL FIELD, DATEDIFF, CREATE OR REPLACE VIEW, and ROLLUP/GROUPING. The adapter expands the rollup into explicit grouping levels for local execution. Native MySQL execution is verified separately by the user's authenticated command. A hand-calculated case checks exactly-five ties, invoice-vs-line averaging, missing months, zero baselines, repeated product lines, pair ordering, half-year reconciliation, and the zero-sales-day spike convention.

## Verification status

All 50 regression tests passed. All twenty delivered queries passed the complete 397,884-line disposable warehouse comparison against independently source-derived answers. Q17 includes product rows, supplier/store subtotals, and the selected-2011 grand total. Q19 returned 114,713 qualifying product/date rows under the documented 374-day average convention. Native MySQL verification is pending the user output.

| Query | Complete-source fixture rows | Result |
| --- | ---: | --- |
| Q1 | 120 | PASS |
| Q2 | 42 | PASS |
| Q3 | 100 | PASS |
| Q4 | 56 | PASS |
| Q5 | 50 | PASS |
| Q6 | 36 | PASS |
| Q7 | 10 | PASS |
| Q8 | 30 | PASS |
| Q9 | 120 | PASS |
| Q10 | 14 | PASS |
| Q11 | 120 | PASS |
| Q12 | 20 | PASS |
| Q13 | 15,010 | PASS |
| Q14 | 11,921 | PASS |
| Q15 | 300 | PASS |
| Q16 | 5 | PASS |
| Q17 | 14,674 | PASS |
| Q18 | 3,596 | PASS |
| Q19 | 114,713 | PASS |
| Q20 | 25 | PASS |

## Measured Q19 examples for the report

An independent scan of the reconciled cleaned source identifies these five qualifying dates with the highest daily revenue. The complete Q19 fixture result comparison includes every one of these rows.

| Product | Date | Daily revenue GBP | Common-window daily average GBP | Spike factor |
| --- | --- | ---: | ---: | ---: |
| 23843 — PAPER CRAFT , LITTLE BIRDIE | 2011-12-09 | 168,469.60 | 450.453476 | 374.000000 |
| 23166 — MEDIUM CERAMIC TOP STORAGE JAR | 2011-01-18 | 77,183.60 | 217.691791 | 354.554480 |
| 22502 — PICNIC BASKET WICKER 60 PIECES | 2011-06-10 | 39,916.50 | 126.647594 | 315.177722 |
| 85123A — WHITE HANGING HEART T-LIGHT HOLDER | 2011-01-11 | 8,656.00 | 268.993316 | 32.179238 |
| M — Manual | 2011-10-27 | 8,322.12 | 143.796604 | 57.874246 |

The largest factor is 374 when all of a product's revenue occurs on one day of the common 374-day window. Other factor-374 examples have daily revenue as small as a few pounds. Relative concentration and absolute revenue therefore need separate interpretation. The required cleaning rules retain valid positive source lines and remove cancellations/nonpositive lines, so these figures describe cleaned positive sales rather than net sales after returns. A flagged date is an analytical concentration signal; it does not establish a cause or an error in that sale.

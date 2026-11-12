# Dataset source and inspection

## Acquisition

- Dataset: Online Retail, UCI Machine Learning Repository, dataset 352.
- Source page: https://archive.ics.uci.edu/dataset/352/online+retail
- Original direct workbook URL: https://archive.ics.uci.edu/ml/machine-learning-databases/00352/Online%20Retail.xlsx
- Download date: 12 November 2026, Asia/Karachi.
- Saved file: `Data/Online Retail.xlsx`.
- File size: 23,715,344 bytes.
- SHA-256: `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d`.
- License: Creative Commons Attribution 4.0 International, as stated by UCI.

UCI's archive and direct workbook downloads were unusually slow. The complete workbook was retrieved from the public mirror at https://raw.githubusercontent.com/pplonski/datasets-for-start/master/online-retail/Online-Retail.xlsx. All 2,134,016 bytes received from UCI's direct workbook endpoint matched the mirror's prefix exactly. The full workbook passed ZIP member integrity checks and contains the published row count, expected columns, and date coverage. A complete byte-for-byte comparison against a full UCI download was not performed. The workbook was copied unchanged into `Data/` and its checksum was verified after inspection.

Assignment-required citation: Chen, D., Sain, S. L., and Guo, K. Online Retail. UCI Machine Learning Repository. https://archive.ics.uci.edu/dataset/352/online+retail. Downloaded 12 November 2026.

UCI's current dataset citation: Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5BW33.

## Workbook structure and field mapping

One worksheet, `Online Retail`, contains 541,909 data rows plus one header row. Dates span 1 December 2010 at 08:26 through 9 December 2011 at 12:50. These historical timestamps carry no timezone information in the workbook. Unit prices are in pounds sterling according to UCI.

| Source column | Project field | Observed Python cell types | Missing or blank rows |
| --- | --- | --- | --- |
| InvoiceNo | transaction_id | 532,618 integers, 9,291 strings | 0 |
| StockCode | product_id | 487,036 integers, 54,873 strings | 0 |
| Description | product_name | 540,454 strings, 1 integer, 1,454 empty cells | 1,454 |
| Quantity | quantity | 541,909 integers | 0 |
| InvoiceDate | event_time | 541,909 datetimes | 0 |
| UnitPrice | unit_price | 537,406 floating-point values, 4,503 integers | 0 |
| CustomerID | customer_id | 406,829 integers, 135,080 empty cells | 135,080 |
| Country | country | 541,909 strings | 0 |

Missing counts include null cells and whitespace-only strings. The inspection found 25,900 distinct invoice IDs, 4,070 product IDs, 4,372 nonmissing customer IDs, and 38 countries in the raw data. These are raw counts and must be recalculated after cleaning for master-data preparation.

## Raw data quality observations

| Observation | Rows |
| --- | --- |
| Invoice ID begins with C, case-insensitive | 9,288 |
| Quantity is zero or negative | 10,624 |
| Unit price is zero or negative | 2,517 |
| Missing customer ID | 135,080 |
| Missing description | 1,454 |
| Exact repeat of a previously encountered eight-column row | 5,268 |

These issue counts overlap and cannot be summed to calculate the number removed. Exact repeated rows remain separate source lines under the project grain. Description quality is handled during product-master generation. UCI's metadata page labels missing values as absent, while direct workbook inspection finds the missing cells recorded above.

## Conventions for the preparation stage

- Treat invoice, customer, and product IDs as identifiers. Convert integer cells directly to decimal text without a `.0` suffix, preserve text IDs including letters, and detect missing customer IDs before conversion. Use the resulting stable text consistently as UTF-8 input to the prescribed MD5 rules.
- Read timestamps as datetimes. Preserve event time, and derive the warehouse calendar attributes from it.
- Convert prices from their numeric cell values using `Decimal(str(value))` for decimal arithmetic during preparation and loading.
- Assign a stable internal identity from the original workbook row before filtering. Preserve repeated lines individually throughout processing.
- Convert nonmissing product descriptions to text before comparing their lengths. Retain the longest cleaned description per product and use `UNKNOWN` when none is available.

## Required generation rules and pending measurements

Preparation will retain rows with positive quantity, positive unit price, a present customer ID, and a non-cancelled invoice. Invoice MD5 remainder modulo 10 assigns 0–6 to Online and 7–9 to Periodic. Online invoices use store 1. Periodic invoices use store `2 + hash % 4`. Customer demographics, product categories, and suppliers will follow the exact deterministic rules in sections 5.4.2–5.4.3 of the assignment. Periodic rows will be sorted by event time and divided into 48 release groups.

## Programmatic cleaning results

Step 5 reads the original workbook through `python Mesh-Join/main.py clean` and writes `Data/cleaned_sales.csv` with the original Excel row number as `source_line_id`. The four required predicates are applied together. Rows with missing descriptions and exact repeated valid lines are retained. Original order is preserved for later first-country selection.

| Cleaning measure | Rows |
| --- | --- |
| Before cleaning | 541,909 |
| After cleaning | 397,884 |
| Removed | 144,025 |
| Cancellation condition | 9,288 |
| Nonpositive quantity condition | 10,624 |
| Nonpositive unit price condition | 2,517 |
| Missing customer condition | 135,080 |

The four condition counts overlap. The total removed is the union of those conditions. The before and after counts reconcile as 397,884 + 144,025 = 541,909. Cleaning does not load database records. Channel counts, cleaned master counts, partition counts, and periodic group-size range remain pending their preparation stages.

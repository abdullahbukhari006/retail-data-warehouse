# Final requirements review

**Student:** Abdullah Bukhari · **ID:** 24I-2601 · **Section:** DS-5A
**Instructor:** Dr. Asif Naeem · DS3003 and DS3004 · Fall 2026
**Review date:** 13 November 2026
**Implementation and packaging status:** Ready for submission

## Required deliverables

The release archive is `AbdullahBukhari_24I-2601_Project.zip`. It contains 45 files under exactly the six required folders. The payload manifest lists the size and SHA-256 of each other payload file; a separate ZIP SHA-256 sidecar is provided outside the archive.

| Component | Final outcome | Evidence |
| --- | --- | --- |
| Create-DW | PASS | Full schema defines twelve tables, five fact FKs, line identity, original dimension IDs, stored time parts and revenue, positive measures, and dependency-safe recreation. Both additive migrations match the full schema exactly. |
| Preparation | PASS | Original workbook included; programmatic cleaning, exact MD5 channels/stores/master attributes, source-line preservation, original flags, 48 sorted balanced groups, and all required exports verified. |
| MESHJOIN | PASS | Disk masters scanned in 500-row pages, 500-row chunks, H multimap, shared transaction references, bounded 1,000-row stream, complete coverage and retirement checks. |
| Threads and controls | PASS | Independent Online/Periodic/MeshJoin workers with owned connections; priority, hourly release intervals, all required mode/speed/status/quit commands, graceful drain and cancellation. |
| Peak overload and recovery | PASS | Periodic-only durable shedding under actual peak saturation and non-peak replay with retained identities, flags, and downstream acknowledgement. |
| Warehouse | PASS | Consolidation after both matches, exact decimal revenue, dimensions before facts, atomic periodic publication, conflict detection, idempotent retry, and every-line reconciliation. |
| Q1–Q20 | PASS | Separately numbered executable answers; stored revenue/time attributes; all twenty native MySQL results and fresh-extraction fixture results matched independent source answers. |
| Report | PASS | Eleven-page PDF and matching DOCX with correct cover identity, actual star diagram, MESHJOIN and three shortcomings, cleaning/channel/partition/group/shed counts, Q19 findings, reflection, citation and download date, and bonus evidence. |
| ReadMe | PASS | Fresh/reset schema setup, pinned dependency installation, original-file preparation, master/staging loading, all commands, rates/scale, both loading modes, queries, checks, expected totals, recovery, provenance, and submission instructions. |
| Bonus | PASS | Online/Live-only completed-result cache, peak hold, non-peak atomic copy/delete in batches up to 500, no Periodic caching, restart/rollback/conflict handling, equivalent complete final facts. |
| Packaging | PASS | Six root folders; required CSVs and reproduction inputs; no developer paths, local document dependencies, environments, Git internals, secrets/configuration files, temporary databases, logs, or bytecode. |

## Fresh-extraction validation

The candidate ZIP was CRC-checked and extracted into a separate temporary folder. Tests used the verified Python 3.13.3 environment with the exact pinned mysql-connector-python 9.7.0 and openpyxl 3.1.5 versions. Source, preparation outputs, and the disposable warehouse were separate from the working project and the user's MySQL database.

1. The extracted entry point passed `--help`.
2. The original workbook was cleaned from scratch: 541,909 before, 397,884 retained, 144,025 removed. The four overlapping exclusion counts matched recorded evidence.
3. `generate-data` and `prepare-periodic` reproduced all six generated CSVs byte-for-byte by SHA-256, including both masters, Online stream, cleaned/assigned support data, and periodic staging. The README's default-path commands were also run in place on the extracted package and matched the same hashes.
4. All **62 extracted regression tests passed**. They needed no credentials and used writable temporary fixture folders.
5. The extracted CLI loaded 4,338 customer and 3,665 product masters and all 119,144 staging rows into a fresh disposable database. It executed the complete three-thread cache-mode batch flow with all 278,740 Online and 119,144 Periodic lines, all 48 groups, and zero existing facts at the start.
6. The fresh cache flow inserted 278,740 Online and 119,144 Periodic facts, cached/transferred all 278,740 Online results, and shed/replayed/acknowledged 1,538 Periodic rows. Final stream, H, queue, shed, prepared Periodic, and Online-cache counts were zero.
7. Complete source reconciliation passed for every fact field, dimension/master attribute, calendar part, release assignment/flag, and channel total. All worker-owned connections closed.
8. All twenty extracted query results matched independent source-derived answers, ending **OLAP verification: PASS (20/20)**.

The disposable database uses the documented SQLite fixture adapters for MySQL syntax and transaction/read behavior; this is a fresh-package portability and integration test. Native MySQL 8.0.45 execution, full source reconciliation, all twenty native OLAP answers, and the peak-to-non-peak cache demonstration were separately confirmed during development and are recorded in `Project-Report/evidence/verification_record.md`. The final extraction test does not claim a second fresh native server installation.

| Channel | Facts | Quantity | Revenue GBP |
| --- | ---: | ---: | ---: |
| Online | 278,740 | 3,526,585 | 6,075,186.073000 |
| Periodic | 119,144 | 1,641,227 | 2,836,221.831000 |
| Total | 397,884 | 5,167,812 | 8,911,407.904000 |

Dimensions matched 4,338 customers, 3,665 products, five suppliers, five stores, and 17,282 time rows. Coverage remained 2010-12-01 08:26:00 through 2011-12-09 12:50:00. Periodic group sizes remained 2,482–2,483.

## Final query result sizes

| Question | Rows | Outcome |
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

## Integrity and portable operation

- All source/dependency/data/report payloads match the tested extraction snapshot. Only final documentation/manifest records were finalized after the integration run, and the release ZIP is rechecked for CRC, exact inventory, every recorded hash, and source-file equality.
- The original workbook checksum is `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d`.
- The MySQL schema has twelve tables. Both additive definitions are identical to the corresponding full-schema definitions, and child working tables are dropped before referenced dimensions.
- Application paths resolve relative to the extracted project, not a laptop-specific directory. Private keys and credential/configuration files are absent. Test-only fixture credentials do not identify a real server. The DOCX embeds its figure and has no external local-file relationship.
- The PDF has eleven pages and matches the previously rendered/visually reviewed report. Final report/data/application bytes are protected by the manifest.
- The completed development checklist is removed from the workspace and excluded from the ZIP. This final review and the permanent report evidence retain the relevant acceptance record.

## Documented interpretations and limitations

Q20 follows its explicit regular VIEW instruction; the section 11 overview's materialized-view wording is addressed in the query notes and report. Q1 and Q11 both have executable answers. Query comments document selected years, partial final periods, ranking ties, cart-level average purchase grain, missing/zero percentage baselines, and Q19's 374-day zero-sales-date convention. Q13 preserves exact stored names including trailing spaces. Generated demographic/category/channel/store attributes follow the brief and are not measured commercial attributes.

The unchanged source workbook was acquired from a disclosed public mirror after slow UCI transfers. A 2,134,016-byte UCI prefix matched, while full equality to a completed UCI download was not established. The required citation, actual 12 November 2026 download date, and this provenance limitation are present in the report, data note, and README.

## Final student action

Upload the completed ZIP to Google Classroom by **13 November 2026 at 23:00** and verify the receipt. Classroom upload/receipt is a separate student action, not an automated package check. No upload, repository push, or submission confirmation is claimed by this review.

# T-082: HistData yearly archives for the range importer

## Finding (pod run, 2026-10-04)
`scripts/import_histdata.py --symbol NSXUSD --from-year 2010 --through-year 2026` imported 21 months (2025-01 to 2026-09) and rejected all 180 months from 2010-01 to 2024-12 with "HistData download form is missing or does not match the request". HistData publishes monthly M1 archives only for the current and previous year. Older complete years are published as one archive per year (for example `DAT_ASCII_NSXUSD_M1_2023.zip`, covering Jan to Dec). The importer only requests monthly archives, so history before 2025 is missing.

## Required change (Codex, Light tier unless it touches the downloader allow-list, which is Full)
1. Verify first, without guessing: fetch the HistData download page for one past year (no month) and one current-year month, record the exact form fields and URLs in `docs/reports/histdata_qa.md`.
2. Extend `histdata_range.py` / `histdata_download.py`: for each year, request the yearly M1 archive when HistData offers no monthly archives for that year; keep monthly for the years that do. Keep the fixed host, no redirects, size caps, member-name check, atomic cache and one request at a time with pauses.
3. Yearly archives are large: raise the size cap only for yearly M1 and record the new cap in the QA report.
4. Manifest: pin first-seen sha256 per yearly archive (cache integrity only, same wording as for months). Mark completed work per month so a yearly archive is imported once and reruns skip it and add zero rows.
5. Conflicting minutes: detect them per month inside the yearly file. For each conflicting month, rebuild from that month's tick archive (monthly tick archives exist for old years; verify) and record both hashes, as for monthly M1. If a month's tick archive cannot be fetched, reject that month, do not guess.
6. Do not import a year twice with different methods: the lake partition for a day must come from exactly one method; record the method per month in the manifest.
7. Tests: yearly path with fake downloads (resume, idempotence, conflict month rebuilt from ticks, failed year reported, partial exit code 1), plus a test that a year with monthly archives still uses monthly.
8. Update `infra/runpod/README.md` section 8 (earliest years: NSXUSD and XAUUSD, verified from the HistData site; no more 180 wasted requests) and keep one command per symbol.

## Acceptance
- Dry-run list shows, per symbol, which years use yearly vs monthly archives.
- Pod run (owner starts the pod; Claude drives the terminal): NSXUSD and XAUUSD from the earliest year through the last completed month, then a rerun that adds zero rows; summaries recorded in validations.
- QA: row counts per year, gap classification, no unexpected gaps beyond the known weekend/session patterns, spot comparison of overlapping months with the 2025 to 2026 monthly import.

## Pod state to reuse
The pod volume already holds 2025-01 to 2026-09 for NSXUSD (and XAUUSD 2025 onward is importing). Do not re-import those months.

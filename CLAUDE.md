# foxtrot-training-pulse

**Owner:** Samuel Kosco — Data Analyst, Foxtrot Aviation Services
**Purpose:** Read-only web replica of the `Training Compliance.pbix` dashboard (Power BI Data Sources folder) — LMS + Safety101 training compliance, plus a "Needs attention" watchlist the pbix doesn't have. Sibling of `pulse-web` (foxtrot-labor-pulse): same design system, same publishing model.

The repo name carries a random suffix and the page carries `noindex` because GitHub Pages is public — treat the URL as the access control and share it only internally.

## How it works

1. `build_training.py` reads the four pbix sources (plus `Safety101 Emp Import.csv` for names/titles) from the synced Data Hub folder — the pbix itself is never read — and renders `index.html` from `template.html` with the data embedded (`training_data.json` alongside).
2. `.github/workflows/refresh.yml` mirrors pulse-web's refresh pattern (dual cron + guard) but runs at **9 PM Eastern** — off-hours, and late enough to catch the same day's 6:26 AM LMS flow dump and ~11:35 AM Safety101 desktop automation output. A manual daytime dispatch doesn't cancel the evening run (only post-9 PM commits count as "already refreshed"). The three Graph secrets (`TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`) are configured (added 2026-08-11 from the ERP `Python Scripts\API Keys` folder — the "Entra Enterprise App GitHub Connection" app; renew the secret there and in this repo when it expires). Manual refresh still works: `python build_training.py` then commit/push, or dispatch the workflow from the Actions tab.
3. `fetch_training_sources.py` is the CI download step (Graph client credentials, same Data Hub drive ID as pulse-web). `TRAIN_DATA_DIR=sources python build_training.py` mimics CI locally.

## Sources (as found in the pbix Power Query)

- `Power BI Data Sources/Location Management.csv` — Manager → LMS Location mapping; rows for DFW AA, MQY, OFFCAK Akron-Canton Office, SPMZ are excluded like the pbix does. `LMS Location` is the location key everywhere.
- `Flow Dumps/dashboard-transcript/learner_transcript.csv` — LMS coursework (Paylocity LMS), statuses Completed / Overdue / Not Started / In Progress.
- `Safety101/S101 Compliance/Safety101 Data.csv` — Safety101 fact table, `Comp?` ∈ {0, 0.5, 1}, refreshed by Sam's desktop automation (`safety101_automation.py`).
- `Safety101/S101 Compliance/Foxtrot Aviation Services Entire Organization Expiring Training.csv` — expiring/never-granted job qualifications; Department is used as Location; Status becomes "Not Signed Off" when Expiration Status contains "Never"/"Expired" (key NC), else the "Expires on …" text (key C).
- `Safety101/S101 Compliance/Safety101 Emp Import.csv` — Employee ID → Full Name / Job Title, used only for the watchlist name join.

## Calculation notes (owner-simplified model, Aug 2026)

Sam simplified the pbix's mixed definitions: **every item is either Compliant or Overdue**, and every percentage on the page is Compliant ÷ (Compliant + Overdue).

- **LMS**: only Completed and Overdue transcript rows count; Not Started / In Progress are ignored entirely ("like walks in a batting average"). So the LMS gauge still equals the pbix `[LMS Comp]` measure, and the chart now uses the same math (the pbix chart column used not-Overdue ÷ all rows — deliberately not replicated anymore).
- **Safety101**: `Comp?` = 1 is Compliant; 0 and 0.5 (partial) are Overdue. Gauge = count(=1) ÷ count — slightly stricter than the pbix `[S101 Comp]`, which credited partials as half.
- **Detail tables** show only overdue items: LMS Overdue courses, and Safety101 qualifications whose Expiration Status contains "Never"/"Expired" (future "Expires on …" rows are compliant and not shown). The pbix status slicers/chips were removed with the simplification.
- **By-location chart**: locations with S101 compliance = 0 are excluded (the pbix visual filter). Benchmark line = 0.85 (`[Compliance Benchmark]`). Intentional fix kept from v1: the pbix plots `Sum(Management[LMS Compliance])`, which doubles percentages for two-manager locations; here each location is plotted once.
- **Needs attention watchlist** (the addition, shown under the KPIs): a person is listed when (LMS Overdue + S101 Overdue) ÷ (LMS counted + S101 required) > 20%. Follows the RM/location filter. The LMS↔Safety101 person join is by normalized name ("Last, First" flipped, letters-only): ~92% of S101 people match; unmatched people still appear with one system's counts.
- The page force-reloads every 30 minutes; filter state persists in sessionStorage across reloads.

## Local development

`python build_training.py` reads the synced Data Hub folder directly. Open `index.html` — no server needed.

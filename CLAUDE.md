# foxtrot-training-pulse

**Owner:** Samuel Kosco — Data Analyst, Foxtrot Aviation Services
**Purpose:** Read-only web replica of the `Training Compliance.pbix` dashboard (Power BI Data Sources folder) — LMS + Safety101 training compliance, plus a "Needs attention" watchlist the pbix doesn't have. Sibling of `pulse-web` (foxtrot-labor-pulse): same design system, same publishing model.

The repo name carries a random suffix and the page carries `noindex` because GitHub Pages is public — treat the URL as the access control and share it only internally.

## How it works

1. `build_training.py` reads the four pbix sources (plus `Safety101 Emp Import.csv` for names/titles) from the synced Data Hub folder — the pbix itself is never read — and renders `index.html` from `template.html` with the data embedded (`training_data.json` alongside).
2. `.github/workflows/refresh.yml` mirrors pulse-web's daily 9 AM Eastern refresh (dual cron + guard). It **no-ops green until the three Graph secrets are added** (`TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET` — Foxtrot Report Automation app, same values as pulse-web). Until then, refresh locally: `python build_training.py` then commit/push `index.html` + `training_data.json`.
3. `fetch_training_sources.py` is the CI download step (Graph client credentials, same Data Hub drive ID as pulse-web). `TRAIN_DATA_DIR=sources python build_training.py` mimics CI locally.

## Sources (as found in the pbix Power Query)

- `Power BI Data Sources/Location Management.csv` — Manager → LMS Location mapping; rows for DFW AA, MQY, OFFCAK Akron-Canton Office, SPMZ are excluded like the pbix does. `LMS Location` is the location key everywhere.
- `Flow Dumps/dashboard-transcript/learner_transcript.csv` — LMS coursework (Paylocity LMS), statuses Completed / Overdue / Not Started / In Progress.
- `Safety101/S101 Compliance/Safety101 Data.csv` — Safety101 fact table, `Comp?` ∈ {0, 0.5, 1}, refreshed by Sam's desktop automation (`safety101_automation.py`).
- `Safety101/S101 Compliance/Foxtrot Aviation Services Entire Organization Expiring Training.csv` — expiring/never-granted job qualifications; Department is used as Location; Status becomes "Not Signed Off" when Expiration Status contains "Never"/"Expired" (key NC), else the "Expires on …" text (key C).
- `Safety101/S101 Compliance/Safety101 Emp Import.csv` — Employee ID → Full Name / Job Title, used only for the watchlist name join.

## Calculation notes (replicated from the pbix data model)

- **LMS gauge** = Completed ÷ (Completed + Overdue) — `[LMS Comp]`. Not Started / In Progress don't count against it.
- **Safety101 gauge** = SUM(`Comp?`) ÷ COUNT — `[S101 Comp]`; partial (0.5) sign-offs count half.
- **By-location chart** uses the pbix calc columns: LMS = share of rows not Overdue; S101 = share of rows with `Comp?` = 1 (stricter than the gauges — this mismatch is faithful to the original). Locations with S101 compliance = 0 are excluded (the pbix visual filter). Benchmark line = 0.85 (`[Compliance Benchmark]`).
- **Intentional fix:** the pbix chart plots `Sum(Management[LMS Compliance])`, which doubles percentages for locations with two managers. Here each location is plotted once.
- **Status chips** replicate the pbix slicers (`LMS Filter` default "Only Show Overdue", `S101 Filter` default "Show Noncompliant & Expiring Soon"). They filter the two detail tables and the watchlist definition only — gauges and the chart ignore them, which differs from the pbix, where the slicers cross-filter the gauges (with the default slicer selections that would pin the LMS gauge at 0%, so it was judged a wiring accident and not replicated).
- **Needs attention watchlist** (the addition): a person is listed when (LMS bad + S101 bad) ÷ (all LMS assigned + all S101 required) > 20%. LMS bad = Overdue (+ Not Started + In Progress when the wide chip is on); S101 bad = anything not fully signed off (partials count as bad here). Follows the RM/location filter. The LMS↔Safety101 person join is by normalized name ("Last, First" flipped, letters-only): ~94% of S101 people match; unmatched people still appear with one system's counts.
- The page force-reloads every 30 minutes; filter state persists in sessionStorage across reloads.

## Local development

`python build_training.py` reads the synced Data Hub folder directly. Open `index.html` — no server needed.

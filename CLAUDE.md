# foxtrot-training-pulse

**Owner:** Samuel Kosco — Data Analyst, Foxtrot Aviation Services
**Purpose:** Read-only web replica of the `Training Compliance.pbix` dashboard (Power BI Data Sources folder) — LMS + Safety101 training compliance, plus a "Needs attention" watchlist the pbix doesn't have. Sibling of `pulse-web` (foxtrot-labor-pulse): same design system, same publishing model.

The repo name carries a random suffix and the page carries `noindex` because the Pages site is public — treat the URL as the access control and share it only internally. Since the org migration the CODE is private (org repo); only the rendered site is public. The platform embeds this site and its JSON feeds the platform homepage stats.

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

## Calculation notes (owner model, Aug 2026)

**Every item is Completed, Overdue, or Incomplete.** Every percentage on the page is Completed ÷ (Completed + Overdue) — Incomplete items count nowhere ("like walks in a batting average").

- **LMS**: Completed / Overdue from the transcript; Not Started + In Progress = Incomplete.
- **Safety101**: `Comp?` = 1 is Completed; 0 and 0.5 (partial) are Overdue — **unless the employee was hired within the last 10 days** (`NEW_HIRE_GRACE_DAYS`, hire dates joined from `Paylocity Reports/Basic Employee Info.csv` by Employee Id with "A" stripped), in which case their gaps are Incomplete. An employee with no hire-date match is treated as not-recent (gaps stay Overdue).
- **Detail tables** show non-completed items with an Overdue/Incomplete status column, overdue sorted first: LMS courses, and Safety101 qualifications whose Expiration Status contains "Never"/"Expired" (future "Expires on …" rows are compliant and not shown). The pbix status slicers/chips were removed with the simplification.
- **Watchlist O/I/C columns** show per-person overdue/incomplete/completed counts per platform; the >20% share = (LMS O + S101 O) ÷ (LMS C+O + S101 C+O).
- **By-location chart**: locations with S101 compliance = 0 are excluded (the pbix visual filter). Benchmark line = 0.85 (`[Compliance Benchmark]`). Intentional fix kept from v1: the pbix plots `Sum(Management[LMS Compliance])`, which doubles percentages for two-manager locations; here each location is plotted once.
- **Needs attention watchlist** (the addition, shown under the KPIs): a person is listed when (LMS Overdue + S101 Overdue) ÷ (LMS counted + S101 required) > 20%. Follows the RM/location filter. The LMS↔Safety101 person join is by normalized name ("Last, First" flipped, letters-only): ~92% of S101 people match; unmatched people still appear with one system's counts.
- The page force-reloads every 30 minutes; filter state persists in sessionStorage across reloads.

## Local development

`python build_training.py` reads the synced Data Hub folder directly. Open `index.html` — no server needed.

## Org migration (2026-08-19)

Canonical repo: **Foxtrot-Aviation-Services/foxtrot-training-pulse-7fd5c381** (private; the Pages
site is public at `foxtrot-aviation-services.github.io/foxtrot-training-pulse-7fd5c381/`). The old
`sam-kosco.github.io/foxtrot-training-pulse-7fd5c381/` URL stays live via a same-named mirror repo
on Sam's personal account, force-synced by this repo's "Mirror to legacy
URL" workflow (deploy key in `MIRROR_DEPLOY_KEY`). The mirror has Actions
DISABLED — never push to it or run anything there. Retire the legacy URL
(delete the mirror repo + mirror.yml) once the Foxtrot Platform rollout
replaces old links.

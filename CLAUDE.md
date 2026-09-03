# foxtrot-training-pulse

**Owner:** Samuel Kosco — Data Analyst, Foxtrot Aviation Services
**Purpose:** Read-only web replica of the `Training Compliance.pbix` dashboard (Power BI Data Sources folder) — LMS + Safety101 training compliance, plus a "Needs attention" watchlist the pbix doesn't have. Sibling of `pulse-web` (foxtrot-labor-pulse): same design system, same publishing model.

The repo name carries a random suffix and the page carries `noindex` because the Pages site is public — treat the URL as the access control and share it only internally. Since the org migration the CODE is private (org repo); only the rendered site is public. The platform embeds this site and its JSON feeds the platform homepage stats.

## How it works

1. `build_training.py` reads the four pbix sources (plus `Safety101 Emp Import.csv` for names/titles) from the synced Data Hub folder — the pbix itself is never read — and renders `index.html` from `template.html` with the data embedded (`training_data.json` alongside).
2. `.github/workflows/refresh.yml` is fired by the Foxtrot Platform's dispatcher at **9 PM Eastern** (manifest `Monitoring/schedules.json`; native crons removed 2026-08-31 — GitHub's scheduler is fully retired org-wide) — off-hours, and late enough to catch the same day's 6:26 AM LMS flow dump and the evening Safety101 automation output (nightly 8 PM ET). The old in-workflow ET guard is vestigial: dispatches always proceed. The three Graph secrets (`TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`) are configured (added 2026-08-11 from the ERP `Python Scripts\API Keys` folder — the "Entra Enterprise App GitHub Connection" app; renew the secret there and in this repo when it expires). Manual refresh still works: `python build_training.py` then commit/push, or dispatch the workflow from the Actions tab.
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
- The page force-reloads every 30 minutes; filter state persists in sessionStorage across reloads (validated against the active scope).
- **Platform-embed scoping (2026-08-26)**: the platform shell appends `#pscope={rms,locs}` (URL-encoded JSON) to the iframe src, computed per acting user from the org chart (`/api/training/scope`). When present: the location dropdown trims to `locs`, the RM dropdown hides unless `rms` is non-empty (only RMs *below* the viewer appear), and the unfiltered default aggregates over "your locations". Matching is case-insensitive on both names and locations (training locations ARE labor-dist names). No hash = public/mirror behavior, completely unfiltered — that's the whole isolation mechanism, keep it hash-only.

## Badges (Training and Credentials)

The page carries a Badges section (branch `training-and-credentials`, owner Clara Lana):
two top tabs — Training | Badges — with Active/Terminated sub-views inside Badges.

- **Source of truth:** `Definitive Lists/Badges.csv` (one row per badge; built by the
  one-time `consolidate_badges.py` migration from the old 37-tab workbook). Manual
  fixes (missing Employee IDs, corrections) are edited directly in that file.
- **Employee join:** by normalized Employee ID — active employees from
  `Definitive Lists/Current Employees.csv` (status + Labor Dist from Home Labor
  Assignment); terminated employees from `Definitive Lists/Terminated Employees.csv`
  (authoritative incl. IDs; a badge with no usable ID may classify by unique name
  match against it). Location is tied to the badge; Labor Dist AND Position are tied
  to the employee (standardized from the employee files at build time; the badge
  tracker's hand-typed position survives only for unmatched records).
  Unmatched records are kept and labeled (never guessed, never dropped).
  A badge with Returned = Yes always renders in the Deactivated view, never active;
  the Returned column is shown only there.
- **Sub-views (two):** Coverage — the entry view: one row per FOLDER (all labor
  distributions plus any badge-only locations like SRQ; employee counts from
  Current Employees.csv, badge counts by where the badge is FILED). Badges file
  by badge location, which was rewritten to the official folder vocabulary
  (Clara's decision sheet + rule: single-candidate airport, else holder's own
  labor dist). Clicking a folder drills in: the badges filed there (a visitor's
  badge shows "Based at" with a plane marker), the folder's own people, unbadged
  employees with a prefilled New-badge action, and "badged elsewhere" markers for
  people whose badges are filed at other folders. A special "Unmatched records"
  row holds badges whose Employee ID matches nothing.
  Deactivated — badge-keyed: any Deactivated/Termed/returned badge plus every badge
  of a terminated employee; its pill shows the deactivation REASON.
  Statuses (Active / Expiring 30d / Expired) recompute at each build.
- **Alerts:** computed at every build and published in `training_data.json` as
  `alertsFeed` (kinds: expiring &le;7 days / expired standing / unreturned; each
  entry carries employee, badge, location, labor dist, contact managers, and a
  deep link). DELIVERY is the platform's daily digest (Sam): one email per
  recipient per day compiling all platform alerts. `badge_alert_emails.py` is a
  manual fallback only, not wired into CI.
- **Rules:** a row without a badge number is not a badge (renders Not Badged);
  legacy pre-process records were bulk-closed 2026-08-27 with reason "Employee
  terminated" and unknown return status (see badge-data/close_out_legacy.py on
  Clara's machine; backups kept). Alerts = terminated employee whose badge is not
  yet closed out.
- **Badge Specifications by Location** (`Definitive Lists/Badge Specifications.csv`,
  owner-editable): one row per location. Badge Required = No blocks the New Badge
  form with a "No Badge Required" notice (no number, no questions). Badge Type is
  the location's single standard - locked (read-only) on the form. Every non-blank
  question column (Escort, CBP Access, AOA Driving Privileges, Extra Question) is
  an OPEN Yes/No question at that location, never pre-answered; blank = the
  question does not exist there. The CBP Access column doubles as the location's
  special question: "Customs" (CVG) renames it Customs and a Yes requires the
  customs seal number (Customs Number column; shown in the breakdown); any other
  custom text (DFW's "Envoy hangar access keys") becomes the question's label;
  "leadership only" anywhere in the value renders ONLY as a note on the question
  — nothing filters by who is leadership. A Badge Type containing " or "
  (JAX "Blue or Blue Escort", CAK HQ "SIDA or AOA") leaves the type editable so
  the user picks; Number Optional = Yes (Access-keys locations SCF JSX, SLN MRO)
  makes badge number and expiration optional on the form. Extra Question holds
  location-exclusive questions (BNA's Ramp Access, GoJet's Green Stripe, the
  "AOA Access" question at JSX/Envoy stations). The form's kind selector reads
  "Standard airport badge" / "Customer Badge". Specs populate only after a location
  is selected. The location drill-down shows the question list in a strip and a
  per-badge answer column for each of that location's questions (blank answers on
  legacy records = never recorded by the old tracker). Badge Type values in
  Badges.csv were bulk-standardized to these names (2026-08-28). Adding a
  location = adding a row; then re-run the type standardization (always
  EXCLUDING rows whose Badge Type is "Customer Badge").
  **Customer Badge** is a second badge kind available at EVERY location,
  including no-badge ones (there the form only allows Customer Badges); it
  carries no airport-access questions, and the same person can hold both the
  location's standard badge and a Customer Badge under different numbers.
- **Deactivation reasons** (required dropdown; recorded in the Badges.csv
  `Deactivation Reason` column, receiver in `Received By`): Employee terminated,
  Location closed, Lost or damaged, Employee transferred, Expired / replaced, Other.
  Physical return + receiver are REQUIRED for Employee terminated / Location closed /
  Employee transferred / Expired-replaced; optional (recordable) for Lost-or-damaged
  and Other; receiver always required when returned is checked; Other requires a
  note. An unreturned terminated-employee badge stays on the alert list even after
  deactivation - only a recorded return clears it.
- **Alerts:** a terminated employee whose badge isn't marked Returned raises an alert
  (shown on the Terminated tab; emitted as `alerts` in `training_data.json` — contact
  resolved from Labor Dist via Location Management managers).
- **Actions** (modeled on the Hiring Hub's interaction patterns): New Badge / Renew /
  Deactivate modal forms. Deactivation requires the physical badge marked returned
  plus who received it. Forms run in PREVIEW MODE — they display the JSON payload
  that will go to the Power Automate save relay once Sam wires it; nothing writes yet.
- **Deep links:** `#badge/<id>` highlights a row; `#renew/<id>` opens that badge's
  renewal form (target for alert emails).

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

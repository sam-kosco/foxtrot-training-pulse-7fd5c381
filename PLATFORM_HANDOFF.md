# Badge System — Platform Handoff (for Sam)

Owner: Clara Lana · Handoff date: 2026-09-03 · Branch: `training-and-credentials` (draft PR #1)

The badge tracker is feature-complete on the page side and running nightly.
Three pieces move to the platform, all agreed 2026-09-02: **the save backend**
(no Power Automate — Hiring Hub-style, direct and application-driven), **alert
delivery via the daily digest**, and two small config items. This document is
the complete spec for those pieces.

## Current state (what already works)

- The page (this repo, branch `training-and-credentials`) renders the Badges
  tab from `Definitive Lists/Badges.csv` + `Badge Specifications.csv` on the
  Data Hub, joined to the employee extracts by Employee ID. Nightly rebuild via
  the platform dispatcher at 9 PM ET (`refresh.yml`).
- All ~62 locations have specification rows driving the New Badge form.
- The three action forms (New Badge / Renewal / Deactivation) validate fully
  and run in **preview mode**: they display the exact JSON payload they will
  submit. Nothing writes yet — that's the backend below.
- Alerts are computed at every build and published in `training_data.json` as
  `alertsFeed` (contract below). Page display is live; email delivery is
  yours (digest).

## 1. Save backend (takes the forms out of preview mode)

> **BUILT 2026-09-03 (Sam):** engine/badges.py + `POST /api/badges/create|
> renew|deactivate` on the platform, writing Badges.csv exactly per the
> mapping below (deactivation `note` goes to the Notes column; every action
> also stamps an audit note "date actor: action"). One deviation from the
> sketch: auth is NOT the session cookie — the embed is cross-origin, so
> the platform mints a signed token into the iframe hash (`badgeauth`/
> `badgeapi`, beside `#pscope`) for real users with **Training access**
> (badges and the training pulse are one permission — Sam, 2026-09-03;
> Training defaults on for platform accounts, so every user can save), and
> `submitAction()` in template.html sends it as a Bearer header. No hash
> (public page, mirror) = preview mode stays. A save also nudges this
> repo's refresh workflow (10-min debounce) so the page catches up fast.
> Item 2 is BUILT the same day: the platform's per-user morning digest
> (foxtrot-platform engine/digest.py) consumes `alertsFeed` and scopes
> each alert to the right people via the org chart — gated on the
> USER_DIGEST app setting until platform onboarding. The drawer rename
> ("Training & Credentials") shipped 2026-09-03; feeds.json registration
> is the one remaining config ask.

Recommended shape — the Hiring Requests pattern (Pattern A):

- `engine/badges.py` — validation + the write
- three routes in `app.py`: `POST /api/badges/create`, `/api/badges/renew`,
  `/api/badges/deactivate`
- `Badges` entry in `engine/permissions.py` MODULES
- **Write target: `Definitive Lists/Badges.csv` on the Data Hub via Graph**
  (your house pattern — the file stays the single source of truth; the page,
  the alerts, and Clara's tooling all keep working unchanged). Optionally fire
  the dispatcher for an immediate page refresh after a write; otherwise the
  9 PM build picks it up.

The page is embedded in the platform shell, so the forms can `fetch` these
endpoints with the platform session cookie — same as the Hiring Hub embeds.
Flip the forms live by replacing `showPreview(payload)` with the POST (one
function in `template.html`; Clara's side can make that change once endpoints
exist).

### Payload contracts (exactly what the forms produce today)

`create`:

```json
{
  "action": "create",
  "employeeId": "12345", "employeeName": "First Last",
  "location": "BNA MRO", "badgeType": "Red SIDA Badge",
  "badgeNumber": "105710", "expiration": "2027-06-30",
  "escortPrivileges": "Yes",            // only if asked at this location
  "cbpAccess": "Yes",                   // only if asked (label may be Customs
                                        //   or a custom question)
  "customsNumber": "13932-...",         // only when Customs = Yes (CVG)
  "aoaDrivingPrivileges": "No",         // only if asked
  "toolAccess": "Yes",                  // only if asked
  "additionalAccess": "Ramp Access: Yes",  // the location's extra question
  "requestedAt": "2026-09-03T18:22:00.000Z"
}
```

Notes: `expiration` may be `""` at access-keys locations (SCF JSX, SLN MRO)
where number/expiration are optional. `badgeType` is "Customer Badge" for
customer badges (no question fields present).

`renew`:

```json
{ "action": "renew", "badgeId": "BDG-0123", "employeeId": "...",
  "employeeName": "...", "badgeNumber": "...", "location": "...",
  "newExpiration": "2028-01-31", "requestedAt": "..." }
```

`deactivate`:

```json
{ "action": "deactivate", "badgeId": "BDG-0123", "employeeId": "...",
  "employeeName": "...", "badgeNumber": "...", "location": "...",
  "reason": "Employee terminated", "returned": "Yes",
  "receivedBy": "Jane Supervisor", "note": "",
  "deactivatedDate": "2026-09-03", "requestedAt": "..." }
```

### Payload → Badges.csv column mapping

| Payload field | CSV column |
|---|---|
| create: new row | `Badge ID` = next `BDG-nnnn`; `Employee ID`, `Employee Name`, `Location`, `Badge Type`, `Badge Number`, `Expiration Date` (ISO) |
| escortPrivileges | `Escort` |
| cbpAccess | `CBP Access` |
| customsNumber | `Customs Number` |
| aoaDrivingPrivileges | `AOA Driving Privileges` |
| toolAccess | `Tool Access` |
| additionalAccess | `Additional Access` (string `"Label: Yes"`) |
| renew: newExpiration | `Expiration Date` (and clear a stale `Status` if set) |
| deactivate | `Status` = `Deactivated`, `Deactivation Reason`, `Returned`, `Received By`, `Deactivated Date` |

Validation rules the server should re-check (the form already enforces them):
required reason on deactivation; physical return + receiver REQUIRED for
Employee terminated / Location closed / Employee transferred / Expired-replaced;
note required for reason Other; customs number required when Customs = Yes;
dates ISO; never delete rows.

## 2. Alert delivery — the daily digest

The build publishes `alertsFeed` in `training_data.json` (same file the
platform already reads for homepage stats). One entry per alert:

```json
{ "kind": "expiring" | "expired" | "unreturned",
  "badgeId": "BDG-0123", "employee": "First Last", "employeeId": "12345",
  "badgeNumber": "3239620", "badgeType": "SIDA",
  "location": "IAH CABIN", "laborDist": "IAH CABIN",
  "expiration": "2026-09-08", "daysLeft": 5,        // expiring/expired only
  "empStatus": "Terminated",                          // unreturned only
  "contacts": ["Manager One", "Manager Two"],         // from Location Management
  "link": "https://.../#renew/BDG-0123" }             // renewal deep link
```

Semantics agreed with Clara: *see it coming at 30* (page status), *get alerted
at 7* (`kind: "expiring"` starts at daysLeft ≤ 7); `expired` is a standing
list (weekly summary cadence suggested); `unreturned` persists until a return
is recorded — deactivation alone does not clear it. `badge_alert_emails.py`
in this repo is a working formatting reference (tables, deep links,
once-per-event state) — kept as a manual fallback only, not in CI.

## 3. Config asks

- **feeds.json**: register `Definitive Lists/Badges.csv` (suggested max_age
  744h) and `Badge Specifications.csv`.
- **Location Management**: new badge locations need rows so alerts have
  contacts (current gaps: none known; future locations per the SOP checklist).
- **Drawer label**: rename the platform drawer entry to
  "Training and Credentials".

## 4. Known data issue (evidence attached)

The Current/Terminated employee extracts drop rows vs Basic Employee Info
(~24 active people missing, ≥2 terminations missed as of 2026-08-27).
Concrete badge-side case: **Brennan Lowrey** (STL GOJET badge on file,
listed by the location as working) appears in *neither* extract.

## Where everything lives

- Page + build: this repo, branch `training-and-credentials` (draft PR #1 —
  merging it launches the tab on the public page, so merge timing is Clara's
  go-live call, independent of the backend work).
- Data: Data Hub → `Definitive Lists/` (Badges, Badge Specifications,
  Current/Terminated Employees).
- Ops SOP (training doc, shared by Clara): golden rules, workflows,
  new-location checklist, alert catalog.
- Clara's data tooling + timestamped backups: her machine,
  `C:\Users\clara\repos\badge-data\` (migration scripts, bulk-change backups).

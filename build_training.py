"""Build the Foxtrot Training Pulse page.

Reproduces the "Training Compliance.pbix" dashboard from its four raw sources
(the pbix itself is never read) and renders index.html from template.html with
the data embedded. Run with no env vars to read the synced Data Hub folder;
set TRAIN_DATA_DIR to a folder holding the four CSVs to mimic CI.

Owner model (Aug 2026): every item is Completed, Overdue, or Incomplete.
  LMS: Completed / Overdue from the transcript; Not Started + In Progress are
       "Incomplete".
  Safety101: Comp? = 1 is Completed; anything less is Overdue — unless the
       employee was hired within the last NEW_HIRE_GRACE_DAYS (hire dates from
       Paylocity Basic Employee Info.csv), in which case it is Incomplete.
  Every percentage = Completed / (Completed + Overdue) — Incomplete items count
  nowhere, like walks in a batting average.
  Benchmark = 0.85 (pbix [Compliance Benchmark]).
"""

import csv
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath

NEW_HIRE_GRACE_DAYS = 10
BADGE_SOON_DAYS = 30      # badge "Expiring" window

HERE = Path(__file__).parent
# Synced Data Hub for local runs — resolves for any user's synced copy;
# override with LOCAL_DATA_HUB if yours lives elsewhere.
DATA_HUB = Path(os.environ.get("LOCAL_DATA_HUB")
                or Path.home() / "Foxtrot Aviation Services" / "Data Hub - Documents")

SOURCES = {
    "mgmt": r"Power BI Data Sources\Location Management.csv",
    "lms": r"Flow Dumps\dashboard-transcript\learner_transcript.csv",
    "s101": r"Safety101\S101 Compliance\Safety101 Data.csv",
    "expiring": r"Safety101\S101 Compliance\Foxtrot Aviation Services Entire Organization Expiring Training.csv",
    "emp": r"Safety101\S101 Compliance\Safety101 Emp Import.csv",
    "empinfo": r"Paylocity Reports\Basic Employee Info.csv",
    "badges": r"Definitive Lists\Badges.csv",
    "roster": r"Definitive Lists\Roster.csv",
    "current": r"Definitive Lists\Current Employees.csv",
    "termedcsv": r"Definitive Lists\Terminated Employees.csv",
    "specs": r"Definitive Lists\Badge Specifications.csv",
    # Early terminations (core repo's roster job): people termed via the
    # Termination Form whom Paylocity still shows Active until the pay
    # cycle closes — excluded from every count so leavers stop dinging
    # compliance (Sam, 2026-08-27). The badge join treats them as
    # terminated too (their badges move to Deactivated and can alert).
    "earlyterm": r"Definitive Lists\Early Terminations.csv",
}

# Locations the pbix Management query filters out of Location Management.csv
EXCLUDE_LOCS = {"DFW AA", "MQY", "OFFCAK Akron-Canton Office", "SPMZ"}

# Home-office locations dropped from the whole dashboard — ops people only.
# Applied at ingest, so HQ folks appear in no table, graph, filter, or count.
DASH_EXCLUDE = {"CAK HQ", "OFFCAK Akron-Canton Office"}


def src_path(key):
    override = os.environ.get("TRAIN_DATA_DIR")
    if override:
        return Path(override) / PureWindowsPath(SOURCES[key]).name
    return DATA_HUB / SOURCES[key]


def read_csv(key, missing_ok=False):
    try:
        with open(src_path(key), newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        if missing_ok:
            return []
        raise


def norm_name(s):
    """lowercase, letters only, collapsed — join key for LMS<->S101 names."""
    return " ".join(re.sub(r"[^a-z ]", " ", s.lower()).split())


def short_date(s):
    """'04/08/2026' -> '04/08/26' (leaves anything unparseable untouched)."""
    try:
        return datetime.strptime(s, "%m/%d/%Y").strftime("%m/%d/%y")
    except ValueError:
        return s


def last_first_to_display(s):
    """'Abreha, Matthew hailu' -> 'Matthew hailu Abreha'."""
    if "," in s:
        last, first = s.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return s.strip()


def main():
    # ---- Management: manager -> locations (LMS Location = the key used everywhere)
    managers = {}
    for r in read_csv("mgmt"):
        loc, mgr, lms_loc = (r["Location"].strip(), r["Manager"].strip(),
                             r["LMS Location"].strip())
        if not mgr or not lms_loc:
            continue
        if loc in EXCLUDE_LOCS or lms_loc in EXCLUDE_LOCS or lms_loc in DASH_EXCLUDE:
            continue
        managers.setdefault(mgr, set()).add(lms_loc)
    mgmt_locs = sorted({l for locs in managers.values() for l in locs})

    # ---- Hire dates: employees hired within the grace window are "Incomplete"
    #      on Safety101 rather than Overdue
    def norm_eid(s):
        return s.strip().replace("A", "").lstrip("0")

    today = datetime.now().date()
    recent_ids = set()
    hire_by_id = {}          # norm id -> hire date (for the S101 due column)
    alias_by_eid = {}        # norm id -> {normalized "first last"} incl. preferred name
    basic_emp = {}   # norm id -> (status, labor, position, name): badge join fallback
    for r in read_csv("empinfo"):
        nid = norm_eid(r["Employee Id"])
        if nid:
            basic_emp[nid] = (r.get("Employee Status Description", "").strip(),
                              r.get("Labor Dist Description", "").strip(),
                              r.get("Position Description", "").strip(),
                              f"{(r.get('Preferred First Name', '').strip() or r.get('First Name', '').strip())} "
                              f"{r.get('Last Name', '').strip()}".strip())
        last = r.get("Last Name", "").strip()
        if last:
            # Both legal and preferred first names, so an LMS row filed under a
            # nickname (Tony/Antonio, Britt/Brittany) still joins to S101.
            variants = {norm_name(f"{fn} {last}")
                        for fn in (r.get("First Name", ""),
                                   r.get("Preferred First Name", "")) if fn.strip()}
            if variants:
                alias_by_eid[nid] = variants
        hd = r.get("Hire Date", "").strip()
        if not hd:
            continue
        try:
            hired = datetime.strptime(hd, "%m/%d/%Y").date()
        except ValueError:
            continue
        hire_by_id[nid] = hired
        if (today - hired).days <= NEW_HIRE_GRACE_DAYS:
            recent_ids.add(nid)
    print(f"hire dates: {len(hire_by_id)} employees; {len(recent_ids)} hired within "
          f"{NEW_HIRE_GRACE_DAYS} days (S101 gaps count Incomplete, not Overdue)")

    # ---- Early terminations: exclude leavers Paylocity still shows Active.
    #      S101/expiring rows carry ids; the LMS transcript only has names,
    #      so those are matched by normalized name (the list is ~a couple
    #      dozen people — a same-name collision with an active employee is
    #      possible but far rarer than the daily leaver noise this removes).
    et_rows = read_csv("earlyterm", missing_ok=True)
    et_ids = {norm_eid(r["Employee Id"]) for r in et_rows}
    et_names = {norm_name(f"{r.get('First Name', '')} {r.get('Last Name', '')}")
                for r in et_rows}
    print(f"early terminations excluded: {len(et_rows)} people")

    # ---- Location aggregates (shared LMS + S101 counters)
    loc_agg = {}

    def agg(loc):
        return loc_agg.setdefault(loc, {"lmsC": 0, "lmsO": 0, "lmsI": 0,
                                        "sC": 0, "sO": 0, "sI": 0, "sP": 0})

    # ---- LMS transcript: Completed / Overdue / Incomplete (= Not Started + In Progress)
    lms_detail = []          # Overdue + Incomplete rows for the on-page table
    lms_people = {}          # norm name -> {name, locs, c, o, i}
    LMS_STATE = {"Completed": "c", "Overdue": "o",
                 "Not Started": "i", "In Progress": "i"}
    for r in read_csv("lms"):
        emp = " ".join(r["Employee"].split())
        course = " ".join(r["Course Title"].split())
        loc = r["Location"].strip()
        status = r["Status"].strip()
        due = r.get("Due Date", "").strip()
        if not emp or status not in LMS_STATE or loc in DASH_EXCLUDE:
            continue
        if norm_name(emp) in et_names:
            continue          # early-termed (form ahead of Paylocity)
        state = LMS_STATE[status]
        a = agg(loc)
        a[{"c": "lmsC", "o": "lmsO", "i": "lmsI"}[state]] += 1
        p = lms_people.setdefault(norm_name(emp), {
            "name": emp, "locs": set(), "c": 0, "o": 0, "i": 0})
        p["locs"].add(loc)
        p[state] += 1
        if state == "o":
            lms_detail.append([emp, course, loc, short_date(due)])

    # ---- Safety101 fact table: Comp? = 1 Completed; else Overdue, or
    #      Incomplete when the employee is inside the new-hire grace window
    emp_names = {r["Employee ID"].strip(): (r["Full Name"].strip(),
                                            r["Job Title"].strip())
                 for r in read_csv("emp")}
    s101_people = {}         # emp id -> {locs, c, o, i}
    for r in read_csv("s101"):
        eid, loc = r["Emp ID"].strip(), r["Location"].strip()
        if loc in DASH_EXCLUDE or norm_eid(eid) in et_ids:
            continue
        try:
            comp = float(r["Comp?"])
        except ValueError:
            continue
        a = agg(loc)
        p = s101_people.setdefault(eid, {"locs": set(), "c": 0, "o": 0, "i": 0, "p": 0})
        p["locs"].add(loc)
        if comp == 1:
            a["sC"] += 1
            p["c"] += 1
        elif norm_eid(eid) in recent_ids:
            a["sI"] += 1
            p["i"] += 1
        else:
            a["sO"] += 1
            p["o"] += 1
            if comp == 0.5:      # Leadership I / Management Essentials partial —
                a["sP"] += 1     # still overdue, but counts half in the % (below)
                p["p"] += 1

    # ---- Overdue Safety101 qualifications (detail table): Never Granted/Expired
    #      rows, excluding new hires still in their grace window. Due date =
    #      hire date + grace days.
    exp_detail = []
    for r in read_csv("expiring"):
        exp = r["Expiration Status"].strip()
        if r["Department"].strip() in DASH_EXCLUDE:
            continue
        if not ("Never" in exp or "Expired" in exp):
            continue          # future "Expires on ..." rows are compliant
        eid = norm_eid(r["Employee ID"])
        if eid in et_ids:
            continue          # early-termed (form ahead of Paylocity)
        if eid in recent_ids:
            continue          # still in the new-hire grace window — Incomplete
        hired = hire_by_id.get(eid)
        due = ((hired + timedelta(days=NEW_HIRE_GRACE_DAYS)).strftime("%m/%d/%y")
               if hired else "")
        exp_detail.append([last_first_to_display(r["Employee"]),
                           r["Job Qualification"].strip(),
                           r["Department"].strip(), due])
    exp_detail.sort(key=lambda x: (x[2], x[0]))

    # ---- Join S101 people to LMS people by name for the watchlist
    lms_by_fl = {}
    for key, p in lms_people.items():
        toks = key.split()
        if len(toks) >= 2:
            lms_by_fl.setdefault((toks[0], toks[-1]), []).append(key)
    people, claimed, fallback_hits, misses = [], set(), 0, 0
    for eid, sp in sorted(s101_people.items()):
        raw, title = emp_names.get(eid, (f"Employee {eid}", ""))
        display = last_first_to_display(raw)
        # Candidate join keys: the S101/legal display name plus BEI name variants
        # (incl. Preferred First Name) so an LMS row under a nickname still matches.
        cand_keys = {norm_name(display)} | alias_by_eid.get(norm_eid(eid), set())
        match = next((ck for ck in cand_keys
                      if ck in lms_people and ck not in claimed), None)
        if not match:
            for ck in cand_keys:
                toks = ck.split()
                if len(toks) < 2:
                    continue
                cands = [k for k in lms_by_fl.get((toks[0], toks[-1]), [])
                         if k not in claimed]
                if len(cands) == 1:
                    match, fallback_hits = cands[0], fallback_hits + 1
                    break
        entry = {"n": display, "t": title, "l": sorted(sp["locs"]),
                 "sc": sp["c"], "so": sp["o"], "si": sp["i"], "sp": sp["p"],
                 "lc": 0, "lo": 0, "li": 0}
        if match:
            lp = lms_people[match]
            claimed.add(match)
            entry["l"] = sorted(set(entry["l"]) | lp["locs"])
            entry["lc"], entry["lo"], entry["li"] = lp["c"], lp["o"], lp["i"]
        else:
            misses += 1
        people.append(entry)
    for key, lp in lms_people.items():
        if key in claimed:
            continue
        people.append({"n": lp["name"], "t": "", "l": sorted(lp["locs"]),
                       "sc": 0, "so": 0, "si": 0, "sp": 0,
                       "lc": lp["c"], "lo": lp["o"], "li": lp["i"]})
    print(f"name join: {len(s101_people) - misses}/{len(s101_people)} S101 "
          f"people matched to LMS ({fallback_hits} via first+last fallback); "
          f"{len(lms_people) - len(claimed)} LMS-only people")

    # ---- Badges (the Credentials tab): one row per badge from the
    #      consolidated Badges.csv. Time-based statuses are recomputed at
    #      build time so the page tracks today's date; lifecycle statuses
    #      (Termed / Not Badged / Deactivated) are kept as stored.
    KEEP_STATUS = {"Termed", "Not Badged", "Deactivated"}
    TERM_GROUP = {"Terminated", "Retired", "Deceased"}
    # labor dist -> managers (from Location Management) = who gets contacted
    loc_mgrs = {}
    for mgr, locs in managers.items():
        for l in locs:
            loc_mgrs.setdefault(l, []).append(mgr)
    # Active employees: Current Employees.csv (id, status, Home Labor
    # Assignment). Terminated employees: Terminated Employees.csv - the
    # source of truth for the terminated list, including Employee IDs.
    cur_emp = {}
    for r in read_csv("current"):
        cid = norm_eid(r.get("Employee Id", ""))
        if cid:
            cur_emp[cid] = (r.get("Status", "").strip() or "Active",
                            r.get("Home Labor Assignment", "").strip(),
                            r.get("Position", "").strip(),
                            f"{r.get('First Name', '').strip()} "
                            f"{r.get('Last Name', '').strip()}".strip())
    term_emp, term_names = {}, {}
    for r in read_csv("termedcsv"):
        tid = norm_eid(r.get("Employee Id", ""))
        if not tid:
            continue
        term_emp[tid] = (r.get("Labor Dist Description", "").strip(),
                         (r.get("Position Description", "").strip()
                          or r.get("Job Title", "").strip()),
                         f"{(r.get('Preferred First Name', '').strip() or r.get('First Name', '').strip())} "
                         f"{r.get('Last Name', '').strip()}".strip())
        tlast = r.get("Last Name", "").strip()
        for fn in (r.get("First Name", ""), r.get("Preferred First Name", "")):
            if fn.strip() and tlast:
                key = norm_name(f"{fn} {tlast}")
                term_names[key] = (None if key in term_names
                                   and term_names[key] != tid else tid)
    badge_rows, badge_locs, badge_alerts = [], set(), []
    for r in read_csv("badges"):
        bname = r.get("Employee Name", "").strip()
        if not bname:
            continue
        bloc = r.get("Location", "").strip()
        stored = r.get("Status", "").strip()
        bexp = r.get("Expiration Date", "").strip()
        if stored in KEEP_STATUS:
            status = stored
        else:
            try:
                d = datetime.strptime(bexp, "%Y-%m-%d").date()
                status = ("Expired" if d < today else
                          "Expiring" if (d - today).days <= BADGE_SOON_DAYS
                          else "Active")
            except ValueError:
                status = "Unknown"
        if bloc:
            badge_locs.add(bloc)
        # employee join, by normalized Employee ID: Current Employees first
        # (active list), then Terminated Employees. A badge with no usable ID
        # gets one last chance against the terminated list BY UNIQUE NAME -
        # that list is authoritative incl. IDs, so the match also supplies
        # the displayed ID. Everything else stays Unmatched for Clara's
        # manual fix in Badges.csv - never guessed, never dropped.
        nid = norm_eid(r.get("Employee ID", ""))
        shown_eid = r.get("Employee ID", "").strip()
        # position is standardized from the employee files for matched
        # employees (auto-updates on promotion); the badge tracker's
        # hand-typed value survives only for unmatched records
        # matched employees display their official name from the employee
        # files ("First Last", normal case) - the old tabs' spellings
        # ("PENTS, ANTHONY") stay in Badges.csv as history, never on screen
        cname = ""
        if nid and nid in et_ids and nid in cur_emp:
            # early-terminated: termed via the Termination Form but still
            # Active in Paylocity - the badge side treats them as terminated
            # (badges move to Deactivated; unreturned ones alert)
            _st, labor, position, cname = cur_emp[nid]
            es = "Terminated"
        elif nid and nid in cur_emp:
            es, labor, position, cname = cur_emp[nid]
        elif nid and nid in term_emp:
            es = "Terminated"
            labor, position, cname = term_emp[nid]
        elif nid and nid in basic_emp:
            # fallback: Basic Employee Info covers employees the Current /
            # Terminated extracts miss (gap flagged to Sam 2026-08-27)
            es, labor, position, cname = basic_emp[nid]
        else:
            tmatch = term_names.get(norm_name(bname))
            if tmatch:
                es = "Terminated"
                labor, position, cname = term_emp[tmatch]
                shown_eid = shown_eid or tmatch
            else:
                es, labor, position = "Unmatched", "", ""
        bid = r.get("Badge ID", "").strip()
        num = r.get("Badge Number", "").strip()
        returned = r.get("Returned", "").strip()
        # a row without a badge number is not a badge - it is a person on
        # record without one (unless it is a closed-out/termed record)
        if (not num or num.upper() == "N/A") and status not in (
                "Termed", "Deactivated") and es not in TERM_GROUP:
            status = "Not Badged"
        def ynorm(v):
            v = v.strip()
            return ("Yes" if v[:1].upper() == "Y"
                    else "No" if v[:1].upper() == "N" else v)
        badge_rows.append([
            bid, shown_eid, cname or bname,
            position or r.get("Position", "").strip(),
            num, r.get("Badge Type", "").strip(),
            bexp, status, returned, bloc, labor, es,
            r.get("Deactivation Reason", "").strip(),
            # per-badge specification answers (blank = never recorded)
            ynorm(r.get("Escort", "")),
            ynorm(r.get("CBP Access", "")),
            ynorm(r.get("AOA Driving Privileges", "")),
            r.get("Additional Access", "").strip(),
            r.get("Received By", "").strip(),   # [17] returned-badge tooltip
            r.get("Deactivated Date", "").strip(),   # [18] Deactivated column
            ynorm(r.get("Tool Access", "")),    # [19] per-badge answer
            r.get("Customs Number", "").strip(),   # [20] shown when Customs=Yes
        ])
        # alert: terminated employee whose badge has not been closed out.
        # Cleared by the close-out flow (which requires the return for
        # terminations going forward); legacy records were bulk-closed at
        # process start (2026-08-27) with prior return status unknown.
        if (es in TERM_GROUP and returned != "Yes"
                and num and num.upper() != "N/A"
                and status not in ("Deactivated", "Termed", "Not Badged")):
            badge_alerts.append([bid, bname, num, bloc, labor,
                                 "; ".join(loc_mgrs.get(labor, [])), es])
    border = {"Expired": 0, "Expiring": 1, "Unknown": 2, "Active": 3,
              "Not Badged": 4, "Termed": 5}
    badge_rows.sort(key=lambda b: (border.get(b[7], 9), b[9], b[2]))
    n_unmatched = sum(1 for b in badge_rows if b[11] == "Unmatched")
    n_termed = sum(1 for b in badge_rows if b[11] in TERM_GROUP or b[7] == "Termed")
    print(f"badges: {len(badge_rows)} rows across {len(badge_locs)} locations; "
          f"{n_termed} on the Terminated tab, {n_unmatched} unmatched IDs, "
          f"{len(badge_alerts)} unreturned-badge alerts")

    # ---- Platform alerts feed: everything the platform's inbox needs to
    #      notify the right people, published in training_data.json. Kinds:
    #      expiring (within ALERT_SOON_DAYS), expired (every currently-
    #      expired badge - a standing compliance state, deduped by the
    #      platform's alert tags), unreturned (terminated employee whose
    #      badge is not closed out). The page shows Expiring at
    #      BADGE_SOON_DAYS for visibility; alerts fire at ALERT_SOON_DAYS
    #      ("see it coming at 30, get alerted at 7" - Clara, 2026-09-02).
    ALERT_SOON_DAYS = 7
    PAGES_URL = ("https://foxtrot-aviation-services.github.io/"
                 "foxtrot-training-pulse-7fd5c381/")
    alerts_feed = []
    for b in badge_rows:
        if (not b[4] or b[8] == "Yes" or b[11] in TERM_GROUP
                or b[7] in ("Termed", "Deactivated", "Not Badged")):
            continue
        try:
            bdd = datetime.strptime(b[6], "%Y-%m-%d").date()
        except ValueError:
            continue
        days = (bdd - today).days
        kind = ("expired" if days < 0
                else "expiring" if days <= ALERT_SOON_DAYS else None)
        if not kind:
            continue
        alerts_feed.append({
            "kind": kind, "badgeId": b[0], "employee": b[2],
            "employeeId": b[1], "badgeNumber": b[4], "badgeType": b[5],
            "location": b[9], "laborDist": b[10], "expiration": b[6],
            "daysLeft": days, "contacts": loc_mgrs.get(b[9], []),
            "link": f"{PAGES_URL}#renew/{b[0]}",
        })
    for a in badge_alerts:
        alerts_feed.append({
            "kind": "unreturned", "badgeId": a[0], "employee": a[1],
            "badgeNumber": a[2], "location": a[3], "laborDist": a[4],
            "contacts": a[5].split("; ") if a[5] else [], "empStatus": a[6],
            "link": f"{PAGES_URL}#badge/{a[0]}",
        })
    kc = {}
    for a in alerts_feed:
        kc[a["kind"]] = kc.get(a["kind"], 0) + 1
    print(f"alerts feed: {kc or 'empty'}")

    # ---- Coverage: employed vs badged, grouped by labor distribution.
    #      "Badged" = holds at least one live badge with a real number
    #      (not deactivated/termed/returned). Employee counts come from
    #      Current Employees.csv, so the axis is the HR labor dist.
    live_badged, live_locs = set(), set()
    for b in badge_rows:
        if (b[4] and b[7] in ("Active", "Expiring", "Expired", "Unknown")
                and b[8] != "Yes"):
            bnid = norm_eid(b[1])
            if bnid:
                live_badged.add(bnid)
            if b[9] and b[11] != "Unmatched":
                live_locs.add(b[9])
    cov_agg = {}
    for cid, (_st, clabor, _pos, _cn) in cur_emp.items():
        ld = clabor or "(no labor dist)"
        c = cov_agg.setdefault(ld, [0, 0])
        c[0] += 1
        if cid in live_badged:
            c[1] += 1
    # folders = every labor dist with employees PLUS every location that has
    # live badges filed to it (e.g. SRQ badges with no matching labor dist)
    folders = sorted(set(cov_agg) | live_locs)
    coverage = [[ld, *cov_agg.get(ld, [0, 0])] for ld in folders]
    # per-employee list for the coverage drill-down (one row per current
    # employee: id, name, labor dist, position)
    employees = sorted(
        ([cid, cn, clabor or "(no labor dist)", cpos]
         for cid, (_cs, clabor, cpos, cn) in cur_emp.items()),
        key=lambda e: (e[2], e[1].lower()))
    print(f"coverage: {sum(v[1] for v in cov_agg.values())} of "
          f"{sum(v[0] for v in cov_agg.values())} current employees hold a live "
          f"badge, across {len(coverage)} labor distributions")

    # ---- Badge Specifications by Location: owner-editable control table
    #      (Definitive Lists/Badge Specifications.csv). Drives the New Badge
    #      form defaults; adding a location = adding a row, no code change.
    badge_specs = {}
    try:
        for r in read_csv("specs"):
            sloc = r.get("Location", "").strip()
            if sloc:
                # every non-blank field is an OPEN Yes/No question at that
                # location (never pre-answered); blank = the question does not
                # exist there. CBP value "Leadership only" changes its label to
                # "CBP (Leadership Only)". Extra Question = location-exclusive
                # (e.g. BNA's Ramp Access, GoJet's Green Stripe).
                badge_specs[sloc] = [
                    r.get("Badge Required", "").strip() or "Yes",
                    r.get("Badge Type", "").strip(),
                    r.get("Escort", "").strip(),
                    r.get("CBP Access", "").strip(),
                    r.get("AOA Driving Privileges", "").strip(),
                    r.get("Tool Access", "").strip(),
                    r.get("Extra Question", "").strip(),
                    r.get("Display Note", "").strip(),   # shown on the breakdown
                    r.get("Number Optional", "").strip(),   # [8] access-keys locations
                ]
    except FileNotFoundError:
        print("badge specs: Badge Specifications.csv not found - form defaults off")
    print(f"badge specs: {len(badge_specs)} locations configured "
          f"({sum(1 for v in badge_specs.values() if v[0].lower() == 'no')} no-badge)")

    # ---- Active roster, minimal fields, for the New Badge employee picker
    roster_min = []
    for r in read_csv("roster"):
        eid = r.get("Employee Id", "").strip()
        first = (r.get("Preferred First Name", "").strip()
                 or r.get("First Name", "").strip())
        last = r.get("Last Name", "").strip()
        if eid and (first or last):
            roster_min.append([eid, f"{first} {last}".strip(),
                               r.get("Labor Dist", "").strip(),
                               r.get("Job Title", "").strip()])
    roster_min.sort(key=lambda x: x[1].lower())
    print(f"roster picker: {len(roster_min)} active employees")

    # ---- Freshness: per-source last-refresh times. The header "Data as of"
    #      shows the OLDEST of the data sources — nothing on the page is staler
    #      than that. In CI, sources_meta.json carries SharePoint's true
    #      lastModifiedDateTime (file mtimes there are just download times).
    meta = {}
    override = os.environ.get("TRAIN_DATA_DIR")
    if override and (Path(override) / "sources_meta.json").exists():
        raw = json.loads((Path(override) / "sources_meta.json").read_text())
        for name, iso in raw.items():
            if iso:
                meta[name] = datetime.fromisoformat(
                    iso.replace("Z", "+00:00")).astimezone()
    times = {}
    for k in SOURCES:
        name = PureWindowsPath(SOURCES[k]).name
        if name in meta:
            times[k] = meta[name]
        else:
            try:
                times[k] = datetime.fromtimestamp(
                    src_path(k).stat().st_mtime).astimezone()
            except OSError:
                times[k] = None
    fresh = {k: (t.strftime("%b %d %I:%M %p") if t else "?")
             for k, t in times.items()}
    # badges are excluded from asof on purpose: the file updates on badge
    # events, not daily, so it would drag the training "Data as of" backward.
    # The Badges tab shows its own freshness stamp instead.
    data_times = [times[k] for k in ("lms", "s101", "expiring", "empinfo") if times[k]]
    asof = min(data_times) if data_times else datetime.now().astimezone()
    print(f"data as of (oldest source): {asof.strftime('%b %d %I:%M %p')}")

    data = {
        "generated": datetime.now().strftime("%b %d, %Y %I:%M %p"),
        "asof": asof.strftime("%b %d, %Y %I:%M %p"),
        "benchmark": 0.85,
        "managers": {m: sorted(v) for m, v in sorted(managers.items())},
        "mgmtLocs": mgmt_locs,
        "locations": sorted(loc_agg),
        "locAgg": loc_agg,
        "lmsRows": lms_detail,
        "expRows": exp_detail,
        "people": people,
        "badges": badge_rows,
        "alerts": badge_alerts,
        "alertsFeed": alerts_feed,
        "alertSoonDays": ALERT_SOON_DAYS,
        "coverage": coverage,
        "employees": employees,
        "folders": folders,
        "badgeSpecs": badge_specs,
        "roster": roster_min,
        "badgeLocs": sorted(badge_locs),
        "badgeSoonDays": BADGE_SOON_DAYS,
        "badgesAsof": fresh["badges"],
        "fresh": {"LMS transcript": fresh["lms"],
                  "Safety101 data": fresh["s101"],
                  "S101 qualifications report": fresh["expiring"],
                  "Hire dates": fresh["empinfo"],
                  "Badge records": fresh["badges"]},
    }

    payload = json.dumps(data, separators=(",", ":"))
    html = (HERE / "template.html").read_text(encoding="utf-8")
    (HERE / "index.html").write_text(
        html.replace("/*__DATA__*/", payload), encoding="utf-8")
    (HERE / "training_data.json").write_text(payload, encoding="utf-8")
    print(f"index.html written — {len(payload):,} bytes of data, "
          f"{len(lms_detail)} LMS detail rows, {len(exp_detail)} S101 detail "
          f"rows, {len(people)} people")


if __name__ == "__main__":
    main()

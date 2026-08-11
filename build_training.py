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

HERE = Path(__file__).parent
DATA_HUB = Path(r"C:\Users\samko\Foxtrot Aviation Services\Data Hub - Documents")

SOURCES = {
    "mgmt": r"Power BI Data Sources\Location Management.csv",
    "lms": r"Flow Dumps\dashboard-transcript\learner_transcript.csv",
    "s101": r"Safety101\S101 Compliance\Safety101 Data.csv",
    "expiring": r"Safety101\S101 Compliance\Foxtrot Aviation Services Entire Organization Expiring Training.csv",
    "emp": r"Safety101\S101 Compliance\Safety101 Emp Import.csv",
    "empinfo": r"Paylocity Reports\Basic Employee Info.csv",
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


def read_csv(key):
    with open(src_path(key), newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def norm_name(s):
    """lowercase, letters only, collapsed — join key for LMS<->S101 names."""
    return " ".join(re.sub(r"[^a-z ]", " ", s.lower()).split())


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
    for r in read_csv("empinfo"):
        hd = r.get("Hire Date", "").strip()
        if not hd:
            continue
        try:
            hired = datetime.strptime(hd, "%m/%d/%Y").date()
        except ValueError:
            continue
        hire_by_id[norm_eid(r["Employee Id"])] = hired
        if (today - hired).days <= NEW_HIRE_GRACE_DAYS:
            recent_ids.add(norm_eid(r["Employee Id"]))
    print(f"hire dates: {len(hire_by_id)} employees; {len(recent_ids)} hired within "
          f"{NEW_HIRE_GRACE_DAYS} days (S101 gaps count Incomplete, not Overdue)")

    # ---- Location aggregates (shared LMS + S101 counters)
    loc_agg = {}

    def agg(loc):
        return loc_agg.setdefault(loc, {"lmsC": 0, "lmsO": 0, "lmsI": 0,
                                        "sC": 0, "sO": 0, "sI": 0})

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
        state = LMS_STATE[status]
        a = agg(loc)
        a[{"c": "lmsC", "o": "lmsO", "i": "lmsI"}[state]] += 1
        p = lms_people.setdefault(norm_name(emp), {
            "name": emp, "locs": set(), "c": 0, "o": 0, "i": 0})
        p["locs"].add(loc)
        p[state] += 1
        if state == "o":
            lms_detail.append([emp, course, loc, due])

    # ---- Safety101 fact table: Comp? = 1 Completed; else Overdue, or
    #      Incomplete when the employee is inside the new-hire grace window
    emp_names = {r["Employee ID"].strip(): (r["Full Name"].strip(),
                                            r["Job Title"].strip())
                 for r in read_csv("emp")}
    s101_people = {}         # emp id -> {locs, c, o, i}
    for r in read_csv("s101"):
        eid, loc = r["Emp ID"].strip(), r["Location"].strip()
        if loc in DASH_EXCLUDE:
            continue
        try:
            comp = float(r["Comp?"])
        except ValueError:
            continue
        a = agg(loc)
        p = s101_people.setdefault(eid, {"locs": set(), "c": 0, "o": 0, "i": 0})
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
        if eid in recent_ids:
            continue          # still in the new-hire grace window — Incomplete
        hired = hire_by_id.get(eid)
        due = ((hired + timedelta(days=NEW_HIRE_GRACE_DAYS)).strftime("%m/%d/%Y")
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
        key = norm_name(display)
        toks = key.split()
        match = key if key in lms_people else None
        if not match and len(toks) >= 2:
            cands = [k for k in lms_by_fl.get((toks[0], toks[-1]), [])
                     if k not in claimed]
            if len(cands) == 1:
                match, fallback_hits = cands[0], fallback_hits + 1
        entry = {"n": display, "t": title, "l": sorted(sp["locs"]),
                 "sc": sp["c"], "so": sp["o"], "si": sp["i"],
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
                       "sc": 0, "so": 0, "si": 0,
                       "lc": lp["c"], "lo": lp["o"], "li": lp["i"]})
    print(f"name join: {len(s101_people) - misses}/{len(s101_people)} S101 "
          f"people matched to LMS ({fallback_hits} via first+last fallback); "
          f"{len(lms_people) - len(claimed)} LMS-only people")

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
        "fresh": {"LMS transcript": fresh["lms"],
                  "Safety101 data": fresh["s101"],
                  "S101 qualifications report": fresh["expiring"],
                  "Hire dates": fresh["empinfo"]},
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

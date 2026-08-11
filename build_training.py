"""Build the Foxtrot Training Pulse page.

Reproduces the "Training Compliance.pbix" dashboard from its four raw sources
(the pbix itself is never read) and renders index.html from template.html with
the data embedded. Run with no env vars to read the synced Data Hub folder;
set TRAIN_DATA_DIR to a folder holding the four CSVs to mimic CI.

Owner-simplified model (Aug 2026): everything is either Overdue or Compliant.
  LMS: only Completed and Overdue rows count — Not Started / In Progress are
       ignored entirely, like walks in a batting average.
  Safety101: Comp? = 1 is Compliant; anything less (0 or 0.5) is Overdue.
  All percentages everywhere = Compliant / (Compliant + Overdue).
  Benchmark = 0.85 (pbix [Compliance Benchmark]).
"""

import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DATA_HUB = Path(r"C:\Users\samko\Foxtrot Aviation Services\Data Hub - Documents")

SOURCES = {
    "mgmt": r"Power BI Data Sources\Location Management.csv",
    "lms": r"Flow Dumps\dashboard-transcript\learner_transcript.csv",
    "s101": r"Safety101\S101 Compliance\Safety101 Data.csv",
    "expiring": r"Safety101\S101 Compliance\Foxtrot Aviation Services Entire Organization Expiring Training.csv",
    "emp": r"Safety101\S101 Compliance\Safety101 Emp Import.csv",
}

# Locations the pbix Management query filters out of Location Management.csv
EXCLUDE_LOCS = {"DFW AA", "MQY", "OFFCAK Akron-Canton Office", "SPMZ"}


def src_path(key):
    override = os.environ.get("TRAIN_DATA_DIR")
    if override:
        return Path(override) / Path(SOURCES[key]).name
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
        if loc in EXCLUDE_LOCS or lms_loc in EXCLUDE_LOCS:
            continue
        managers.setdefault(mgr, set()).add(lms_loc)
    mgmt_locs = sorted({l for locs in managers.values() for l in locs})

    # ---- Location aggregates (shared LMS + S101 counters)
    loc_agg = {}

    def agg(loc):
        return loc_agg.setdefault(loc, {"lmsC": 0, "lmsO": 0, "sC": 0, "sO": 0})

    # ---- LMS transcript: only Completed and Overdue count
    lms_detail = []          # Overdue rows for the on-page table
    lms_people = {}          # norm name -> {name, locs, c, o}
    for r in read_csv("lms"):
        emp = " ".join(r["Employee"].split())
        course = " ".join(r["Course Title"].split())
        loc = r["Location"].strip()
        status = r["Status"].strip()
        due = r.get("Due Date", "").strip()
        if not emp or status not in ("Completed", "Overdue"):
            continue
        a = agg(loc)
        p = lms_people.setdefault(norm_name(emp), {
            "name": emp, "locs": set(), "c": 0, "o": 0})
        p["locs"].add(loc)
        if status == "Completed":
            a["lmsC"] += 1
            p["c"] += 1
        else:
            a["lmsO"] += 1
            p["o"] += 1
            lms_detail.append([emp, course, loc, due])

    # ---- Safety101 fact table: Comp? = 1 compliant, anything less overdue
    emp_names = {r["Employee ID"].strip(): (r["Full Name"].strip(),
                                            r["Job Title"].strip())
                 for r in read_csv("emp")}
    s101_people = {}         # emp id -> {locs, c, o}
    for r in read_csv("s101"):
        eid, loc = r["Emp ID"].strip(), r["Location"].strip()
        try:
            comp = float(r["Comp?"])
        except ValueError:
            continue
        a = agg(loc)
        p = s101_people.setdefault(eid, {"locs": set(), "c": 0, "o": 0})
        p["locs"].add(loc)
        if comp == 1:
            a["sC"] += 1
            p["c"] += 1
        else:
            a["sO"] += 1
            p["o"] += 1

    # ---- Overdue Safety101 qualifications (detail table): Never Granted/Expired only
    exp_detail = []
    for r in read_csv("expiring"):
        exp = r["Expiration Status"].strip()
        if not ("Never" in exp or "Expired" in exp):
            continue          # future "Expires on ..." rows are compliant
        exp_detail.append([last_first_to_display(r["Employee"]),
                           r["Job Qualification"].strip(),
                           r["Department"].strip()])
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
                 "sc": sp["c"], "so": sp["o"], "lc": 0, "lo": 0}
        if match:
            lp = lms_people[match]
            claimed.add(match)
            entry["l"] = sorted(set(entry["l"]) | lp["locs"])
            entry["lc"], entry["lo"] = lp["c"], lp["o"]
        else:
            misses += 1
        people.append(entry)
    for key, lp in lms_people.items():
        if key in claimed:
            continue
        people.append({"n": lp["name"], "t": "", "l": sorted(lp["locs"]),
                       "sc": 0, "so": 0, "lc": lp["c"], "lo": lp["o"]})
    print(f"name join: {len(s101_people) - misses}/{len(s101_people)} S101 "
          f"people matched to LMS ({fallback_hits} via first+last fallback); "
          f"{len(lms_people) - len(claimed)} LMS-only people")

    # ---- Freshness
    fresh = {}
    for k in SOURCES:
        try:
            fresh[k] = datetime.fromtimestamp(
                src_path(k).stat().st_mtime).strftime("%b %d %I:%M %p")
        except OSError:
            fresh[k] = "?"

    data = {
        "generated": datetime.now().strftime("%b %d, %Y %I:%M %p"),
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
                  "S101 qualifications report": fresh["expiring"]},
    }

    payload = json.dumps(data, separators=(",", ":"))
    html = (HERE / "template.html").read_text(encoding="utf-8")
    (HERE / "index.html").write_text(
        html.replace("/*__DATA__*/", payload), encoding="utf-8")
    (HERE / "training_data.json").write_text(payload, encoding="utf-8")
    print(f"index.html written — {len(payload):,} bytes of data, "
          f"{len(lms_detail)} LMS overdue rows, {len(exp_detail)} S101 overdue "
          f"rows, {len(people)} people")


if __name__ == "__main__":
    main()

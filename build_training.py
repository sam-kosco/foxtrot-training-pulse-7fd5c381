"""Build the Foxtrot Training Pulse page.

Reproduces the "Training Compliance.pbix" dashboard from its four raw sources
(the pbix itself is never read) and renders index.html from template.html with
the data embedded. Run with no env vars to read the synced Data Hub folder;
set TRAIN_DATA_DIR to a folder holding the four CSVs to mimic CI.

Measures replicated from the pbix data model:
  LMS gauge   = Completed / (Completed + Overdue)          [LMS Comp]
  S101 gauge  = SUM(Comp?) / COUNT                         [S101 Comp]
  Chart, LMS  = rows with Status <> "Overdue" / all rows   [Management[LMS Compliance]]
  Chart, S101 = rows with Comp? = 1 / all rows             [Management[S101 Compliance]]
  Benchmark   = 0.85                                       [Compliance Benchmark]

Intentional fix vs the pbix: the original combo chart plots
Sum(Management[LMS Compliance]) so locations with two managers double their
percentage; here each location's value is plotted once.
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
    """'Abreha, Matthew hailu' -> 'Matthew hailu Abreha' (title-cased lightly)."""
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

    # ---- LMS transcript
    lms_rows = read_csv("lms")
    loc_agg = {}   # loc -> mutable counters (shared with S101 below)

    def agg(loc):
        return loc_agg.setdefault(loc, {
            "lmsC": 0, "lmsO": 0, "lmsNS": 0, "lmsIP": 0, "lmsTot": 0,
            "sN": 0, "sSum": 0.0, "sC1": 0, "sNC": 0, "sP": 0})

    STATUS_KEY = {"Completed": "lmsC", "Overdue": "lmsO",
                  "Not Started": "lmsNS", "In Progress": "lmsIP"}
    lms_detail = []          # non-Completed rows for the on-page table
    lms_people = {}          # norm name -> {name, locs, c,o,ns,ip}
    for r in lms_rows:
        emp = " ".join(r["Employee"].split())
        course = " ".join(r["Course Title"].split())
        loc = r["Location"].strip()
        status = r["Status"].strip()
        due = r.get("Due Date", "").strip()
        if not emp or status not in STATUS_KEY:
            continue
        a = agg(loc)
        a[STATUS_KEY[status]] += 1
        a["lmsTot"] += 1
        p = lms_people.setdefault(norm_name(emp), {
            "name": emp, "locs": set(), "c": 0, "o": 0, "ns": 0, "ip": 0})
        p["locs"].add(loc)
        p[{"Completed": "c", "Overdue": "o", "Not Started": "ns",
           "In Progress": "ip"}[status]] += 1
        if status != "Completed":
            lms_detail.append([emp, course, loc, status, due])

    # ---- Safety101 fact table
    emp_names = {r["Employee ID"].strip(): (r["Full Name"].strip(),
                                            r["Job Title"].strip())
                 for r in read_csv("emp")}
    s101_people = {}         # emp id -> {locs, n, sum, c1, nc, p}
    for r in read_csv("s101"):
        eid, loc = r["Emp ID"].strip(), r["Location"].strip()
        try:
            comp = float(r["Comp?"])
        except ValueError:
            continue
        a = agg(loc)
        a["sN"] += 1
        a["sSum"] += comp
        a["sC1"] += 1 if comp == 1 else 0
        a["sNC"] += 1 if comp == 0 else 0
        a["sP"] += 1 if 0 < comp < 1 else 0
        p = s101_people.setdefault(eid, {
            "locs": set(), "n": 0, "sum": 0.0, "c1": 0, "nc": 0, "p": 0})
        p["locs"].add(loc)
        p["n"] += 1
        p["sum"] += comp
        p["c1"] += 1 if comp == 1 else 0
        p["nc"] += 1 if comp == 0 else 0
        p["p"] += 1 if 0 < comp < 1 else 0

    # ---- Expiring training (Safety101 detail table)
    exp_detail = []
    for r in read_csv("expiring"):
        exp = r["Expiration Status"].strip()
        status = ("Not Signed Off"
                  if ("Never" in exp or "Expired" in exp) else exp)
        key = "NC" if status == "Not Signed Off" else "C"
        exp_detail.append([last_first_to_display(r["Employee"]),
                           r["Job Qualification"].strip(),
                           r["Department"].strip(), status, key])
    exp_detail.sort(key=lambda x: x[3])   # pbix sorts ascending by Status

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
        entry = {"n": display, "t": title,
                 "l": sorted(sp["locs"]),
                 "st": sp["n"], "sc1": sp["c1"], "snc": sp["nc"], "sp": sp["p"],
                 "lt": 0, "lo": 0, "lns": 0, "lip": 0}
        if match:
            lp = lms_people[match]
            claimed.add(match)
            entry["l"] = sorted(set(entry["l"]) | lp["locs"])
            entry["lt"] = lp["c"] + lp["o"] + lp["ns"] + lp["ip"]
            entry["lo"], entry["lns"], entry["lip"] = lp["o"], lp["ns"], lp["ip"]
        else:
            misses += 1
        people.append(entry)
    for key, lp in lms_people.items():
        if key in claimed:
            continue
        people.append({"n": lp["name"], "t": "", "l": sorted(lp["locs"]),
                       "st": 0, "sc1": 0, "snc": 0, "sp": 0,
                       "lt": lp["c"] + lp["o"] + lp["ns"] + lp["ip"],
                       "lo": lp["o"], "lns": lp["ns"], "lip": lp["ip"]})
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
                  "Expiring training": fresh["expiring"]},
    }

    payload = json.dumps(data, separators=(",", ":"))
    html = (HERE / "template.html").read_text(encoding="utf-8")
    (HERE / "index.html").write_text(
        html.replace("/*__DATA__*/", payload), encoding="utf-8")
    (HERE / "training_data.json").write_text(payload, encoding="utf-8")
    print(f"index.html written — {len(payload):,} bytes of data, "
          f"{len(lms_detail)} LMS detail rows, {len(exp_detail)} expiring rows, "
          f"{len(people)} people")


if __name__ == "__main__":
    main()

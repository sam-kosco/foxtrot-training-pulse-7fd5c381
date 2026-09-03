"""One-time migration: consolidate the multi-tab Badge Tracker workbook
into a single Badges.csv plus an exceptions report for human review.

Reads (local paths, nothing is uploaded):
  - per-tab CSV exports of the Badge Tracker workbook (37 location tabs +
    Termed Employees; the Master tab is skipped - its formulas are broken
    and it holds no records). The workbook's stylesheet is malformed in a
    way openpyxl rejects, so the tabs were exported to CSV via Excel; each
    file is named after its tab.
  - Definitive Lists/Roster.csv           (canonical active roster, join by Employee Id)
  - Definitive Lists/Current Employees.csv (employment Status per Employee Id)

Writes (to OUT_DIR, which must stay OUTSIDE any git repo - data files are
never committed per org standards):
  - Badges.csv            one row per badge, normalized columns
  - badge_exceptions.csv  one row per record needing human review

Layout facts this parser depends on (verified against the real workbook):
  - location tabs have a banner in row 1 and headers in row 2; the Termed
    Employees tab has headers in row 1
  - several tabs carry unrelated side-tables to the right (driving
    privileges with DOB/license numbers, pasted rosters). Only the
    contiguous header run starting at column A is read, so those never
    enter the output - the DOB/license data is excluded on purpose.
  - STL Fac genuinely has ~2 badges (confirmed); its 400+ side rows are a
    pasted roster, not badges.
  - rows with Badged = No are unbadged personnel, not badges; they are kept
    with Status "Not Badged" (they feed the new-hire follow-up feature)
  - the two TUS tabs track no expiration dates at all - just an
    "Activated? (Y/N)" flag
  - CVG holds a second badge per person in "Badge Number:2" (customs seal);
    CLE has a "Black Constant Access Badge" column. Each becomes its own
    badge row. Neither has its own expiration column.
  - per-location access flags (CBP, DRIVING, AOA/Ramp Driving, Y Number)
    are out of scope for v1 and ignored; they are listed in the run summary.
"""

import csv
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

TABS_DIR = Path(r"C:\Users\clara\repos\badge-data\tabs")
HUB = Path(r"C:\Users\clara\Foxtrot Aviation Services\Data Hub - Documents")
ROSTER_CSV = HUB / "Definitive Lists" / "Roster.csv"
CURRENT_CSV = HUB / "Definitive Lists" / "Current Employees.csv"
OUT_DIR = Path(r"C:\Users\clara\repos\badge-data")

SOON_DAYS = 30          # "Expiring" window
SKIP_TABS = {"Master"}  # broken #REF! summary, no records
TERMED_TAB = "Termed Employees"

BADGE_FIELDS = [
    "Badge ID", "Employee ID", "Employee Name", "Location", "Position",
    "Badge Number", "Badge Type", "Expiration Date", "ASO", "Escort",
    "AOA Access", "Division", "Ramp", "FIS", "Keys/Cards", "Driver Status",
    "Status", "Returned", "Deactivated Date", "Notes", "Source Tab",
]
EXC_FIELDS = ["Badge ID", "Employee Name", "Location", "Issue", "Detail"]

# normalized header text -> canonical field. Headers are lowercased and
# stripped of punctuation before lookup, so "Escort:2" and "ASO? (Y/N)"
# match their base names.
HEADER_MAP = {
    "name": "Employee Name",
    "emp id": "Employee ID",
    "position": "Position",
    "division": "Division",
    "badge number": "Badge Number",
    "badge type": "Badge Type",
    "expiration date": "Expiration Date",
    "aso": "ASO",
    "aso y n": "ASO",
    "escort": "Escort",
    "escort 2": "Escort",
    "aoa access": "AOA Access",
    "badged": "Badged",
    "valid": "Valid",
    "ramp": "Ramp",
    "fis": "FIS",
    "fis pw turned in": None,     # BNA bookkeeping, not badge data
    "keys cards": "Keys/Cards",
    "driver status": "Driver Status",
    "terminated": "TERMINATED",
    "turned in": "TURNED IN",
    "activated y n": "Activated",          # TUS tabs: no expiry tracked
    "badge number 2": "Badge Number 2",    # CVG: second badge per person
    "customs seal": "Customs Seal",
    "black constant access badge": "Black Badge",  # CLE second badge
    # access-privilege flags, out of scope for v1:
    "cbp": None, "driving": None, "aoa driving": None,
    "ramp driving": None, "y number": None, "key": None, "column1": None,
}


def norm_header(v):
    if v is None:
        return ""
    s = re.sub(r"[^a-z0-9 ]", " ", str(v).lower())
    return re.sub(r"\s+", " ", s).strip()


def clean(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def parse_date(s):
    if isinstance(s, (datetime, date)):
        return s.date() if isinstance(s, datetime) else s
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def norm_name(s):
    return re.sub(r"\s+", " ", str(s).lower().replace(",", " ")).strip()


def load_roster():
    """Roster.csv -> (by_id, by_name). Name index maps 'first last' and
    'preferred last' to the employee id; ambiguous names map to None."""
    by_id, by_name = {}, {}
    with open(ROSTER_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            eid = row["Employee Id"].strip()
            by_id[eid] = row
            for first in (row["First Name"], row.get("Preferred First Name", "")):
                first = (first or "").strip()
                if not first:
                    continue
                key = norm_name(f"{first} {row['Last Name']}")
                by_name[key] = None if key in by_name and by_name[key] != eid else eid
    return by_id, by_name


def load_current_status():
    with open(CURRENT_CSV, newline="", encoding="utf-8-sig") as f:
        return {r["Employee Id"].strip(): r["Status"].strip()
                for r in csv.DictReader(f)}


def sheet_records(rows):
    """Yield dicts of canonical fields from one tab's row iterator. Reads
    only the contiguous header run starting at column A."""
    rows = iter(rows)
    first = next(rows, None)
    if first is None:
        return
    header_src = first
    if norm_header(first[0]) != "name":          # banner row, headers on row 2
        header_src = next(rows, None)
        if header_src is None:
            return
    cols = []
    for i, h in enumerate(header_src):
        hn = norm_header(h)
        if not hn:
            break                                 # end of contiguous run
        cols.append((i, HEADER_MAP.get(hn, f"?{hn}")))
    for raw in rows:
        rec = {}
        for i, field in cols:
            if field is None:
                continue
            rec[field] = clean(raw[i]) if i < len(raw) else ""
        if rec.get("Employee Name", ""):
            yield rec


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    roster_by_id, roster_by_name = load_roster()
    current_status = load_current_status()
    today = date.today()

    badges, exceptions = [], []
    unknown_headers = set()

    def flag(bid, name, loc, issue, detail=""):
        exceptions.append({"Badge ID": bid, "Employee Name": name,
                           "Location": loc, "Issue": issue, "Detail": detail})

    for tab_csv in sorted(TABS_DIR.glob("*.csv")):
        tab = tab_csv.stem
        if tab in SKIP_TABS:
            continue
        termed = tab == TERMED_TAB
        location = "" if termed else tab
        try:
            with open(tab_csv, newline="", encoding="utf-8-sig") as f:
                tab_rows = list(csv.reader(f))
        except UnicodeDecodeError:
            # Excel exports CSV in the Windows ANSI codepage, not UTF-8
            with open(tab_csv, newline="", encoding="cp1252") as f:
                tab_rows = list(csv.reader(f))
        for rec in sheet_records(tab_rows):
            unknown_headers.update(k[1:] for k in rec if k.startswith("?"))
            bid = f"BDG-{len(badges) + 1:04d}"
            name = rec.get("Employee Name", "")
            row = {f: "" for f in BADGE_FIELDS}
            row.update({k: v for k, v in rec.items() if k in BADGE_FIELDS})
            row.update({"Badge ID": bid, "Location": location,
                        "Source Tab": tab})

            # --- employee id: verify, or fill from roster by name ---
            eid = re.sub(r"\D", "", row["Employee ID"])
            row["Employee ID"] = eid
            if not eid:
                match = roster_by_name.get(norm_name(name))
                if match:
                    row["Employee ID"] = eid = match
                    flag(bid, name, location, "ID auto-filled from roster",
                         f"matched roster employee {match} by name - verify")
                else:
                    flag(bid, name, location, "missing Employee ID",
                         "no unique roster name match")
            elif eid not in roster_by_id:
                status = current_status.get(eid, "not found")
                if not termed:
                    flag(bid, name, location, "employee not on active roster",
                         f"Current Employees status: {status}")

            # --- status: termed / not badged / from expiration date ---
            not_badged = rec.get("Badged", "").strip().lower() == "no"
            exp = parse_date(rec.get("Expiration Date", ""))
            if exp:
                row["Expiration Date"] = exp.isoformat()
            if termed:
                row["Status"] = "Termed"
                row["Returned"] = "Yes" if rec.get("TURNED IN", "") else ""
            elif not_badged:
                row["Status"] = "Not Badged"
            elif exp:
                row["Status"] = ("Expired" if exp < today else
                                 "Expiring" if exp <= today + timedelta(days=SOON_DAYS)
                                 else "Active")
                if row["Status"] == "Expired" and rec.get("Valid", "").lower() == "yes":
                    flag(bid, name, location, "marked Valid but expired",
                         f"expiration {row['Expiration Date']}")
            elif "Expiration Date" not in rec:
                # tab tracks no expiration at all (TUS): use Activated flag
                if rec.get("Activated", "").strip().lower().startswith("y"):
                    row["Status"] = "Active"
                    row["Notes"] = "no expiration tracked at this location; Activated=Y"
                else:
                    row["Status"] = "Unknown"
                    flag(bid, name, location, "no expiration tracked, not Activated",
                         f"Activated={rec.get('Activated', '')!r}")
            else:
                row["Status"] = "Unknown"
                flag(bid, name, location, "missing/unreadable expiration",
                     repr(rec.get("Expiration Date", "")))

            badges.append(row)

            # --- second badges held in extra columns (CVG, CLE) ---
            for numfield, badge_type in (("Badge Number 2", "Customs Seal"),
                                         ("Black Badge", "Black Constant Access")):
                val = rec.get(numfield, "").strip()
                if not val or val.upper() == "N/A":
                    continue
                bid2 = f"BDG-{len(badges) + 1:04d}"
                row2 = dict(row)
                row2.update({
                    "Badge ID": bid2, "Badge Number": val,
                    "Badge Type": badge_type, "Expiration Date": "",
                    "Status": "Unknown", "Returned": "",
                    "Notes": f"second badge from column {numfield!r}; "
                             "source sheet has no expiration for it",
                })
                badges.append(row2)
                flag(bid2, name, location, "second badge missing expiration",
                     f"{badge_type} {val}")

    # --- duplicate badge numbers across different people ---
    seen = {}
    for row in badges:
        num = row["Badge Number"]
        if not num:
            if row["Status"] not in ("Termed", "Not Badged"):
                flag(row["Badge ID"], row["Employee Name"], row["Location"],
                     "missing badge number")
            continue
        key = (row["Location"], num)
        if key in seen and seen[key][1] != row["Employee Name"]:
            flag(row["Badge ID"], row["Employee Name"], row["Location"],
                 "duplicate badge number",
                 f"{num} also held by {seen[key][1]} ({seen[key][0]})")
        else:
            seen[key] = (row["Badge ID"], row["Employee Name"])

    with open(OUT_DIR / "Badges.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=BADGE_FIELDS, lineterminator="\r\n")
        w.writeheader()
        w.writerows(badges)
    with open(OUT_DIR / "badge_exceptions.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=EXC_FIELDS, lineterminator="\r\n")
        w.writeheader()
        w.writerows(exceptions)

    by_status = {}
    for row in badges:
        by_status[row["Status"]] = by_status.get(row["Status"], 0) + 1
    by_issue = {}
    for e in exceptions:
        by_issue[e["Issue"]] = by_issue.get(e["Issue"], 0) + 1

    print(f"badges: {len(badges)}")
    for k in sorted(by_status):
        print(f"  {k}: {by_status[k]}")
    print(f"exceptions: {len(exceptions)}")
    for k in sorted(by_issue):
        print(f"  {k}: {by_issue[k]}")
    if unknown_headers:
        print("unmapped headers (ignored):", ", ".join(sorted(unknown_headers)))
    print(f"written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Downloads the Training Pulse source files from SharePoint via Microsoft
Graph into ./sources (the same files the Training Compliance pbix queries).

Credentials (GitHub Secrets / env): TENANT_ID, CLIENT_ID, CLIENT_SECRET —
the Foxtrot Report Automation app, same as pulse-web and the compliance
trackers.
"""
import json
import os
import sys
from pathlib import Path

import requests

TENANT_ID = os.environ["TENANT_ID"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

# Data Hub document library (drive-relative paths, no "Shared Documents" prefix)
DRIVE_ID = "b!_bzXaIx86kOufgJN3ih-BaDIDthKYuxJkJtLi1Bm5irGjCEnK-VHSpBRRm3_SDKU"

FILES = [
    "Power BI Data Sources/Location Management.csv",
    "Flow Dumps/dashboard-transcript/learner_transcript.csv",
    "Safety101/S101 Compliance/Safety101 Data.csv",
    "Safety101/S101 Compliance/Foxtrot Aviation Services Entire Organization Expiring Training.csv",
    "Safety101/S101 Compliance/Safety101 Emp Import.csv",
    "Paylocity Reports/Basic Employee Info.csv",
    "Definitive Lists/Badges.csv",
]

OUT = Path(__file__).parent / "sources"


def get_token():
    resp = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def main():
    OUT.mkdir(exist_ok=True)
    token = get_token()
    print("Token acquired.")
    failed, meta = [], {}
    hdrs = {"Authorization": f"Bearer {token}"}
    for path in FILES:
        base = (f"https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}"
                f"/root:/{requests.utils.quote(path)}")
        name = path.rsplit("/", 1)[-1]
        m = requests.get(base + "?$select=lastModifiedDateTime", headers=hdrs, timeout=30)
        if m.status_code == 200:
            meta[name] = m.json().get("lastModifiedDateTime")
        r = requests.get(base + ":/content", headers=hdrs, timeout=120)
        if r.status_code == 200:
            (OUT / name).write_bytes(r.content)
            print(f"  ok  {name} ({len(r.content) // 1024} KB, modified {meta.get(name)})")
        else:
            failed.append((name, r.status_code))
            print(f"  FAIL {name}: HTTP {r.status_code}", file=sys.stderr)
    (OUT / "sources_meta.json").write_text(json.dumps(meta, indent=1))
    if failed:
        sys.exit(f"aborting: {len(failed)} downloads failed {failed}")


if __name__ == "__main__":
    main()

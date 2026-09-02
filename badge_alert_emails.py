"""Send badge alert emails from the alertsFeed the nightly build publishes.

Runs in CI right after build_training.py (see refresh.yml). Reads
training_data.json -> alertsFeed and sends via Microsoft Graph as
foxtrot.automation@ (house pattern - no SMTP, failures fail the run).

Anti-fatigue design (Clara, 2026-09-02):
  - expiring  : one email per location, each badge alerted ONCE per
                expiration date (a renewed badge re-alerts next cycle)
  - unreturned: alerted once per badge
  - expired   : a standing state, not an event - rolled into a WEEKLY
                per-location summary instead of daily nagging
badge_alerts_state.json (committed by the workflow) remembers what was
sent; state only advances after a successful send.

TEST MODE: until per-location distribution lists exist, RECIPIENTS_OVERRIDE
(comma-separated) routes every email there, with the would-be recipients
named in the body. Without an override the script refuses to send (prints
only) - real recipients are a deliberate decision, not a default.
DRY_RUN=1 renders and prints everything, sends nothing, changes no state.
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).parent
DATA_FILE = HERE / "training_data.json"
STATE_FILE = HERE / "badge_alerts_state.json"

DRY_RUN = os.environ.get("DRY_RUN", "") not in ("", "0", "false")
OVERRIDE = [e.strip() for e in os.environ.get("RECIPIENTS_OVERRIDE", "").split(",")
            if e.strip()]
SENDER = "foxtrot.automation@foxtrotaviation.com"
WEEKLY_DAYS = 7

TD = "padding:7px 10px;border-bottom:1px solid #e2e7ef;font-size:13px"
TH = ("padding:8px 10px;background:#1B2A4A;color:#ffffff;font-size:12px;"
      "text-align:left")


def send_mail(subject, html, to_addrs):
    import requests
    tok = requests.post(
        f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials",
              "client_id": os.environ["CLIENT_ID"],
              "client_secret": os.environ["CLIENT_SECRET"],
              "scope": "https://graph.microsoft.com/.default"},
        timeout=30)
    tok.raise_for_status()
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{SENDER}/sendMail",
        headers={"Authorization": f"Bearer {tok.json()['access_token']}"},
        json={"message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to_addrs],
        }},
        timeout=60)
    resp.raise_for_status()   # a swallowed send error is an invisible outage


def table(items):
    rows = ""
    for i, a in enumerate(items):
        days = a.get("daysLeft")
        when = ("" if days is None
                else f" (expired {-days} day{'s' if days != -1 else ''} ago)" if days < 0
                else f" ({days} day{'s' if days != 1 else ''} left)")
        rows += (
            f"<tr style=\"background:{'#f7f8fa' if i % 2 else '#ffffff'}\">"
            f"<td style=\"{TD}\"><b>{a['employee']}</b></td>"
            f"<td style=\"{TD}\">{a.get('badgeNumber') or '&mdash;'}</td>"
            f"<td style=\"{TD}\">{a.get('badgeType') or '&mdash;'}</td>"
            f"<td style=\"{TD}\">{a.get('expiration', '')}{when}</td>"
            f"<td style=\"{TD}\"><a href=\"{a['link']}\">Open badge &rarr;</a></td></tr>")
    return (
        f"<table style=\"border-collapse:collapse;font-family:Arial,sans-serif\">"
        f"<tr><th style=\"{TH}\">Employee</th><th style=\"{TH}\">Badge #</th>"
        f"<th style=\"{TH}\">Type</th><th style=\"{TH}\">Expiration</th>"
        f"<th style=\"{TH}\"></th></tr>{rows}</table>")


def body(intro, items, contacts):
    test_note = ""
    if OVERRIDE:
        test_note = (
            "<p style=\"color:#a05a00;font-size:12px;border:1px solid #f0c36d;"
            "background:#fdf1dc;padding:8px 10px\"><b>TEST MODE:</b> this alert "
            "would go to: " + (", ".join(contacts) or "(no contact on file)") +
            ". Recipients are overridden until the per-location distribution "
            "lists are set up.</p>")
    return (
        "<div style=\"font-family:Arial,sans-serif;font-size:14px;color:#0c1a2b\">"
        f"<p>{intro}</p>{test_note}{table(items)}"
        "<p>Open a badge link to start its renewal or close-out directly on the "
        "Training and Credentials page.</p>"
        "<p style=\"margin-top:16px;color:#888;font-size:12px\">&mdash; Foxtrot "
        "Aviation Services Badge Tracker</p></div>")


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    feed = data.get("alertsFeed", [])
    state = (json.loads(STATE_FILE.read_text(encoding="utf-8"))
             if STATE_FILE.exists() else {})
    sent = state.setdefault("sent", {})
    today = date.today()
    stamp = today.isoformat()
    outbox = []   # (subject, html, contacts, state_keys)

    def by_loc(items):
        g = {}
        for a in items:
            g.setdefault(a.get("location") or "(no location)", []).append(a)
        return sorted(g.items())

    # -- expiring: once per badge per expiration date, grouped per location
    fresh = [a for a in feed if a["kind"] == "expiring"
             and f"expiring:{a['badgeId']}:{a['expiration']}" not in sent]
    for loc, items in by_loc(fresh):
        n = len(items)
        outbox.append((
            f"[Badge Alert] {loc} — {n} badge{'s' if n != 1 else ''} expiring "
            f"within {data.get('alertSoonDays', 7)} days",
            body(f"The following badge{'s' if n != 1 else ''} at <b>{loc}</b> "
                 f"expire{'s' if n == 1 else ''} within "
                 f"{data.get('alertSoonDays', 7)} days:", items,
                 items[0].get("contacts", [])),
            items[0].get("contacts", []),
            [f"expiring:{a['badgeId']}:{a['expiration']}" for a in items]))

    # -- unreturned: once per badge, grouped per location
    fresh = [a for a in feed if a["kind"] == "unreturned"
             and f"unreturned:{a['badgeId']}" not in sent]
    for loc, items in by_loc(fresh):
        n = len(items)
        outbox.append((
            f"[Badge Alert] {loc} — {n} unreturned badge{'s' if n != 1 else ''} "
            "from terminated employees",
            body(f"The following badge{'s' if n != 1 else ''} belong to "
                 f"terminated employees and {'was' if n == 1 else 'were'} never "
                 "marked returned. Please collect and close out:", items,
                 items[0].get("contacts", [])),
            items[0].get("contacts", []),
            [f"unreturned:{a['badgeId']}" for a in items]))

    # -- expired: weekly per-location summary of the standing list
    last = state.get("expired_summary_last")
    due = (not last or
           (today - datetime.strptime(last, "%Y-%m-%d").date()).days >= WEEKLY_DAYS)
    expired = [a for a in feed if a["kind"] == "expired"]
    if due and expired:
        for loc, items in by_loc(expired):
            n = len(items)
            outbox.append((
                f"[Badge Weekly] {loc} — {n} expired badge{'s' if n != 1 else ''}",
                body(f"Weekly summary: badge{'s' if n != 1 else ''} at "
                     f"<b>{loc}</b> currently expired and awaiting renewal:",
                     items, items[0].get("contacts", [])),
                items[0].get("contacts", []),
                ["__weekly__"]))

    if not outbox:
        print("nothing to send (all alerts previously sent; weekly not due)")
        return 0

    can_send = bool(OVERRIDE) and not DRY_RUN
    for subject, html, contacts, keys in outbox:
        print(f"{'SEND' if can_send else 'WOULD SEND'}: {subject}"
              f"  -> {OVERRIDE if OVERRIDE else contacts or ['(no contact)']}")
        if not can_send:
            continue
        send_mail(subject, html, OVERRIDE)
        for k in keys:
            if k == "__weekly__":
                state["expired_summary_last"] = stamp
            else:
                sent[k] = stamp

    if can_send:
        STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
        print(f"state updated: {STATE_FILE.name}")
    elif not DRY_RUN:
        print("RECIPIENTS_OVERRIDE not set - refusing to send to real "
              "recipients by default; emails printed only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

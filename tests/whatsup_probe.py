#!/usr/bin/env python3

import sys
import requests
from bs4 import BeautifulSoup

URL = "https://minorplanetcenter.net/whatsup/index"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.6 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://minorplanetcenter.net/",
    "Connection": "keep-alive",
}

COLUMN_SCHEMA = [
    "designation",
    "mag",
    "solar_elong",
    "lunar_elong",

    "begin_time",
    "begin_ra",
    "begin_dec",
    "begin_alt",

    "max_time",
    "max_ra",
    "max_dec",
    "max_alt",

    "end_time",
    "end_ra",
    "end_dec",
    "end_alt",
]

def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)

def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    # ------------------------------------------------------------
    # 1. GET form page (session + CSRF)
    # ------------------------------------------------------------
    r = session.get(URL, timeout=15)
    print("GET status:", r.status_code)

    if r.status_code != 200:
        die("Initial GET blocked (403)")

    soup = BeautifulSoup(r.text, "html.parser")
    csrf_input = soup.find("input", {"name": "authenticity_token"})
    if not csrf_input:
        die("CSRF token not found")

    csrf = csrf_input["value"]
    print("CSRF token extracted")

    # ------------------------------------------------------------
    # 2. POST form
    # ------------------------------------------------------------
    payload = {
        "utf8": "✓",
        "authenticity_token": csrf,

        # Location
        "latitude": "42.4",
        "longitude": "-71.1",

        # Observing time (UTC)
        "year": "2025",
        "month": "12",
        "day": "30",
        "hour": "22",
        "minute": "28",
        "duration": "1",

        # Constraints
        "max_objects": "10",
        "min_alt": "30",
        "solar_elong": "45",
        "lunar_elong": "20",

        # Object type: mp | neo | cmt
        "object_type": "mp",

        "submit": "Submit",
    }

    r2 = session.post(URL, data=payload, timeout=30)
    print("POST status:", r2.status_code)

    if r2.status_code != 200:
        die("Form POST failed")

    print("Success — received response HTML")

    # ------------------------------------------------------------
    # 3. Parse results table
    # ------------------------------------------------------------
    soup2 = BeautifulSoup(r2.text, "html.parser")
    tables = soup2.find_all("table")

    if len(tables) < 2:
        die("Results table not found")

    results_table = tables[-1]
    rows = results_table.find_all("tr")

    objects = []

    for tr in rows:
        values = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(values) != len(COLUMN_SCHEMA):
            continue

        row = dict(zip(COLUMN_SCHEMA, values))

        obj = {
            "designation": row["designation"],
            "mag": float(row["mag"]),
            "solar_elong": float(row["solar_elong"]),
            "lunar_elong": float(row["lunar_elong"]),

            "begin": {
                "time": row["begin_time"],
                "ra": row["begin_ra"],
                "dec": row["begin_dec"],
                "alt": float(row["begin_alt"]),
            },
            "max": {
                "time": row["max_time"],
                "ra": row["max_ra"],
                "dec": row["max_dec"],
                "alt": float(row["max_alt"]),
            },
            "end": {
                "time": row["end_time"],
                "ra": row["end_ra"],
                "dec": row["end_dec"],
                "alt": float(row["end_alt"]),
            },
        }

        objects.append(obj)

    # ------------------------------------------------------------
    # 4. Output summary (ready for imaging logic)
    # ------------------------------------------------------------
    print(f"\nParsed {len(objects)} objects:\n")

    for o in sorted(objects, key=lambda x: x["max"]["alt"], reverse=True):
        print(
            f"{o['designation']:>6} | "
            f"V={o['mag']:4.1f} | "
            f"Alt(max)={o['max']['alt']:5.1f} | "
            f"RA={o['max']['ra']} | "
            f"Dec={o['max']['dec']}"
        )

if __name__ == "__main__":
    main()

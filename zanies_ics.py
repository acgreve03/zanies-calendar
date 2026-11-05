#!/usr/bin/env python3
"""Build an iCalendar feed of Zanies Nashville shows.

Usage: python zanies_ics.py [output.ics]
Needs Python 3.9+ and nothing else installed. (On Windows: pip install tzdata)
"""
import html
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOMS = ["Zanies", "The Lab"]  # remove a room to leave its shows out
MONTHS_AHEAD = 6               # this month plus the next 5
SHOW_LENGTH = timedelta(minutes=90)  # the site's end time is a placeholder

ENDPOINT = "https://nashville.zanies.com/wp-admin/admin-ajax.php"
CALENDAR_PAGE = "https://nashville.zanies.com/events/"
CENTRAL = ZoneInfo("America/Chicago")
ADDRESSES = {
    "Zanies": "Zanies, 2025 8th Ave S, Nashville, TN 37204",
    "The Lab": "The Lab at Zanies, 2019 8th Ave S, Nashville, TN 37204",
}


def month_ranges(today, count):
    year, month = today.year, today.month
    for _ in range(count):
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        yield date(year, month, 1).isoformat(), date(next_year, next_month, 1).isoformat()
        year, month = next_year, next_month


def fetch_month(start, end):
    data = urllib.parse.urlencode({
        "action": "loadEtixMonthViewEventPageFn",
        "start": start,
        "end": end,
        "venue_id": "0",
        "page_id": "14726",
    }).encode()
    request = urllib.request.Request(ENDPOINT, data=data, headers={
        "X-Requested-With": "XMLHttpRequest",
        "Referer": CALENDAR_PAGE,
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if not body.get("success") or not isinstance(body.get("data", {}).get("events"), list):
        raise ValueError(f"Unexpected response from Zanies for {start}")
    return body["data"]["events"]


def clean(text):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", text or "")).split())


def find_link(raw):
    if re.match(r"https?:", raw.get("url") or "", re.I):
        return raw["url"]
    markup = (raw.get("strctaHtml") or "") + " " + (raw.get("strTitleHtml") or "")
    match = re.search(r"""href\s*=\s*["']?(https?:[^"'\s>]+)""", markup, re.I)
    return match.group(1) if match else CALENDAR_PAGE


def to_show(raw):
    if not raw.get("start") or not raw.get("plaintitle"):
        return None
    name = clean(raw["plaintitle"])
    room = clean(raw.get("venuename"))
    start = datetime.fromisoformat(raw["start"]).replace(tzinfo=CENTRAL)
    return {
        "key": f"{raw.get('similartitle') or name}|{raw['start']}",
        "room": room,
        "title": name if room == "Zanies" else f"{name} ({room})",
        "start": start,
        "end": start + SHOW_LENGTH,
        "location": ADDRESSES.get(room, room),
        "description": f"Room: {room}\nTickets and details: {find_link(raw)}\n"
                       "End time is an estimate. Check door times on the Zanies site.",
    }


def escape(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def fold(line):
    """Split a line into pieces of at most 75 bytes, as the iCalendar format requires."""
    pieces, current, size = [], "", 0
    for char in line:
        width = len(char.encode())
        if size + width > 75:
            pieces.append(current)
            current, size = " ", 1
        current += char
        size += width
    pieces.append(current)
    return "\r\n".join(pieces)


def utc_stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(shows):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//zanies-sync//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Zanies Nashville",
        "X-WR-TIMEZONE:America/Chicago",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
    ]
    for show in sorted(shows, key=lambda s: (s["start"], s["key"])):
        uid = re.sub(r"[^A-Za-z0-9-]", "-", show["key"])
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@zanies-sync",
            # Tied to the show, so the file only changes when the shows do.
            f"DTSTAMP:{utc_stamp(show['start'])}",
            f"DTSTART:{utc_stamp(show['start'])}",
            f"DTEND:{utc_stamp(show['end'])}",
            f"SUMMARY:{escape(show['title'])}",
            f"LOCATION:{escape(show['location'])}",
            f"DESCRIPTION:{escape(show['description'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def main():
    output = sys.argv[1] if len(sys.argv) > 1 else "zanies.ics"
    today = datetime.now(CENTRAL).date()
    shows = {}
    for start, end in month_ranges(today, MONTHS_AHEAD):
        for raw in fetch_month(start, end):
            show = to_show(raw)
            if show and show["room"] in ROOMS:
                shows[show["key"]] = show
    if not shows:
        raise SystemExit("Zanies returned no shows, so the existing feed was left unchanged.")

    # Replace the file only once everything has succeeded.
    folder = os.path.dirname(os.path.abspath(output))
    with tempfile.NamedTemporaryFile("w", dir=folder, delete=False, newline="", encoding="utf-8") as tmp:
        tmp.write(build_ics(shows.values()))
    os.chmod(tmp.name, 0o644)
    os.replace(tmp.name, output)
    print(f"Wrote {len(shows)} shows to {output}")


if __name__ == "__main__":
    main()
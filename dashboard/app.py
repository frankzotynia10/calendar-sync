import os
import datetime

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, render_template
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]

PORT = int(os.environ.get("PORT", "8080"))

# How many days forward/back of events to serve to the dashboard
EVENTS_PAST_DAYS = int(os.environ.get("EVENTS_PAST_DAYS", "1"))
EVENTS_FUTURE_DAYS = int(os.environ.get("EVENTS_FUTURE_DAYS", "14"))

# Safety-net TTL: if a LiveTrack session's started_at is older than this,
# the dashboard treats it as inactive even if the DB flag hasn't been flipped yet.
LIVETRACK_TTL_HOURS = int(os.environ.get("LIVETRACK_TTL_HOURS", "5"))

# CALENDAR_META format: name1=Label One|#color1,name2=Label Two|#color2,...
# Display label + dot color per source_cal, kept out of the frontend so
# renaming/recoloring a feed is a config change, not a code change.
CALENDAR_META_RAW = os.environ.get("CALENDAR_META", "")

DEFAULT_META_COLOR = "#999999"
DEFAULT_META_LABEL = "Other"


def parse_calendar_meta(raw):
    meta = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, rest = entry.split("=", 1)
        label, color = rest.split("|", 1)
        meta[name.strip()] = {"label": label.strip(), "color": color.strip()}
    return meta


CALENDAR_META = parse_calendar_meta(CALENDAR_META_RAW)

app = Flask(__name__)


def get_conn():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/events")
def api_events():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                select source_cal, title, start_time, end_time, all_day, location
                from calendar.calendar_events
                where start_time >= now() - (%s || ' days')::interval
                  and start_time <= now() + (%s || ' days')::interval
                order by start_time asc
                """,
                (EVENTS_PAST_DAYS, EVENTS_FUTURE_DAYS),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    events = []
    for r in rows:
        events.append(
            {
                "source_cal": r["source_cal"],
                "title": r["title"],
                "start_time": r["start_time"].isoformat() if r["start_time"] else None,
                "end_time": r["end_time"].isoformat() if r["end_time"] else None,
                "all_day": r["all_day"],
                "location": r["location"],
            }
        )

    return jsonify({"events": events})


@app.route("/api/calendar-meta")
def api_calendar_meta():
    return jsonify(
        {
            "sources": CALENDAR_META,
            "default": {"label": DEFAULT_META_LABEL, "color": DEFAULT_META_COLOR},
        }
    )


@app.route("/api/livetrack")
def api_livetrack():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "select active, url, started_at from calendar.live_track_state where id = 1"
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return jsonify({"active": False, "url": None})

    active = row["active"]
    started_at = row["started_at"]

    # Client-side-facing TTL safety net, computed server-side so the
    # frontend never has to reimplement the cutoff logic.
    if active and started_at is not None:
        age = datetime.datetime.now(datetime.timezone.utc) - started_at
        if age > datetime.timedelta(hours=LIVETRACK_TTL_HOURS):
            active = False

    return jsonify({"active": active, "url": row["url"] if active else None})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)

# Calendar Sync + Home Dashboard

Two services in this repo:

- **`sync/`** — pulls events from published ICS feeds (O365, Google, iCloud) into PostgreSQL
- **`dashboard/`** — Flask backend + wall-display frontend that reads that data, shows a live time-grid calendar, and auto-switches to a Garmin LiveTrack map during rides

Deployed on Andromeda, viewed on an iPad (Mini 2 currently, Air 1 planned) mounted on a wall via Add to Home Screen (Guided Access recommended for true kiosk lockdown).

---

## `sync/` — Calendar Sync Service

### What it syncs

| Table | Contents |
|-------|----------|
| `calendar.calendar_events` | Normalized events: title, start/end, all-day flag, location, source calendar |

Recurring events are expanded into individual occurrences within a rolling window (past 7 days / future 90 days by default) rather than stored as raw RRULEs. All writes upsert on `(source_cal, event_uid)` — safe to re-run. Recurring instances get a `_<epoch>` suffix appended to their UID since ICS reuses one UID across all occurrences of a series.

### Configuration

```env
CALENDAR_FEEDS="fz_o365=https://outlook.office365.com/.../calendar.ics,
cz_o365=https://outlook.office365.com/.../calendar.ics,
fz_google=https://calendar.google.com/.../basic.ics,
icloud_frank=https://p132-caldav.icloud.com/published/2/....ics"

DB_HOST=10.10.0.10
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_password

WINDOW_PAST_DAYS=7
WINDOW_FUTURE_DAYS=90
```

`CALENDAR_FEEDS` is a comma-separated list of `name=url` pairs (multi-line-in-quotes works fine for readability). Add or remove feeds here — no code changes needed.

**iCloud feeds:** the "Public Calendar" link iCloud gives you starts with `webcal://` — swap that prefix for `https://` and it's a normal ICS URL.

**Current feeds:** `fz_o365`, `cz_o365`, `fz_google`, `icloud_frank`. Wife's Google and second iCloud calendar still to be added.

### Schedule

Runs via n8n workflow **"Calendar Sync Schedule"** every 20 minutes (`docker exec calendar-sync python calendar_sync.py` over SSH). No webhook option for O365/Google/iCloud ICS publish links, so polling is the only option.

---

## `dashboard/` — Wall Display

### Backend (`app.py`)

Flask app serving the frontend template plus three JSON endpoints:

| Endpoint | Purpose |
|----------|---------|
| `/api/events` | Events in the configured date window (`EVENTS_PAST_DAYS`/`EVENTS_FUTURE_DAYS`) |
| `/api/calendar-meta` | Display label + color per `source_cal`, driven by `CALENDAR_META` env var |
| `/api/livetrack` | Current LiveTrack state (`active`, `url`), with a server-side TTL safety check |

Connects to Postgres as `dashboard_user` (read-only role scoped to the `calendar` schema).

### Configuration

```env
DB_HOST=10.10.0.10
DB_PORT=5432
DB_NAME=postgres
DB_USER=dashboard_user
DB_PASSWORD=your_password

PORT=8080

EVENTS_PAST_DAYS=1
EVENTS_FUTURE_DAYS=14

LIVETRACK_TTL_HOURS=5

# Display label + dot color per source_cal (must match calendar-sync's CALENDAR_FEEDS keys)
CALENDAR_META="fz_o365=Frank - Work|#4a90d9,
cz_o365=Christina - Work|#d94a90,
fz_google=Frank - Personal|#4ad97a,
icloud_frank=Frank - iCloud|#c9a24a"
```

### Frontend (`templates/index.html`)

Single-file HTML/CSS/JS, deliberately written in conservative ES5-style JavaScript (no optional chaining, no `formatToParts`, no arrow-function class fields) for compatibility with **Safari 12** on older iPads (Mini 2 / Air 1, both capped at iOS 12.5.7).

**Three responsive tiers**, driven by `getDaysToShow()` in JS (not fixed CSS breakpoints alone):

| Viewport | View | Days shown |
|----------|------|------------|
| `< 768px` (phone) | Agenda list | 7-day rolling window, stacked by day |
| `768px – 1599px` (iPad, both orientations) | Time grid | 3 days |
| `≥ 1600px` (desktop) | Time grid | 7 days |

The desktop threshold is set well above 1536px (iPad Air 1's resolution) and 1024px (iPad Mini/Air landscape CSS width) so no iPad orientation ever gets misclassified as "desktop."

**Time grid features:**
- Half-hour gridlines, 6 AM–11 PM, auto-scrolls to ~7 AM on first load
- Events positioned by actual start/end time; overlapping events split into side-by-side columns automatically
- All-day events shown in a separate strip above the timed grid
- Red "now" line on today's column, updates every minute
- **"Up next" highlight** — the single soonest upcoming timed event gets a white glow (grid) or bold highlight (agenda), recalculated on every data refresh
- Sticky header (day names) and sticky legend row — stay visible while scrolling
- Calendar colors/labels come from `/api/calendar-meta`, not hardcoded — add a feed, add a `CALENDAR_META` entry, done

**LiveTrack integration:** polls `/api/livetrack` every 30s. When active, swaps the whole view to an iframe of the LiveTrack URL; reverts to the calendar automatically when inactive.

**Overnight screensaver:** active 9:00 PM–5:30 AM Eastern only. After 5 minutes with no tap/click/keypress during that window, fades to a dim clock; any interaction wakes it instantly. Never triggers while LiveTrack is active. Toggleable in the in-page settings panel.

**Settings panel** (gear icon, top-right): toggles for night screensaver on/off and light/dark theme. Persisted via `localStorage` (this is a plain deployed webpage, not a sandboxed Artifact, so `localStorage` is fine here) — per-device preference, no backend involved.

**iOS home-screen install:** the page includes `apple-mobile-web-app-capable` and related meta tags so launching from Add to Home Screen hides Safari's address bar/chrome. **Important:** these only take effect on icons created *after* the tags were added — if you already have a home screen icon from before, delete it and re-add it (Share → Add to Home Screen) for the fullscreen behavior to kick in.

### Files

| File | Purpose |
|------|---------|
| `app.py` | Flask backend, DB queries, LiveTrack TTL logic |
| `templates/index.html` | Full frontend (grid/agenda views, screensaver, settings) |
| `Dockerfile` | Container definition |
| `requirements.txt` | Python deps |
| `calendar.png` | Favicon / apple-touch-icon source, referenced directly via raw GitHub URL |

---

## LiveTrack automation (n8n)

Full lifecycle, spread across two n8n workflows plus a branch on the existing Strava webhook handler:

| Workflow | Trigger | Action |
|----------|---------|--------|
| **LiveTrack Start** | IMAP on a dedicated Gmail inbox (added as a LiveTrack recipient in Garmin Connect) | Extracts the LiveTrack URL from the email, sets `active=true` in `calendar.live_track_state` |
| **LiveTrack Manual End** | Webhook (`/webhook/livetrack-end`), fired from an iOS Shortcut | Immediately sets `active=false` — no delay |
| *(branch on)* **Strava Webhook Handler** | New Strava activity created | Waits 20 minutes, then sets `active=false` — the primary "ride's over" signal, with a buffer so the display doesn't cut away the instant you stop |
| **LiveTrack TTL Sweep** | Every 15 minutes | Force-closes anything still `active=true` after `LIVETRACK_TTL_HOURS` (5h) — safety net in case the above never fire |

`calendar.live_track_state` is a single-row table (`id=1` constrained) holding `active`, `url`, `started_at`.

---

## Schema

Lives in its own `calendar` schema in the same Postgres instance as the fitness data — kept separate from `hevy_*`/`garmin_*`/`strava_*` in `public` so the fitness MCP read-only layer never surfaces family calendar data.

```sql
create schema if not exists calendar;

create table if not exists calendar.calendar_events (
    id bigserial primary key,
    source_cal text not null,
    event_uid text not null,
    title text,
    start_time timestamptz not null,
    end_time timestamptz,
    all_day boolean default false,
    location text,
    raw_ics text,
    last_synced timestamptz default now(),
    unique (source_cal, event_uid)
);

create index if not exists idx_calendar_events_start on calendar.calendar_events (start_time);
create index if not exists idx_calendar_events_source on calendar.calendar_events (source_cal);

create table if not exists calendar.live_track_state (
    id smallint primary key default 1,
    active boolean default false,
    url text,
    started_at timestamptz,
    check (id = 1)
);

insert into calendar.live_track_state (id, active, url, started_at)
values (1, false, null, null)
on conflict (id) do nothing;

-- Grant read access to whichever roles need it:
grant usage on schema calendar to dashboard_user;
grant select on all tables in schema calendar to dashboard_user;
grant usage on schema calendar to claude_reader;
grant select on all tables in schema calendar to claude_reader;
```

---

## CI/CD

GitHub Actions builds and pushes both images to GHCR on every push to `main` (`sync/` and `dashboard/` have separate build contexts so neither image picks up the other's files), then fires an n8n deploy webhook.

```yaml
docker_compose/calendar-sync-compose.yml
```
defines both containers on Andromeda: `calendar-sync` (idle, triggered by n8n cron) and `dashboard` (long-running, port 8080).

---

## Git safety

Do not commit: `.env`, ICS URLs, LiveTrack email credentials — all unauthenticated or sensitive links/secrets.

---

## Roadmap / open items

- [ ] Add wife's Google calendar + second iCloud feed
- [ ] iPad Air 1 as second/replacement display
- [ ] Confirm LiveTrack end-to-end (Garmin Edge session → dashboard auto-popup → auto-close) in a real ride

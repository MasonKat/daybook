# DECISIONS

One line each. "deferred" = not built in P0, on purpose.

## Process
- 2026-11-03: No `daybook` repo was attached to the cloud session and attaching one was refused, so the build lives in a local git repo; the push needs the teacher (see HANDOFF.md, "Push this repo").
- The build ran on 11-03 (one day early). P1, P2 and most of P3 are done; the dates in this file are build dates.
- Python ≥ 3.11 (tested on 3.12 and 3.13), not pinned to 3.12: nothing in the code needs 3.12.

## Data model
- `classes.term` holds `START..END` (ISO dates); it defines the scheduled class days and `remaining_sessions`.
- `classes.weekday_mask`: Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64; `--days 화,금` or `tue,fri`.
- A class day = a `sessions` row OR a scheduled weekday inside the term (so the Friday pilot needs no `session start`).
- `submissions` has no set column (exact schema): the set id is kept in the Worker record and the `submission` event payload.
- One submission row per student per day (`UNIQUE(student_id, day)`); a phone upload after a teacher/paper mark turns it into `phone`.
- One attitude row per student per session; a second tap corrects it (the `events` log keeps every tap).
- `said_aloud` = 1 for board taps, 0 for `daybook score`.
- `events` is append-only, enforced by SQLite triggers; the last pull position is the latest `pull` event (no extra table).
- Extra indexes and a `PRAGMA user_version` = 1 for future migrations.
- Slug = the display name, de-duplicated with a numeric suffix; editable with `roster edit --slug`.
- Times are stored as UTC ISO; "day" is computed with a fixed UTC+9 (Korea has no DST), no tz database needed.

## Derived values
- Streak: today without a submission is still open (neither counts nor breaks); a past class day without one breaks.
- The student page computes the streak for days after the publish date in the browser (Worker `/status` days + schedule), on top of the streak through the day before publish.
- Reward goal = active × remaining sessions × `goal_coef`, rounded up, computed at the first publish and frozen into `reward_goal_points` (a goal that shrank every week would never fill); `daybook class edit <c> --goal 0` recomputes.
- `class_total` = all attitude points of the class's active students over the whole term. Multiple reward cycles per term: deferred.
- "반 누적 +N" on the 오늘 완료 screen = the class total as of the last publish (the Worker never knows classes).
- Trend: least-squares slope per round over the last 6 rounds; thresholds 0.01 (efficiency) and 0.03 (reach); the SVG y-axis is 0–100 % and assumes both values are 0..1 (clamped).
- Class median band on the trend: deferred ("median band deferred").

## Importers
- Screeners: match `student` → `ext_id`, else `name` → `full_name`, else `student` → `full_name` (the field may hold a name); a name shared by two students is reported as ambiguous, never guessed.
- Unmatched rows are printed and counted in the `import_screeners` event; the import still exits 0 so the rest is kept.
- Re-imports upsert on (student, test_name, date) and (ext_id | unambiguous full_name): no duplicates, tokens kept.
- Roster rows not in the CSV are left as they are (never deleted); `Status` values other than `active_values` → inactive.
- A display name equal to the full name (2- or 4-character names) is allowed at import with a warning, and publish refuses it until `roster edit --display`.

## Publish and pages
- Publish renders into `.dist-staging/`, lints it, and swaps it into `dist/` only if clean; a failed publish leaves `dist/` and the DB unchanged.
- Lint errors: phone pattern, any `full_name`, `ext_id` (word-bounded), `parent_phone` digits, non-generic `<title>`/`og:`, third-party resources, files > 100 KB. Another student's display name on a per-student page is a warning only (short given names collide with ordinary words).
- `dist/_headers` sets `X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer` and a CSP (`connect-src` = self + the Worker); `robots.txt` disallows all.
- The parent page has no JavaScript and no links; the student page loads one same-origin script.
- `videos.json` is inlined into the student page at publish (no runtime fetch); only http(s) URLs are kept; newest first per group.
- Each student page has its own `manifest.webmanifest` (`start_url ./#today`) so the home-screen icon opens that student's page; there is no shared `static/manifest.webmanifest` and no service worker (offline: deferred).
- Student JSON data is embedded as `<script type="application/json">` with `<`, `>`, `&` escaped.
- The demo uses its own `demo.sqlite` and refuses to seed a database that has non-demo students.

## Worker
- Added `GET /status?t=` (the token's days in the last 8 days) so a reopened 오늘 tab shows "done" and the streak is right between publishes.
- Records (token, day, ts, key; no photo) live 14 days (`RECORD_TTL_DAYS`), photos 7 days (`PHOTO_TTL_DAYS`): a weekly pull must not miss a 7-day-old record.
- The record is also stored as KV metadata so `/submissions` needs only `list` calls, no per-key reads.
- CORS: a browser request from another origin to `/submit` or `/status` gets 403; requests without `Origin` (curl, console) pass; teacher endpoints need `X-Teacher-Token` (constant-time compare).
- Uppercase tokens are lower-cased before validation; `set` is restricted to `[A-Za-z0-9_-]{0,80}`.
- The KV rate-limit counter is approximate (KV is eventually consistent); good enough for 10/hour.
- If the browser cannot decode the photo (e.g. HEIC on desktop Chrome), the original is sent if ≤ 8 MB.
- Pull reads since the previous Worker `now` − 5 minutes (KV list is eventually consistent); upserts are idempotent.
- Old student tokens from `roster rotate-tokens` still map to their student on pull (kept in the event log).
- Free-plan budget: each upload = 3 KV writes (1,000/day free); the board polls the Worker with `list` (1,000/day free). Default `board_poll_seconds` = 30 (2026-11-03, local agent's review): about 180 list calls per 90-minute class instead of 540 at 10 s. The board page still refreshes from the laptop every 10 s; `board_poll_seconds` in daybook.json overrides.

## Console and board
- Extra commands beyond the brief: `class add|edit|list` (roster commands need a class), `roster edit`, `roster list --links` (to send links), `submit` (paper/teacher mark), `pull --photos`.
- The board creates today's session when it starts; it is bound to 127.0.0.1 and refuses cross-origin POSTs.
- Playwright checks run in Node (`tests/e2e/check.cjs`, same runtime as the Worker tests); `make` installs Playwright only if Node cannot already resolve it.
- Config lives in `daybook.json` (gitignored) or `DAYBOOK_*` environment variables; the teacher token never enters the repo.

## Deferred
- deferred (local agent will add after the clone, before 수능 week): `daybook class cancel <class> <date>` so a closed day does not break streaks.
- deferred: `export pdf` (the command is a stub that exits 2 with a message).
- deferred: class median band on the parent trend.
- deferred: cancelling a scheduled class day (holidays); a cancelled weekday still counts as a class day for the streak.
- deferred: several reward cycles per term (the class total counts the whole term).
- deferred: QR image generation; `session start --set` prints the `/go/?set=` URL for the teacher's existing QR tool.
- deferred: service worker / offline mode.
- deferred: a real-device install test on iPhone Safari and Android Chrome (manifest, icons, apple meta tags and standalone mode are in place; only Chromium was tested here).
- deferred: the Cloudflare deploy itself (needs the teacher's account: README "Teacher deploy steps").
- deferred: the `effort` table is created empty (P2 data source not defined yet).
- out of scope: login, database server, chat, push notifications, app store, analytics; nothing talks to Notion, Google or make.com.
- not in P0: retiring Google Forms / make.com (the teacher decides later).

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

## Design pass v1 (2026-11-03, from DESIGN_PROMPT_cloudCC_20261104)
- Interaction patterns only: no third-party logo, name, trademark colour or copied screen; the lint now fails on `kakao`, `instagram` or `#FEE500` (case-insensitive, Korean words such as 카카오톡 are allowed).
- Student theme: dark by default, light when the phone asks for light (`prefers-color-scheme: light`); the reels feed and the story viewer are always dark.
- Tabs moved to a bottom nav (영상 · 오늘 · 내 기록) with our own inline SVG icons; the stories row shows on 영상 and 오늘, not on 내 기록.
- Stories: 새 영상 (videos added in the last 7 days; hidden when none), 연속, 반 목표 (class total only), 선생님 (latest note; hidden when none). The ring is grey once seen; the seen key is a hash of the story's content, so new content colours it again.
- Story slides auto-advance every 5 s (none with reduced motion); tap right/left, swipe down, × or Escape; after the last slide the next story opens.
- 반 목표 story: "다음 단계까지 N점" = the next milestone (or the goal) above the class total; "목표 달성!" once reached.
- Reels: the YouTube iframe is created only on tap, uses `youtube-nocookie.com`, `referrerpolicy="strict-origin"` (YouTube refuses embeds without a referrer; the origin carries no token) and `autoplay=1` (the tap is the user's action). Opening another reel or leaving the tab removes the iframe. CSP gains `frame-src https://www.youtube-nocookie.com`.
- A reel without `yt_id` keeps ▶ as a link to `url`; a reel without `thumb` shows a gradient card with the title.
- Thumbnails need Pillow (new Python dependency): shrunk to 480 px wide, JPEG q70, stepping quality down until ≤ 60 KB; the lint enforces 60 KB for `dist/t/`.
- Demo videos are written to `demo/videos.demo.json` (gitignored) with dates relative to today, so the 새 영상 story is never empty in the demo; demo thumbnails are abstract synthetic images in `demo/thumbs/`.
- 오늘 is two steps: choose a photo → preview with 올리기 / 다시 찍기 → upload. The success screen dropped the reward line (it lives in the 반 목표 story).
- 내 기록 profile: counters 연속 · 제출 (submission days so far) · 누적 점수; one tile per day from the term start (at most 180), newest first, 3 per row; tiles are coloured by status (submitted / class day missed / no class / today open), ♥ = 3 points that day; never a photo.
- Teacher messages on 내 기록 = every note, oldest first (newest at the bottom), dated by the note's week; no input box.
- Parent room: one group of report cards per week from the term start, last 8 weeks; the 2 most recent are open, older ones sit in "이전 기록" (`<details>`). Past weeks are dated on their Saturday (the publish day) and carry no time; the current week shows the publish time.
- Past weeks' cards are computed as of that week's end: submissions, trend (rounds up to then), week sum, cumulative and the class bar (class total then, against today's frozen goal).
- "이번 주 {k}/{n}회": k = submission days, n = days of that week up to the publish day.
- The parent page now loads one script (scroll to the bottom on open); it still has no links and no input.
- The room footer reads "이 방은 주간 기록 전용입니다 · 답장·문의는 카카오톡으로" (both strings from §5).
- The e2e audit renders every page at 360×740 and 390×844 in light and dark; overflow is measured on scroll containers too, not only the document.

## Pipeline fixes from the v2 review (2026-11-03)
- Windows: every text file read/write names UTF-8 (`scripts/check_encoding.py`, enforced by pytest); the CLI writes UTF-8 to the console and recovers undecoded POSIX arguments; a test runs the CLI under an ASCII locale with UTF-8 mode off (stricter than cp1252). `scripts/demo.ps1`, `check.ps1`, `deploy-pages.ps1` are the twins of the make targets; they were written without a PowerShell to run them here, so the local agent runs them first.
- Raw ids: a student page never contains a set/round id. It carries `{fnv1a(set_id): label}` and maps `?set=` in the browser; label = `assignments.json` (optional, gitignored; example in `mappings/`), else the session topic, else "{M/D} 과제" of the session date, else today's date.
- Lint `raw_id` (student and parent pages): `Adaptive_`, `round_`, `_G1`..`_G12`, `.json`. A note that quotes a round id now stops publish.

## Design pass v2 (2026-11-03)
- `videos.json` gains `kind` ("short" | "long", default long) and `duration`. 영상 = 쇼츠 (9:16 full-screen snap reels, shorts only) and 강의 (16:9 cards, 2+ per 844 px screen) as sub-tabs; with no shorts only 강의 shows. Short thumbnails are shrunk to 360 px wide, long to 480 px.
- Story rings hold content: the newest video's thumbnail, the streak number inside a ring filled by this week's submission days / 7 (conic gradient), a goal donut (% of the class goal), an "AMP" monogram for the teacher. Seen = grey.
- Profile tiles: submitted = 16 % tint of the gradient, a 3-point day = full gradient + ♥ (even without a submission), missed class day = thin rose outline + dot, no class = faint grey, today = accent outline. Month headers in the grid; month highlight rings (count of submitted days) jump to the month. Bio line "{class} · {n}번째 기록" (n = submission days).
- Done screen adds "오늘의 한 장 · M/D 기록됨 HH:MM" (time from the upload response, kept per device; from the published data when published the same day). No photo.
- Parent room: one report message per week (title "주간 리포트 · M월 N주 (range)", where N = which Thursday of the month); rows 제출 (7-day strip with ✓ + time; "제출 k일 (수업일 c일 중 d일)"), 주간 테스트 (latest 확보 효율 / 도전 도달 % + direction words; the chart only in the newest week), 태도 ("이번 주 +w · 누적 c점"; the class bar only in the newest week). The teacher's line is a separate bubble. Earlier weeks are one summary line each in a closed `<details>`; the summary uses the efficiency direction as "테스트 …".
- Each past week is dated and timed by the last successful publish inside that week (from the `events` log); without one it is dated on its Saturday with no time.
- Trend chart: the first date label is left-anchored and the last right-anchored, so no label is clipped (checked in e2e with getBBox).
- Contrast: light muted text #5b6472 (≥ 5.2:1 on every light background), green/amber direction words darkened in light mode; all dark-mode muted text ≥ 6:1.
- Screenshots are taken at 390×844; the audit still renders every page at 360 and 390 px, light and dark.

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

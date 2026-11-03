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

## F1: demo student 콩 and the taste build (2026-11-03)
- 콩 (full name 강콩, S913, slug 콩) joins 고1A in the demo: submissions every day from 22 days ago to yesterday except one no-class day (streak 21), today left open for the visitor, 3 points on the last five sessions (five ♥), 2 on the others, three notes in the last three weeks, rising screener rows (appended to the committed rounds; the other students' rows are unchanged).
- The demo now has 13 students (12 + 콩); the e2e expects 13.
- `daybook publish --taste` renders only the student with slug 콩 from the demo database (refuses one without the `demo_seed` event), at `/s/kong/` and `/p/kong/`, a landing page instead of `/go/`, the banner on every page, no Worker URL (CSP `connect-src 'self'`), and logs `publish_taste` (not `publish`, so it never feeds the parent room's publish times).
- Taste lint on top of the normal one: no Worker URL or Worker endpoint string in HTML/JSON, no other demo student's display name.
- In the taste build the upload is simulated in the browser (500 ms, no shrink, no network); 출석 and reactions will be simulated the same way when F2–F4 land.
- `make deploy-taste` / `scripts\deploy-taste.ps1` reseed `demo.sqlite` with `demo --force --no-publish` (so the real `dist/` is untouched) and deploy `dist-taste/` to the separate Pages project `daybook-taste`; the teacher runs them.
- The taste banner is amber (#f59e0b), not a messenger brand colour.

## F2: 출석 (2026-11-03)
- Schema v2 (additive, migrated automatically on the next command): `checkins (id, student_id, session_id, ts, source phone|teacher, UNIQUE(student_id, session_id))`; `sessions.start_time` (HH:MM) and `sessions.code` (4 random digits, `secrets`). `PRAGMA user_version` = 2.
- `daybook session start <class> [--at HH:MM]` (default now) keeps an existing code on re-runs (`--new-code` draws again) and PUTs `/session` with the class key, date, code, window (30 min before → 60 min after) and the token → class-key map of all active students. The Worker never sees a class name: the class key is fnv1a("class:<id>").
- The board starts the session the same way (code kept), shows 오늘의 코드 in huge digits, the arrivals (initial + time) and a "출석 처리" button per student; `daybook checkin <slug>` is the CLI twin. Both write source `teacher`.
- Worker: `PUT /session` (teacher), `POST /checkin` (Pages origin; t + code as form data), `GET /checkins?since=` (teacher), `/status` adds `checkin_ts`, `class_today`, `start`. The tokmap is one KV key. A refused check-in (wrong code, closed window, no class today, 5 wrong codes per token per day) answers 200 with `ok: false`, so the student's browser console stays clean; malformed input still gets 4xx.
- After a successful check-in, any further check-in that day is idempotent (keeps the first time), even with a wrong code.
- `daybook pull` also reads `/checkins` (same `since`) and creates the session row if needed.
- Student 오늘 tab: on a class day (the published schedule or sessions, or `/status class_today`) the ritual is ① 출석 (4-digit pad, submits on the 4th digit) → ② 과제 끝 · 찍고 가자 (the photo). On other days the tab is "오늘 공부 인증" (the photo only). Step ② 오늘의 장면 arrives with F4; until then the steps are numbered 1–2.
- Parent weekly report: one line per class day so far, "화 출석 18:02 · 끝 20:55" (– when missing); "지각" only with `"show_late": true` in daybook.json and a check-in after the start time. No points depend on time.
- The deployed Worker must be redeployed (`cd worker && npx wrangler deploy`) for 출석; an older Worker keeps uploads working and `pull` skips check-ins.

## F3: 과제 끝 with 오늘 한 줄 (2026-11-03)
- Schema v3 (additive): `submissions.reflection` (0–40 characters) and `submissions.chip` (`ok` 이해됐어요 · `confused` 아직 헷갈려요 · `rewatch` 다시 볼 영상 있어요).
- The preview step has an optional text field (maxlength 40) and three toggle chips (one at a time). The page sends `line`/`chip` only when filled; the Worker keeps the earlier ones on a re-upload without them, trims control characters and caps 40 characters; unknown chips become empty.
- Who sees it: the teacher on the board row and with `daybook lines <class> [--date] [--days N]`; the student on 내 기록 ("내 한 줄", newest first, at most 30); parents only with `"parent_shows_reflection": true` in daybook.json (a quoted line under that day's 출석/끝 row).
- Student-written text never blocks the teacher's publish: `lint.text_scrubber` (console code, may read the private columns) drops a line that holds a phone number, any full name / ext_id / parent phone, a raw id or a brand word; the chip still shows. The original stays in the local database for the teacher.
- The done screen is the day summary: "출석 18:02 → 끝 20:55" on a class day ("끝 20:55" otherwise), 오늘의 한 장, 연속, 반 누적. "장면" joins it with F4.
- Demo: sessions start at 18:00 with a code; students who usually come get check-ins at 17:40–18:09 (some by 출석 처리); 콩 has a check-in for every past class day and six reviews.

## F4: 오늘의 장면 (2026-11-03)
- Schema v4: `moments (id, class_id, session_id, ts, text, image_key)`, at most 3 per session; images live in `moments_dir` (default `moments/`, gitignored), never in git.
- On the laptop, before anything is stored or sent: `ImageOps.exif_transpose` then a fresh RGB copy (all EXIF/GPS/ICC dropped), long edge ≤ 1080 px, JPEG quality stepped down to ≤ 200 KB. The gate "학생 얼굴·이름·문제집 원문 전체가 보이지 않나요? (y/N)" defaults to N; only y stores (CLI prompt; a checkbox on the board that the server also requires).
- Same day: the console PUTs the JPEG (base64) to `/moment-put`, stored as `moment:<class key>:<date>:<n>` with a 14-day TTL. A student token reads only its own class (`GET /moments?t=` → metadata + counts + its own reaction; `GET /moment?t=&k=` → the JPEG, shown through a blob URL so the CSP stays `img-src 'self' blob: data:`). The page asks only after `load`.
- Saturday publish bakes the moments as 720 px ≤ 100 KB copies into `dist/m/<class key>/<date>_<n>.jpg` (the class key, never the name). The student story shows the moments of the last 7 days; the parent report shows up to 3 per week ("이번 주 수업 장면"), without reaction counts.
- Reactions 🔥 👏 🤯: anonymous class counters (`POST /react`), one per token per moment, the first tap counts; students see the totals; the board shows the latest totals ("지난 장면 반응"). No names, no comments, no ranking.
- The ritual is ① 출석 → ② 장면 → ③ 과제 끝 on class days. Step ② is done when the student opened today's moment (✓) or reacted (the emoji); without a moment today it shows "–" and never blocks. The day summary reads "출석 18:02 → 장면 🔥 → 끝 20:55". In the taste build step ② uses the newest baked moment and reactions stay in the browser.
- The board uploads a file (on a laptop that is a file or the camera app's photo); a live webcam capture is deferred.
- `/moments` for a token whose class is unknown answers an empty list (not 404), so the student console stays clean.
- Demo: two synthetic chalkboard scenes on each class's latest class day and one on the class day before (with synthetic EXIF, so the stripping runs in every demo).

## F5: the proposal's leftovers (2026-11-03)
- `daybook class cancel <class> <date> [--reason 휴강] [--undo]`: schema v5 adds `sessions.cancelled` (the reason). A cancelled day is removed from the class days (streak, ritual, parent "수업일" counts) and from `remaining_sessions` for a goal that is not frozen yet (a frozen goal stays as it is). The profile shows it as a grey hatched tile with the reason.
- QR: `daybook qr <class> --set <id> [--out f.svg|f.png]` and `daybook qr sheet <class>` (A4 HTML, one card per set id of the class, labels from assignments.json, else "M/D 과제") with `segno` (new pure-Python dependency). The URL is always `{pages_url}/go/?set=<id>`; the code refuses anything that is not a plain set id. Output in `qr/` (gitignored).
- Class band on the parent trend: the class's middle 50 % (p25–p75) per round, for 확보 효율 and 도전 도달, as a faint polygon; only when every shown round has ≥ 5 active students in the class, so no single student's value can be read back. Never another student's point.
- `daybook export report <slug>`: one A4 page (print → PDF in the browser): current and longest streak in the term, submission rate on class days, attitude total, the trend (with the band), every teacher note, the number of class moments. Written to `reports/` (gitignored); display name only.
- `daybook note suggest <class> [--date]`: prints a ready `daybook note …` command per student without a note this week, e.g. "이번 주 수업일 2일 모두 제출, 태도 5/6, 확보 효율 상승"; the teacher edits and runs it. Nothing is written by itself.

## Deferred
- deferred: live webcam capture for 장면 올리기 on the board (a file input is used).
- deferred: `export pdf` stays a stub (exit 2); `daybook export report <slug>` writes print-ready HTML instead (print → PDF in the browser, no PDF library).
- deferred: several reward cycles per term (the class total counts the whole term).
- deferred: service worker / offline mode.
- deferred: a real-device install test on iPhone Safari and Android Chrome (manifest, icons, apple meta tags and standalone mode are in place; only Chromium was tested here).
- deferred: the Cloudflare deploy itself (needs the teacher's account: README "Teacher deploy steps").
- deferred: the `effort` table is created empty (P2 data source not defined yet).
- out of scope: login, database server, chat, push notifications, app store, analytics; nothing talks to Notion, Google or make.com.
- not in P0: retiring Google Forms / make.com (the teacher decides later).
